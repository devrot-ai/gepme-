"""TerraSense GeoAI — FastAPI application.

Endpoints:
  GET  /health         — readiness probe
  POST /v1/recommend   — natural-language outdoor activity recommendation
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from terrasense import MODEL_ID, MODEL_LICENSE, MODEL_PARAM_COUNT, SCORING_VERSION, __version__
from terrasense.config import settings
from terrasense.llm import MODEL
from terrasense.pipeline import recommend
from terrasense.schemas import HealthResponse, RecommendRequest, RecommendationResponse

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("terrasense")

# Concurrency semaphore
_semaphore: asyncio.Semaphore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the LLM once at startup (in a background thread to avoid blocking)."""
    global _semaphore
    _semaphore = asyncio.Semaphore(settings.max_concurrency)

    if settings.load_model:
        logger.info("Loading model %s in background thread...", settings.model_id)
        t = threading.Thread(target=MODEL.load, daemon=True)
        t.start()
    else:
        MODEL.error = "Model load skipped (TERRASENSE_LOAD_MODEL=false). Heuristic parser active."
        logger.info(MODEL.error)

    yield
    logger.info("Shutting down TerraSense GeoAI.")


app = FastAPI(
    title="TerraSense GeoAI",
    version=__version__,
    description=(
        "Geospatial AI outdoor activity assistant. "
        "Answers natural-language questions about when and where to go outside."
    ),
    lifespan=lifespan,
)

# CORS
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID"],
    max_age=3600,
)


@app.middleware("http")
async def add_request_id(request: Request, call_next) -> Response:
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Readiness probe. Reports model and provider status."""
    model_status = MODEL.status()
    providers: dict[str, str] = {
        "nominatim": settings.nominatim_url,
        "overpass": settings.overpass_url,
        "open_meteo_weather": settings.open_meteo_url,
        "open_meteo_aq": settings.open_meteo_aq_url,
        "osrm": settings.osrm_url,
    }
    status = "healthy"
    if not MODEL.ready and settings.load_model:
        status = "degraded"
    return HealthResponse(
        status=status,
        service="terrasense-geoai",
        version=__version__,
        scoring_version=SCORING_VERSION,
        model=model_status,
        providers={k: "configured" for k in providers},
        timestamp_utc=datetime.now(UTC).isoformat(),
    )


@app.post("/v1/recommend", response_model=RecommendationResponse, tags=["recommendation"])
async def post_recommend(body: RecommendRequest) -> RecommendationResponse | JSONResponse:
    """Natural-language outdoor activity recommendation.

    Accepts a question and optional structured hints, returns ranked
    place-and-time recommendations with evidence.
    """
    if _semaphore is not None:
        acquired = _semaphore._value > 0  # noqa: SLF001
        if not acquired:
            return JSONResponse(
                status_code=503,
                content={"error": "Server busy. Try again shortly.", "request_id": str(uuid.uuid4())},
            )

    try:
        async with _semaphore if _semaphore else asyncio.Semaphore(1):
            result = await asyncio.wait_for(
                recommend(body),
                timeout=settings.request_timeout_s,
            )
        return result
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"error": "Request timed out.", "request_id": str(uuid.uuid4())},
        )
    except ValidationError as exc:
        logger.exception("Validation error in recommend pipeline")
        return JSONResponse(
            status_code=422,
            content={"error": str(exc)[:500], "request_id": str(uuid.uuid4())},
        )
    except Exception as exc:
        logger.exception("Unexpected error in recommend pipeline")
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error.", "request_id": str(uuid.uuid4())},
        )


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "service": "terrasense-geoai",
        "version": __version__,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
