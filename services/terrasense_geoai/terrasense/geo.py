from __future__ import annotations

import math
from typing import Any

from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import transform

GEOD = Geod(ellps="WGS84")
EARTH_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (WGS84 sphere approximation)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(a)))


def geodesic_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    _, _, dist = GEOD.inv(lon1, lat1, lon2, lat2)
    return float(abs(dist))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Forward azimuth from point 1 to point 2, 0=N, clockwise, [0, 360)."""
    az, _, _ = GEOD.inv(lon1, lat1, lon2, lat2)
    return (az + 360.0) % 360.0


def downwind_direction(wind_from_deg: float) -> float:
    """Direction toward which the wind travels.

    Meteorological wind direction is the direction FROM which the wind blows.
    """
    return (float(wind_from_deg) + 180.0) % 360.0


def circular_delta_deg(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def is_roughly_downwind(
    industry_to_candidate_bearing: float,
    wind_from_deg: float,
    tolerance_deg: float = 35.0,
) -> bool:
    travel = downwind_direction(wind_from_deg)
    return circular_delta_deg(industry_to_candidate_bearing, travel) <= tolerance_deg


def distance_to_geometry_m(
    lat: float, lon: float, geom: Any
) -> float | None:
    """Distance from WGS84 point to a GeoJSON-like geometry, in metres.

    Uses geodesic length of a short inverse when the nearest point is a vertex;
    for polygons/lines projects locally via azimuthal equidistant around the point.
    """
    if geom is None:
        return None
    try:
        if isinstance(geom, dict):
            g = shape(geom)
        else:
            g = geom
        if g.is_empty:
            return None
        pt = Point(lon, lat)
        if g.contains(pt) or g.touches(pt):
            return 0.0
        # Local equidistant projection around the query point (metres).
        from pyproj import Transformer

        proj = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m"
        to_m = Transformer.from_crs("EPSG:4326", proj, always_xy=True).transform
        g_m = transform(to_m, g)
        p_m = transform(to_m, pt)
        return float(p_m.distance(g_m))
    except Exception:
        return None


def linestring_from_lonlat(coords: list[list[float]]) -> LineString | None:
    if len(coords) < 2:
        return None
    return LineString([(c[0], c[1]) for c in coords])


def polygon_from_lonlat(coords: list[list[float]]) -> Polygon | None:
    if len(coords) < 4:
        return None
    try:
        return Polygon([(c[0], c[1]) for c in coords])
    except Exception:
        return None
