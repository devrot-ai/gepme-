from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from terrasense.activities import ActivityProfile, tags_match_profile
from terrasense.config import settings
from terrasense.geo import geodesic_m
from terrasense.http_util import cached_get_json, cached_get_json_sync, overpass_post, overpass_post_sync
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


async def _nominatim_venues_fallback(lat: float, lon: float, profile: ActivityProfile, radius_m: float) -> list[OsmFeature]:
    """Fallback venue discovery using Nominatim POI search when Overpass is unreachable or returns 0 matches."""
    now = datetime.now(UTC).isoformat()
    d = max(0.015, min(0.12, radius_m / 111000.0 * 1.2))
    viewbox = f"{lon - d},{lat + d},{lon + d},{lat - d}"
    
    query_terms = [t for t in profile.venue_types if t] or ["park"]
    out: list[OsmFeature] = []
    seen: set[int] = set()

    for term in query_terms[:2]:
        url = f"{settings.nominatim_url}/search"
        data = await cached_get_json(
            url,
            params={
                "q": term,
                "format": "jsonv2",
                "limit": 8,
                "viewbox": viewbox,
                "bounded": 1,
            },
            cache_key=f"nom_poi:{lat:.3f}:{lon:.3f}:{term}",
        )
        if not data or not isinstance(data, list):
            continue
        for item in data:
            try:
                osm_id = int(item.get("osm_id", 0))
                if osm_id in seen:
                    continue
                seen.add(osm_id)
                vlat = float(item.get("lat", lat))
                vlon = float(item.get("lon", lon))
                dist = geodesic_m(lat, lon, vlat, vlon)
                name = item.get("name") or (item.get("display_name", "").split(",")[0] if item.get("display_name") else None)
                category = item.get("category") or "leisure"
                item_type = item.get("type") or "park"
                out.append(
                    OsmFeature(
                        osm_type=item.get("osm_type") or "node",
                        osm_id=osm_id,
                        name=name,
                        lat=vlat,
                        lon=vlon,
                        tags={category: item_type, "name": name or ""},
                        distance_m=dist,
                        retrieved_at_utc=now,
                    )
                )
            except Exception:
                continue
    return out


def _nominatim_venues_fallback_sync(lat: float, lon: float, profile: ActivityProfile, radius_m: float) -> list[OsmFeature]:
    now = datetime.now(UTC).isoformat()
    d = max(0.015, min(0.12, radius_m / 111000.0 * 1.2))
    viewbox = f"{lon - d},{lat + d},{lon + d},{lat - d}"
    query_terms = [t for t in profile.venue_types if t] or ["park"]
    out: list[OsmFeature] = []
    seen: set[int] = set()

    for term in query_terms[:2]:
        url = f"{settings.nominatim_url}/search"
        data = cached_get_json_sync(
            url,
            params={"q": term, "format": "jsonv2", "limit": 8, "viewbox": viewbox, "bounded": 1},
            cache_key=f"nom_poi:{lat:.3f}:{lon:.3f}:{term}",
        )
        if not data or not isinstance(data, list):
            continue
        for item in data:
            try:
                osm_id = int(item.get("osm_id", 0))
                if osm_id in seen:
                    continue
                seen.add(osm_id)
                vlat = float(item.get("lat", lat))
                vlon = float(item.get("lon", lon))
                dist = geodesic_m(lat, lon, vlat, vlon)
                name = item.get("name") or (item.get("display_name", "").split(",")[0] if item.get("display_name") else None)
                category = item.get("category") or "leisure"
                item_type = item.get("type") or "park"
                out.append(
                    OsmFeature(
                        osm_type=item.get("osm_type") or "node",
                        osm_id=osm_id,
                        name=name,
                        lat=vlat,
                        lon=vlon,
                        tags={category: item_type, "name": name or ""},
                        distance_m=dist,
                        retrieved_at_utc=now,
                    )
                )
            except Exception:
                continue
    return out


async def _overpass(query: str) -> dict | None:
    return await overpass_post(query, cache_key=f"overpass:{hash(query)}")


def _overpass_sync(query: str) -> dict | None:
    return overpass_post_sync(query, cache_key=f"overpass:{hash(query)}")


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
    unique: list[OsmFeature] = []
    for _, feat in eligible:
        if any(geodesic_m(feat.lat, feat.lon, u.lat, u.lon) < 40 for u in unique):
            continue
        unique.append(feat)
    return unique


async def discover(lat: float, lon: float, radius_m: float, profile: ActivityProfile) -> tuple[list[OsmFeature], list[OsmFeature], list[str]]:
    now = datetime.now(UTC).isoformat()
    notes: list[str] = []
    vq = VENUE_QUERY.format(radius=int(radius_m), lat=lat, lon=lon)
    cq = CONTEXT_QUERY.format(radius=int(min(radius_m + 1500, 25000)), lat=lat, lon=lon)
    
    # Run Overpass queries concurrently
    raw_v, raw_c = await asyncio.gather(_overpass(vq), _overpass(cq), return_exceptions=True)
    if isinstance(raw_v, Exception) or raw_v is None:
        notes.append("Overpass venue query was unavailable; engaged Nominatim POI discovery fallback.")
        venues = await _nominatim_venues_fallback(lat, lon, profile, radius_m)
    else:
        venues = filter_venues(_elements_to_features(raw_v.get("elements") or [], (lat, lon)), profile, radius_m)
        if not venues:
            notes.append("No mapped OSM venues matched Overpass filters; searching Nominatim POIs.")
            venues = await _nominatim_venues_fallback(lat, lon, profile, radius_m)

    if not venues:
        notes.append(
            f"Using queried location as the target activity zone (no separate tagged venues within {radius_m/1000:.1f} km)."
        )
        venues = [
            OsmFeature(
                osm_type="node",
                osm_id=0,
                name="Queried Location Area",
                lat=lat,
                lon=lon,
                tags={"leisure": "park", "name": "Queried Location Area"},
                distance_m=0.0,
                retrieved_at_utc=now,
            )
        ]

    if isinstance(raw_c, Exception) or raw_c is None:
        notes.append("Overpass context query unavailable; using default proximity buffers.")
        context: list[OsmFeature] = []
    else:
        context = _elements_to_features(raw_c.get("elements") or [], (lat, lon))

    return venues[: settings.max_candidates], context, notes


def discover_sync(lat: float, lon: float, radius_m: float, profile: ActivityProfile) -> tuple[list[OsmFeature], list[OsmFeature], list[str]]:
    now = datetime.now(UTC).isoformat()
    notes: list[str] = []
    vq = VENUE_QUERY.format(radius=int(radius_m), lat=lat, lon=lon)
    cq = CONTEXT_QUERY.format(radius=int(min(radius_m + 1500, 25000)), lat=lat, lon=lon)
    raw_v = _overpass_sync(vq)
    raw_c = _overpass_sync(cq)
    venues = filter_venues(_elements_to_features((raw_v or {}).get("elements") or [], (lat, lon)), profile, radius_m) if raw_v else []
    if not venues:
        venues = _nominatim_venues_fallback_sync(lat, lon, profile, radius_m)
    if not venues:
        venues = [
            OsmFeature(
                osm_type="node",
                osm_id=0,
                name="Queried Location Area",
                lat=lat,
                lon=lon,
                tags={"leisure": "park", "name": "Queried Location Area"},
                distance_m=0.0,
                retrieved_at_utc=now,
            )
        ]
    context = _elements_to_features((raw_c or {}).get("elements") or [], (lat, lon)) if raw_c else []
    return venues[: settings.max_candidates], context, notes
