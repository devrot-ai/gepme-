from __future__ import annotations

import asyncio
from typing import Any

import httpx

from terrasense.cache import http_cache
from terrasense.config import settings

_client: httpx.AsyncClient | None = None
_sync_client: httpx.Client | None = None


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
