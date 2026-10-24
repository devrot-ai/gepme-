from __future__ import annotations

from terrasense.geo import bearing_deg, geodesic_m, is_roughly_downwind
from terrasense.schemas import OsmFeature, SpatialContext


def _nearest(
    origin_lat: float,
    origin_lon: float,
    features: list[OsmFeature],
    predicate,
) -> OsmFeature | None:
    best: OsmFeature | None = None
    best_d = 1e18
    for f in features:
        if not predicate(f.tags):
            continue
        d = geodesic_m(origin_lat, origin_lon, f.lat, f.lon)
        if d < best_d:
            best, best_d = f, d
    return best


def analyze_site(
    lat: float,
    lon: float,
    context: list[OsmFeature],
    wind_from_deg: float | None,
    route_distance_m: float | None,
    route_duration_s: float | None,
) -> SpatialContext:
    notes = [
        "Distances use WGS84 geodesics to OSM feature centroids, not cadastral boundaries.",
        "Missing OSM tags do not prove a hazard or amenity is absent.",
        "Green cover is mapped land use, not canopy percentage.",
        "Live traffic is not integrated; OSRM provides free-flow route estimates only.",
    ]

    def is_major_road(tags: dict[str, str]) -> bool:
        return tags.get("highway") in {"motorway", "trunk", "primary", "secondary"}

    def is_cycle(tags: dict[str, str]) -> bool:
        return tags.get("highway") == "cycleway" or "cycleway" in tags

    def is_path(tags: dict[str, str]) -> bool:
        return tags.get("highway") in {"path", "footway", "pedestrian", "track"}

    def is_industry(tags: dict[str, str]) -> bool:
        if tags.get("landuse") == "industrial":
            return True
        if tags.get("industrial"):
            return True
        return tags.get("man_made") in {"works", "wastewater_plant", "chimney"}

    def is_water(tags: dict[str, str]) -> bool:
        if tags.get("natural") in {"water", "coastline", "bay", "beach"}:
            return True
        return tags.get("waterway") in {"river", "canal", "stream"}

    def is_green(tags: dict[str, str]) -> bool:
        if tags.get("leisure") in {"park", "garden", "nature_reserve", "recreation_ground"}:
            return True
        if tags.get("natural") in {"wood", "scrub", "grassland"}:
            return True
        return tags.get("landuse") in {"forest", "meadow", "grass", "recreation_ground"}

    road = _nearest(lat, lon, context, is_major_road)
    cycle = _nearest(lat, lon, context, is_cycle)
    path = _nearest(lat, lon, context, is_path)
    industry = _nearest(lat, lon, context, is_industry)
    water = _nearest(lat, lon, context, is_water)
    green = _nearest(lat, lon, context, is_green)

    industry_bearing = None
    downwind = None
    wind_note = "Wind–industry relationship not computed (missing wind direction or no mapped industrial feature)."
    if industry is not None:
        industry_bearing = bearing_deg(industry.lat, industry.lon, lat, lon)
        if wind_from_deg is None:
            wind_note = (
                f"Mapped industrial feature {industry.name or 'unnamed'} "
                f"is ~{geodesic_m(lat, lon, industry.lat, industry.lon):.0f} m away; "
                "wind direction missing so downwind status is unknown."
            )
        else:
            downwind = is_roughly_downwind(industry_bearing, wind_from_deg)
            wind_note = (
                "Heuristic only: meteorological wind is the direction FROM which wind blows; "
                f"downwind travel = (wind_from + 180) % 360. Candidate bearing from industry "
                f"is {industry_bearing:.0f}°. "
                + (
                    "Candidate is approximately downwind of the industrial feature."
                    if downwind
                    else "Candidate is not approximately downwind of the industrial feature."
                )
                + " This is not a measured pollution concentration."
            )

    def dist(f: OsmFeature | None) -> float | None:
        if f is None:
            return None
        return geodesic_m(lat, lon, f.lat, f.lon)

    return SpatialContext(
        nearest_major_road_m=dist(road),
        nearest_cycleway_m=dist(cycle),
        nearest_path_m=dist(path),
        nearest_industry_m=dist(industry),
        nearest_industry_name=industry.name if industry else None,
        industry_bearing_deg=industry_bearing,
        downwind_of_industry=downwind,
        wind_relationship_note=wind_note,
        nearest_water_m=dist(water),
        nearest_water_name=water.name if water else None,
        nearest_green_m=dist(green),
        nearest_green_name=green.name if green else None,
        route_distance_m=route_distance_m,
        route_duration_s=route_duration_s,
        live_traffic_available=False,
        completeness_notes=notes,
    )
