"""FastAPI endpoint integration tests for TerraSense GeoAI."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app import app


@pytest.mark.asyncio
async def test_root_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "terrasense-geoai"
        assert "version" in data


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "terrasense-geoai"
        assert "status" in data
        assert "scoring_version" in data
        assert "model" in data
        assert "providers" in data


@pytest.mark.asyncio
async def test_recommend_validation_error():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Question too short (< 3 chars)
        resp = await client.post("/v1/recommend", json={"question": "a"})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_recommend_mocked(monkeypatch):
    """Test /v1/recommend with mocked pipeline components."""
    from terrasense.schemas import (
        ActivityId,
        ParsedIntent,
        RecommendationResponse,
    )

    async def fake_recommend(req):
        return RecommendationResponse(
            request_id="test-123",
            question=req.question,
            intent=ParsedIntent(activity=ActivityId.running),
            recommendation="go",
            summary="Great conditions for running near Powai.",
            candidates=[],
            avoid_reasons=[],
            limitations=[],
            sources=[],
            model={"id": "test-model"},
            generated_at_utc="2026-10-24T00:00:00Z",
        )

    import app as app_module
    monkeypatch.setattr(app_module, "recommend", fake_recommend)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/recommend",
            json={"question": "Where to run tomorrow morning near Powai?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["recommendation"] == "go"
        assert data["intent"]["activity"] == "running"
        assert "Great conditions" in data["summary"]
