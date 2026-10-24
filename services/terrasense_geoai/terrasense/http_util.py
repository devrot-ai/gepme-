from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from terrasense.cache import http_cache
from terrasense.config import settings

logger = logging.getLogger("terrasense")

_client: httpx.AsyncClient | None = None
_sync_client: httpx.Client | None = None

# Overpass API mirrors — fallback list for resilience
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def user_headers() -> dict[str, str]:
    return {"User-Agent": settings.user_agent, "Accept": "application/json"}


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=settings.http_timeout_s, headers=user_headers())
    return _client


def get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None or _sync_client.is_closed:
        _sync_client = httpx.Client(timeout=settings.http_timeout_s, headers=user_headers())
    return _sync_client


async def overpass_post(query: str, cache_key: str | None = None) -> dict[str, Any] | None:
    """Send an Overpass QL query using form-encoded POST, with mirror fallback."""
    key = cache_key or f"overpass:{hash(query)}"
    hit = http_cache.get(key)
    if hit is not None:
        return hit

    mirrors = [settings.overpass_url] + [m for m in OVERPASS_MIRRORS if m != settings.overpass_url]
    for mirror in mirrors:
        try:
            # Use a fresh client per attempt with a short per-mirror timeout
            async with httpx.AsyncClient(timeout=4.0, headers=user_headers()) as client:
                resp = await client.post(mirror, data={"data": query})
            if resp.status_code == 429:
                logger.warning("Overpass 429 from %s, trying next mirror", mirror)
                await asyncio.sleep(1.0)
                continue
            if resp.status_code >= 400:
                logger.warning("Overpass %d from %s, trying next mirror", resp.status_code, mirror)
                continue
            resp.raise_for_status()
            payload = resp.json()
            http_cache.set(key, payload)
            logger.info("Overpass OK from %s (%d elements)", mirror, len(payload.get("elements", [])))
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Overpass error from %s: %s", mirror, type(exc).__name__)
            continue
    return None


def overpass_post_sync(query: str, cache_key: str | None = None) -> dict[str, Any] | None:
    """Synchronous version of overpass_post with mirror fallback."""
    key = cache_key or f"overpass:{hash(query)}"
    hit = http_cache.get(key)
    if hit is not None:
        return hit
    client = get_sync_client()

    mirrors = [settings.overpass_url] + [m for m in OVERPASS_MIRRORS if m != settings.overpass_url]
    for mirror in mirrors:
        try:
            resp = client.post(
                mirror,
                data={"data": query},
                headers={**user_headers(), "Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning("Overpass %d from %s, trying next mirror", resp.status_code, mirror)
                continue
            resp.raise_for_status()
            payload = resp.json()
            http_cache.set(key, payload)
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Overpass error from %s: %s", mirror, exc)
            continue
    return None


async def cached_get_json(
    url: str,
    params: dict[str, Any] | None = None,
    cache_key: str | None = None,
    *,
    method: str = "GET",
    data: str | None = None,
) -> dict[str, Any] | None:
    key = cache_key or f"{method}:{url}:{params}:{data}"
    hit = http_cache.get(key)
    if hit is not None:
        return hit
    client = await get_client()
    try:
        if method == "POST":
            resp = await client.post(url, content=data, headers={**user_headers(), "Content-Type": "text/plain"})
        else:
            resp = await client.get(url, params=params)
        if resp.status_code == 429:
            await asyncio.sleep(1.5)
            return None
        resp.raise_for_status()
        payload = resp.json()
        http_cache.set(key, payload)
        return payload
    except (httpx.HTTPError, ValueError):
        return None


def cached_get_json_sync(
    url: str,
    params: dict[str, Any] | None = None,
    cache_key: str | None = None,
    *,
    method: str = "GET",
    data: str | None = None,
) -> dict[str, Any] | None:
    key = cache_key or f"{method}:{url}:{params}:{data}"
    hit = http_cache.get(key)
    if hit is not None:
        return hit
    client = get_sync_client()
    try:
        if method == "POST":
            resp = client.post(url, content=data, headers={**user_headers(), "Content-Type": "text/plain"})
        else:
            resp = client.get(url, params=params)
        if resp.status_code == 429:
            return None
        resp.raise_for_status()
        payload = resp.json()
        http_cache.set(key, payload)
        return payload
    except (httpx.HTTPError, ValueError):
        return None
