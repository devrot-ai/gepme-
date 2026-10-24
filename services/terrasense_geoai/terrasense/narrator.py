"""Evidence-grounded response generation.

Generates human-readable recommendation text from ranked candidates,
weather evidence, geospatial analysis, and score breakdowns.

The LLM is optional. When unavailable, a deterministic template narrator
produces the explanation. The ranking engine — not the LLM — decides
which candidate wins.
"""
from __future__ import annotations

import logging
from typing import Any

from terrasense.activities import PREP_TEXT, get_profile
from terrasense.llm import MODEL, extract_json_object
from terrasense.schemas import (
    ActivityId,
    ParsedIntent,
    PersonalConstraint,
    RankedCandidate,
    RecommendationResponse,
)

logger = logging.getLogger(__name__)


def _carry_items(activity: ActivityId, constraints: list[PersonalConstraint]) -> list[str]:
    profile = get_profile(activity)
    items = [PREP_TEXT.get(c, c) for c in profile.prep_categories if c in PREP_TEXT]
    if PersonalConstraint.asthma in constraints:
        items.append("Carry a PM2.5 mask. Check AQI on arrival.")
    if PersonalConstraint.dog in constraints:
        items.append("Bring water for your dog and avoid hot pavement.")
    if PersonalConstraint.children in constraints:
        items.append("Pack extra water and sun protection for children.")
    return items


def _monitor_items(activity: ActivityId) -> list[str]:
    profile = get_profile(activity)
    return list(profile.monitor)


def _avoid_items(activity: ActivityId) -> list[str]:
    profile = get_profile(activity)
    return list(profile.avoid_when)


def _evidence_block(candidate: RankedCandidate) -> str:
    """Build a concise evidence summary for the LLM prompt."""
    lines = [
        f"Place: {candidate.place_name} ({candidate.coordinates.lat:.4f}, {candidate.coordinates.lon:.4f})",
        f"Time: {candidate.local_time} ({candidate.timezone})",
        f"Score: {candidate.score.total}/100 [{candidate.score.label}]",
        f"  Weather sub: {candidate.score.weather:.1f}  Venue/Geo sub: {candidate.score.venue_geo:.1f}  Access sub: {candidate.score.accessibility:.1f}",
    ]
    if candidate.score.hard_gated:
        lines.append(f"  HARD GATED: {'; '.join(candidate.score.gate_reasons)}")
    w = candidate.weather
    lines.append(
        f"Weather: {w.temperature_c}°C (feels {w.apparent_temperature_c}°C), "
        f"rain {w.precipitation_mm}mm ({w.precipitation_probability_pct}%), "
        f"wind {w.wind_speed_kmh}km/h from {w.wind_direction_deg}°, UV {w.uv_index}"
    )
    if w.european_aqi is not None:
        lines.append(f"Air quality: EAQI {w.european_aqi}, PM2.5 {w.pm2_5} µg/m³, PM10 {w.pm10} µg/m³")
    s = candidate.spatial
    parts = []
    if s.nearest_green_m is not None:
        parts.append(f"green space {s.nearest_green_m:.0f}m")
    if s.nearest_water_m is not None:
        parts.append(f"water {s.nearest_water_m:.0f}m")
    if s.nearest_industry_m is not None:
        parts.append(f"industry {s.nearest_industry_m:.0f}m")
    if s.nearest_major_road_m is not None:
        parts.append(f"major road {s.nearest_major_road_m:.0f}m")
    if parts:
        lines.append(f"Proximity: {', '.join(parts)}")
    lines.append(f"Wind-industry: {s.wind_relationship_note}")
    if candidate.score.plus_factors:
        lines.append(f"Plus: {'; '.join(candidate.score.plus_factors[:5])}")
    if candidate.score.minus_factors:
        lines.append(f"Minus: {'; '.join(candidate.score.minus_factors[:5])}")
    return "\n".join(lines)


def template_narrate(
    intent: ParsedIntent,
    candidates: list[RankedCandidate],
    recommendation: str,
    limitations: list[str],
) -> str:
    """Deterministic template-based narration (LLM fallback)."""
    if recommendation == "clarify":
        return "I need more details to make a recommendation. Could you specify a location?"
    if recommendation == "unsupported":
        return "This doesn't seem to be about an outdoor activity I can help with."
    if recommendation == "no_venues":
        return (
            "No suitable venues were found in the search area for this activity. "
            "This may be a map-coverage limitation."
        )
    if recommendation == "avoid":
        reasons = []
        for c in candidates:
            if c.score.hard_gated:
                reasons.extend(c.score.gate_reasons)
        reason_text = " ".join(reasons[:3]) if reasons else "Conditions are unsuitable."
        return (
            f"I recommend avoiding {intent.activity.value if intent.activity else 'this activity'} "
            f"during the requested period. {reason_text}"
        )

    lines: list[str] = []
    activity_name = intent.activity.value if intent.activity else "the activity"
    if candidates:
        top = candidates[0]
        lines.append(
            f"**Best option: {top.place_name}** at {top.local_time} "
            f"(score {top.score.total:.0f}/100 — {top.score.label})."
        )
        w = top.weather
        lines.append(
            f"Expected {w.temperature_c}°C (feels like {w.apparent_temperature_c}°C), "
            f"wind {w.wind_speed_kmh} km/h, rain chance {w.precipitation_probability_pct}%."
        )
        if w.european_aqi is not None:
            lines.append(f"Air quality: European AQI {w.european_aqi}, PM2.5 {w.pm2_5} µg/m³.")
        if top.score.plus_factors:
            lines.append("Positives: " + "; ".join(top.score.plus_factors[:4]) + ".")
        if top.score.minus_factors:
            lines.append("Watch out for: " + "; ".join(top.score.minus_factors[:3]) + ".")
        if top.carry:
            lines.append("**Bring:** " + ", ".join(top.carry[:5]) + ".")

    if len(candidates) > 1:
        lines.append("\n**Alternatives:**")
        for c in candidates[1:3]:
            lines.append(
                f"- {c.place_name} at {c.local_time} — score {c.score.total:.0f}/100 ({c.score.label})"
            )

    if limitations:
        lines.append("\n**Limitations:** " + " ".join(limitations[:3]))

    return "\n".join(lines)


def llm_narrate(
    intent: ParsedIntent,
    candidates: list[RankedCandidate],
    recommendation: str,
    limitations: list[str],
) -> str | None:
    """Use the small LLM to generate a grounded explanation.

    Returns None if the model is not ready or inference fails.
    """
    if not MODEL.ready:
        return None

    activity_name = intent.activity.value if intent.activity else "outdoor activity"
    evidence = "\n---\n".join(_evidence_block(c) for c in candidates[:3])
    constraint_text = ", ".join(c.value for c in intent.constraints) if intent.constraints else "none"

    system_prompt = (
        "You are a geospatial outdoor activity advisor. "
        "Use ONLY the provided evidence to explain the recommendation. "
        "Do NOT invent weather values, places, or scores. "
        "If data is missing, say so. Keep the response under 250 words. "
        "Structure: 1) Best pick with reasons 2) Brief weather/AQI summary "
        "3) What to bring 4) What to watch out for 5) Alternatives if any."
    )
    user_prompt = (
        f"Activity: {activity_name}\n"
        f"Recommendation: {recommendation}\n"
        f"User constraints: {constraint_text}\n"
        f"Limitations: {'; '.join(limitations[:3])}\n\n"
        f"Evidence for top candidates:\n{evidence}\n\n"
        "Explain the recommendation using only the evidence above."
    )

    raw = MODEL.generate(system_prompt, user_prompt, max_new_tokens=350)
    if not raw or len(raw.strip()) < 20:
        return None

    # Validate: reject if the LLM invents place names not in the candidates
    candidate_names = {c.place_name.lower() for c in candidates if c.place_name}
    # Light validation — just check it mentions at least one real candidate
    raw_lower = raw.lower()
    if candidates and not any(name in raw_lower for name in candidate_names):
        logger.warning("LLM narration doesn't reference any real candidate; using template.")
        return None

    return raw.strip()


def narrate(
    intent: ParsedIntent,
    candidates: list[RankedCandidate],
    recommendation: str,
    limitations: list[str],
) -> str:
    """Generate the recommendation summary. Tries LLM, falls back to template."""
    llm_result = llm_narrate(intent, candidates, recommendation, limitations)
    if llm_result:
        return llm_result
    return template_narrate(intent, candidates, recommendation, limitations)
