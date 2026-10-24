from __future__ import annotations

from terrasense.config import settings
from terrasense.http_util import cached_get_json, cached_get_json_sync


async def route_estimate(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float, profile: str = "foot"
) -> tuple[float | None, float | None, str]:
    """Return (distance_m, duration_s, note). Free-flow OSRM, not live traffic."""
    url = f"{settings.osrm_url.rstrip('/')}/route/v1/{profile}/{from_lon},{from_lat};{to_lon},{to_lat}"
    params = {"overview": "false", "alternatives": "false"}
    data = await cached_get_json(url, params=params, cache_key=f"osrm:{profile}:{from_lat:.4f}:{from_lon:.4f}:{to_lat:.4f}:{to_lon:.4f}")
    return _parse(data)


def route_estimate_sync(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float, profile: str = "foot"
) -> tuple[float | None, float | None, str]:
    url = f"{settings.osrm_url.rstrip('/')}/route/v1/{profile}/{from_lon},{from_lat};{to_lon},{to_lat}"
    params = {"overview": "false", "alternatives": "false"}
    data = cached_get_json_sync(url, params=params, cache_key=f"osrm:{profile}:{from_lat:.4f}:{from_lon:.4f}:{to_lat:.4f}:{to_lon:.4f}")
    return _parse(data)


def _parse(data: dict | None) -> tuple[float | None, float | None, str]:
    note = "OSRM free-flow estimate only; not live traffic."
    if not data or data.get("code") != "Ok":
        return None, None, "Routing unavailable. " + note
    routes = data.get("routes") or []
    if not routes:
        return None, None, "No route found. " + note
    r0 = routes[0]
    return float(r0.get("distance")), float(r0.get("duration")), note
