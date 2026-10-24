from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from terrasense.activities import ActivityProfile, tags_match_profile
from terrasense.config import settings
from terrasense.geo import geodesic_m
from terrasense.http_util import cached_get_json, cached_get_json_sync
from terrasense.schemas import OsmFeature

CONTEXT_QUERY = """
[out:json][timeout:25];
(
  way["highway"~"motorway|trunk|primary|secondary"](around:{radius},{lat},{lon});
  way["highway"~"cycleway|path|footway|pedestrian"](around:{radius},{lat},{lon});
  nwr["landuse"~"industrial"](around:{radius},{lat},{lon});
  nwr["man_made"~"works|wastewater_plant|chimney"](around:{radius},{lat},{lon});
  nwr["industrial"](around:{radius},{lat},{lon});
  nwr["natural"~"water|coastline|bay|beach"](around:{radius},{lat},{lon});
  way["waterway"~"river|canal|stream"](around:{radius},{lat},{lon});
  nwr["landuse"~"forest|meadow|grass|recreation_ground"](around:{radius},{lat},{lon});
  nwr["natural"~"wood|scrub|grassland"](around:{radius},{lat},{lon});
  nwr["leisure"~"park|garden|nature_reserve"](around:{radius},{lat},{lon});
);
out center tags 80;
"""

VENUE_QUERY = """
[out:json][timeout:25];
(
  nwr["leisure"~"park|pitch|golf_course|garden|nature_reserve|playground|recreation_ground|sports_centre|track"](around:{radius},{lat},{lon});
  nwr["sport"~"running|soccer|cricket|tennis|cycling|golf"](around:{radius},{lat},{lon});
  nwr["highway"~"path|cycleway|footway|track|pedestrian"](around:{radius},{lat},{lon});
  nwr["tourism"~"picnic_site|viewpoint|attraction"](around:{radius},{lat},{lon});
  nwr["natural"~"wood|beach|water"](around:{radius},{lat},{lon});
  nwr["landuse"="forest"](around:{radius},{lat},{lon});
  nwr["historic"~"monument|memorial"](around:{radius},{lat},{lon});
  nwr["place"="square"](around:{radius},{lat},{lon});
  nwr["route"="bicycle"](around:{radius},{lat},{lon});
  nwr["amenity"="bicycle_rental"](around:{radius},{lat},{lon});
);
out center tags 60;
"""


def _elements_to_features(elements: list[dict[str, Any]], origin: tuple[float, float]) -> list[OsmFeature]:
    now = datetime.now(UTC).isoformat()
    out: list[OsmFeature] = []
    olat, olon = origin
    for el in elements:
        osm_type = el.get("type") or "node"
        osm_id = el.get("id")
        if osm_id is None:
            continue
        if osm_type == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        lat_f, lon_f = float(lat), float(lon)
        tags = {str(k): str(v) for k, v in (el.get("tags") or {}).items()}
        name = tags.get("name") or tags.get("name:en")
        out.append(
            OsmFeature(
                osm_type=osm_type,
                osm_id=int(osm_id),
                name=name,
                lat=lat_f,
                lon=lon_f,
                tags=tags,
                distance_m=geodesic_m(olat, olon, lat_f, lon_f),
                retrieved_at_utc=now,
            )
        )
    return out


async def _overpass(query: str) -> dict | None:
    return await cached_get_json(
        settings.overpass_url,
        method="POST",
        data=query,
        cache_key=f"overpass:{hash(query)}",
    )


def _overpass_sync(query: str) -> dict | None:
    return cached_get_json_sync(
        settings.overpass_url,
        method="POST",
        data=query,
        cache_key=f"overpass:{hash(query)}",
    )


def filter_venues(features: list[OsmFeature], profile: ActivityProfile, radius_m: float) -> list[OsmFeature]:
    eligible: list[tuple[int, OsmFeature]] = []
    seen: set[tuple[str, int]] = set()
    for feat in features:
        if feat.distance_m is not None and feat.distance_m > radius_m:
            continue
        ok, spec = tags_match_profile(feat.tags, profile)
        if not ok:
            continue
        key = (feat.osm_type, feat.osm_id)
        if key in seen:
            continue
        seen.add(key)
        # Generic pitches without matching sport are weaker for tennis/cricket/football
        sport = (feat.tags.get("sport") or "").lower()
        if profile.id.value in {"tennis", "cricket", "football", "golf"}:
            wanted = {
                "tennis": "tennis",
                "cricket": "cricket",
                "football": "soccer",
                "golf": "golf",
            }[profile.id.value]
            if feat.tags.get("leisure") == "pitch" and wanted not in sport and profile.id.value != "golf":
                spec = max(1, spec - 1)
            if profile.id.value == "golf" and "golf" not in sport and feat.tags.get("leisure") != "golf_course":
                continue
        eligible.append((spec, feat))
    eligible.sort(key=lambda x: (-x[0], x[1].distance_m or 1e9))
    # Deduplicate near-identical coordinates
    unique: list[OsmFeature] = []
    for _, feat in eligible:
        if any(geodesic_m(feat.lat, feat.lon, u.lat, u.lon) < 40 for u in unique):
            continue
        unique.append(feat)
    return unique


async def discover(lat: float, lon: float, radius_m: float, profile: ActivityProfile) -> tuple[list[OsmFeature], list[OsmFeature], list[str]]:
    notes: list[str] = []
    vq = VENUE_QUERY.format(radius=int(radius_m), lat=lat, lon=lon)
    cq = CONTEXT_QUERY.format(radius=int(min(radius_m + 1500, 25000)), lat=lat, lon=lon)
    raw_v = await _overpass(vq)
    raw_c = await _overpass(cq)
    if raw_v is None:
        notes.append("Overpass venue query failed or timed out.")
        venues: list[OsmFeature] = []
    else:
        venues = filter_venues(_elements_to_features(raw_v.get("elements") or [], (lat, lon)), profile, radius_m)
        if not venues:
            notes.append(
                "No mapped OSM features matched this activity inside the radius. "
                "This is a map-coverage limitation, not proof that no venue exists."
            )
    if raw_c is None:
        notes.append("Overpass context query failed; roads/industry/water/green distances unavailable.")
        context: list[OsmFeature] = []
    else:
        context = _elements_to_features(raw_c.get("elements") or [], (lat, lon))
    return venues[: settings.max_candidates], context, notes


def discover_sync(lat: float, lon: float, radius_m: float, profile: ActivityProfile) -> tuple[list[OsmFeature], list[OsmFeature], list[str]]:
    notes: list[str] = []
    vq = VENUE_QUERY.format(radius=int(radius_m), lat=lat, lon=lon)
    cq = CONTEXT_QUERY.format(radius=int(min(radius_m + 1500, 25000)), lat=lat, lon=lon)
    raw_v = _overpass_sync(vq)
    raw_c = _overpass_sync(cq)
    venues = filter_venues(_elements_to_features((raw_v or {}).get("elements") or [], (lat, lon)), profile, radius_m) if raw_v else []
    context = _elements_to_features((raw_c or {}).get("elements") or [], (lat, lon)) if raw_c else []
    if not raw_v:
        notes.append("Overpass venue query failed or timed out.")
    if not venues:
        notes.append("No mapped OSM features matched this activity inside the radius.")
    if not raw_c:
        notes.append("Overpass context query failed.")
    return venues[: settings.max_candidates], context, notes
