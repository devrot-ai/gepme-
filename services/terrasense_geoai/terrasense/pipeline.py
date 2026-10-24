"""TerraSense GeoAI — end-to-end recommendation pipeline.

Orchestrates: intent → geocode → venue discovery → weather/AQ → spatial
analysis → ranking → narration → structured response.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from terrasense import MODEL_ID, MODEL_LICENSE, MODEL_PARAM_COUNT, SCORING_VERSION, __version__
from terrasense.activities import get_profile
from terrasense.config import settings
from terrasense.geocoder import geocode, reverse_label
from terrasense.intent_parser import parse_intent
from terrasense.llm import MODEL
from terrasense.narrator import _avoid_items, _carry_items, _monitor_items, narrate
from terrasense.osm_provider import discover
from terrasense.ranker import pick_top, rank_combo
from terrasense.routing import route_estimate
from terrasense.schemas import (
    ActivityId,
    GeoPoint,
    HourlyConditions,
    ParsedIntent,
    Provenance,
    RankedCandidate,
    RecommendRequest,
    RecommendationResponse,
    ScoreBreakdown,
)
from terrasense.spatial import analyze_site
from terrasense.time_window import LocalWindow, resolve_window, select_hours
from terrasense.weather import fetch_forecast

logger = logging.getLogger(__name__)


async def _route_for_venue(origin_lat: float, origin_lon: float, v_lat: float, v_lon: float):
    """Fetch OSRM route; returns (dist_m, dur_s, note)."""
    try:
        return await route_estimate(origin_lat, origin_lon, v_lat, v_lon, profile="foot")
    except Exception:
        return None, None, "Routing failed."


async def recommend(request: RecommendRequest) -> RecommendationResponse:
    """Full recommendation pipeline."""
    request_id = str(uuid.uuid4())
    generated_at = datetime.now(UTC).isoformat()
    all_sources: list[Provenance] = []
    all_limitations: list[str] = []

    # 1. Parse intent
    intent = parse_intent(request.question, request)

    if intent.unsupported:
        return RecommendationResponse(
            request_id=request_id,
            question=request.question,
            intent=intent,
            recommendation="unsupported",
            summary="This doesn't appear to be about an outdoor activity I can help with.",
            limitations=["The question was classified as unsupported."],
            model=_model_info(),
            generated_at_utc=generated_at,
        )

    # Default activity if still unclear
    if intent.activity is None:
        intent.activity = ActivityId.hiking
        all_limitations.append("Activity not clearly identified; defaulted to hiking.")

    profile = get_profile(intent.activity)

    # 2. Resolve location
    origin_lat = intent.latitude
    origin_lon = intent.longitude

    if origin_lat is None and intent.place_name:
        geo_result = await geocode(intent.place_name)
        if geo_result:
            origin_lat = geo_result.lat
            origin_lon = geo_result.lon
            intent.latitude = origin_lat
            intent.longitude = origin_lon
            all_sources.append(
                Provenance(
                    source=f"Nominatim geocoding ({geo_result.source})",
                    fetched_at_utc=geo_result.fetched_at_utc,
                    notes=[f"Resolved '{intent.place_name}' → {geo_result.display_name}"],
                )
            )
        else:
            all_limitations.append(f"Geocoding failed for '{intent.place_name}'.")

    if origin_lat is None:
        return RecommendationResponse(
            request_id=request_id,
            question=request.question,
            intent=intent,
            recommendation="clarify",
            summary="I need a location to search for venues. Please specify a place name or coordinates.",
            limitations=all_limitations,
            model=_model_info(),
            generated_at_utc=generated_at,
        )

    # Get display label for origin
    origin_label = intent.place_name
    if not origin_label:
        origin_label = await reverse_label(origin_lat, origin_lon)

    # 3. Discover venues via Overpass
    radius_m = intent.radius_km * 1000
    venues, context, osm_notes = await discover(origin_lat, origin_lon, radius_m, profile)
    all_limitations.extend(osm_notes)
    all_sources.append(
        Provenance(
            source="OpenStreetMap Overpass API",
            fetched_at_utc=datetime.now(UTC).isoformat(),
            notes=[f"Venues found: {len(venues)}, context features: {len(context)}"],
        )
    )

    if not venues:
        return RecommendationResponse(
            request_id=request_id,
            question=request.question,
            intent=intent,
            origin=GeoPoint(lat=origin_lat, lon=origin_lon),
            origin_label=origin_label,
            recommendation="no_venues",
            summary=f"No mapped venues for {profile.display_name} were found within {intent.radius_km} km.",
            limitations=all_limitations,
            sources=all_sources,
            model=_model_info(),
            generated_at_utc=generated_at,
        )

    # 4. Fetch weather + AQ
    hours, tz, weather_prov, weather_notes = await fetch_forecast(origin_lat, origin_lon)
    all_sources.extend(weather_prov)
    all_limitations.extend(weather_notes)

    if not hours:
        all_limitations.append("Weather data unavailable; cannot rank candidates reliably.")
        return RecommendationResponse(
            request_id=request_id,
            question=request.question,
            intent=intent,
            origin=GeoPoint(lat=origin_lat, lon=origin_lon),
            origin_label=origin_label,
            timezone=tz,
            recommendation="avoid",
            summary="Weather data is unavailable. Cannot produce a reliable recommendation.",
            avoid_reasons=["Weather provider failed."],
            limitations=all_limitations,
            sources=all_sources,
            model=_model_info(),
            generated_at_utc=generated_at,
        )

    # 5. Resolve time window
    window = resolve_window(intent, tz)
    hour_times = [h.time_local for h in hours]
    selected_times = select_hours(hour_times, window, tz, settings.max_hours)
    if not selected_times:
        # Fallback: take the next few hours
        all_limitations.append(
            f"No forecast hours match the requested window ({window.start} to {window.end}). Using next available hours."
        )
        selected_times = hour_times[:settings.max_hours]

    hours_by_time = {h.time_local: h for h in hours}
    selected_hours = [hours_by_time[t] for t in selected_times if t in hours_by_time]

    # 6. Score venue × hour combos
    combos: list[tuple[ScoreBreakdown, dict[str, Any]]] = []

    # Get routes for each venue in parallel
    route_tasks = [
        _route_for_venue(origin_lat, origin_lon, v.lat, v.lon)
        for v in venues[:settings.max_candidates]
    ]
    route_results = await asyncio.gather(*route_tasks)

    for v_idx, venue in enumerate(venues[:settings.max_candidates]):
        route_dist, route_dur, route_note = route_results[v_idx]

        for hour in selected_hours:
            wind_from = hour.wind_direction_deg
            spatial = analyze_site(
                venue.lat, venue.lon, context, wind_from,
                route_distance_m=route_dist,
                route_duration_s=route_dur,
            )
            from terrasense.activities import tags_match_profile
            _, specificity = tags_match_profile(venue.tags, profile)

            from terrasense.geo import geodesic_m
            origin_dist = geodesic_m(origin_lat, origin_lon, venue.lat, venue.lon)

            breakdown = rank_combo(
                profile=profile,
                hour=hour,
                spatial=spatial,
                match_specificity=specificity,
                constraints=intent.constraints,
                origin_distance_m=origin_dist,
            )
            combos.append((breakdown, {
                "venue": venue,
                "hour": hour,
                "spatial": spatial,
                "specificity": specificity,
            }))

    # 7. Pick top K
    top = pick_top(combos, k=settings.top_k)

    # Build candidates
    candidates: list[RankedCandidate] = []
    for rank, (score, meta) in enumerate(top, 1):
        venue = meta["venue"]
        hour: HourlyConditions = meta["hour"]
        spatial = meta["spatial"]
        candidates.append(RankedCandidate(
            rank=rank,
            place_name=venue.name or f"OSM {venue.osm_type}/{venue.osm_id}",
            coordinates=GeoPoint(lat=venue.lat, lon=venue.lon),
            osm=venue,
            local_time=hour.time_local,
            timezone=tz,
            score=score,
            weather=hour,
            spatial=spatial,
            carry=_carry_items(intent.activity, intent.constraints),
            monitor=_monitor_items(intent.activity),
            avoid_if=_avoid_items(intent.activity),
            evidence_ids=[
                f"osm:{venue.osm_type}/{venue.osm_id}",
                f"weather:{hour.time_utc}",
                f"aq:{hour.time_utc}",
            ],
        ))

    # All gated?
    all_gated = all(s.hard_gated for s, _ in combos) if combos else True
    avoid_reasons: list[str] = []
    if all_gated:
        for s, _ in combos:
            avoid_reasons.extend(s.gate_reasons)
        avoid_reasons = list(dict.fromkeys(avoid_reasons))[:5]

    # Determine recommendation level
    if not candidates and all_gated:
        recommendation = "avoid"
    elif not candidates:
        recommendation = "no_venues"
    elif candidates[0].score.total >= 60:
        recommendation = "go"
    elif candidates[0].score.total >= 35:
        recommendation = "caution"
    else:
        recommendation = "avoid"

    # 8. Narrate
    summary = narrate(intent, candidates, recommendation, all_limitations)

    return RecommendationResponse(
        request_id=request_id,
        question=request.question,
        intent=intent,
        origin=GeoPoint(lat=origin_lat, lon=origin_lon),
        origin_label=origin_label,
        timezone=tz,
        recommendation=recommendation,
        summary=summary,
        candidates=candidates,
        avoid_reasons=avoid_reasons,
        limitations=all_limitations,
        sources=all_sources,
        model=_model_info(),
        generated_at_utc=generated_at,
    )


def _model_info() -> dict[str, Any]:
    return {
        "id": MODEL_ID,
        "parameter_count": MODEL_PARAM_COUNT,
        "licence": MODEL_LICENSE,
        "scoring_version": SCORING_VERSION,
        "llm_ready": MODEL.ready,
        "llm_error": MODEL.error,
    }
