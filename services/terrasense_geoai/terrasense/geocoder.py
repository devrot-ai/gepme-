from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from terrasense.config import settings
from terrasense.http_util import cached_get_json, cached_get_json_sync


@dataclass
class GeocodeResult:
    display_name: str
    lat: float
    lon: float
    osm_type: str | None
    osm_id: int | None
    country_code: str | None
    fetched_at_utc: str
    source: str = "Nominatim"


async def geocode(query: str) -> GeocodeResult | None:
    if not query.strip():
        return None
    url = f"{settings.nominatim_url.rstrip('/')}/search"
    params = {"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    data = await cached_get_json(url, params=params, cache_key=f"nom:{query.lower()}")
    return _parse(data)


def geocode_sync(query: str) -> GeocodeResult | None:
    url = f"{settings.nominatim_url.rstrip('/')}/search"
    params = {"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    data = cached_get_json_sync(url, params=params, cache_key=f"nom:{query.lower()}")
    return _parse(data)


def _parse(data: dict | list | None) -> GeocodeResult | None:
    if not data:
        return None
    row = data[0] if isinstance(data, list) else data
    if not row:
        return None
    try:
        lat = float(row["lat"])
        lon = float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    addr = row.get("address") or {}
    osm_id = row.get("osm_id")
    return GeocodeResult(
        display_name=row.get("display_name") or query_fallback(row),
        lat=lat,
        lon=lon,
        osm_type=row.get("osm_type"),
        osm_id=int(osm_id) if osm_id is not None else None,
        country_code=(addr.get("country_code") or "").lower() or None,
        fetched_at_utc=datetime.now(UTC).isoformat(),
    )


def query_fallback(row: dict) -> str:
    return row.get("name") or "Unnamed location"


async def reverse_label(lat: float, lon: float) -> str | None:
    url = f"{settings.nominatim_url.rstrip('/')}/reverse"
    params = {"lat": lat, "lon": lon, "format": "jsonv2"}
    data = await cached_get_json(url, params=params, cache_key=f"rev:{lat:.4f},{lon:.4f}")
    if not data:
        return None
    return data.get("display_name")
