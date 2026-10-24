from __future__ import annotations

import re

from pydantic import ValidationError

from terrasense.activities import match_activity_from_text
from terrasense.llm import INTENT_SCHEMA_HINT, MODEL, extract_json_object
from terrasense.schemas import ActivityId, ParsedIntent, PersonalConstraint, RecommendRequest

_RADIUS = re.compile(r"within\s+(\d+(?:\.\d+)?)\s*(km|kilometres|kilometers|m|metres|meters)", re.I)
_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*[, ]\s*(-?\d{1,3}\.\d+)")


def _constraints_from_text(text: str) -> list[PersonalConstraint]:
    t = text.lower()
    found: list[PersonalConstraint] = []
    if "asthma" in t:
        found.append(PersonalConstraint.asthma)
    if any(w in t for w in ("kid", "kids", "child", "children")):
        found.append(PersonalConstraint.children)
    if any(w in t for w in ("parent", "parents", "elderly", "older adult", "senior")):
        found.append(PersonalConstraint.older_adults)
    if "dog" in t:
        found.append(PersonalConstraint.dog)
    if "heat sensitive" in t or "heat-sensitive" in t:
        found.append(PersonalConstraint.heat_sensitive)
    return found


def _radius_from_text(text: str) -> float | None:
    m = _RADIUS.search(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit in {"m", "metres", "meters"}:
        return max(0.2, value / 1000.0)
    return value


def _place_from_text(text: str) -> str | None:
    patterns = [
        r"\bnear(?:by)?\s+([^?,.]+)",
        r"\baround\s+([^?,.]+)",
        r"\bin\s+([A-Z][A-Za-z ]{2,40})",
        r"\bof\s+([A-Z][A-Za-z ]{2,40})",
        r"\bwithin\s+\d+(?:\.\d+)?\s*km of\s+([^?,.]+)",
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.I)
        if m:
            place = m.group(1).strip(" .")
            place = re.sub(r"\b(tomorrow|today|this weekend|evening|morning|afternoon).*$", "", place, flags=re.I).strip()
            if len(place) >= 3 and place.lower() not in {"the", "a", "an"}:
                return place
    return None


def heuristic_parse(question: str, request: RecommendRequest | None = None) -> ParsedIntent:
    q = question.strip()
    activity = match_activity_from_text(q)
    coords = _COORDS.search(q)
    lat = lon = None
    if coords:
        lat, lon = float(coords.group(1)), float(coords.group(2))
    place = _place_from_text(q)
    radius = _radius_from_text(q) or 10.0
    constraints = _constraints_from_text(q)
    date_text = None
    time_window = None
    ql = q.lower()
    for token in ("day after tomorrow", "tomorrow", "today", "this weekend", "weekend", "saturday", "sunday"):
        if token in ql:
            date_text = token
            break
    for token in ("after work", "morning", "afternoon", "evening", "sunset", "night"):
        if token in ql:
            time_window = token
            break
    missing_location = place is None and lat is None
    if request:
        if request.activity:
            activity = request.activity
        if request.location:
            place = request.location
            missing_location = False
        if request.latitude is not None and request.longitude is not None:
            lat, lon = request.latitude, request.longitude
            missing_location = False
        if request.radius_km:
            radius = request.radius_km
    unsupported = activity is None and any(
        w in ql for w in ("stock", "crypto", "recipe", "homework", "code")
    )
    if activity is None and "walk" in ql:
        activity = ActivityId.hiking
    return ParsedIntent(
        activity=activity,
        place_name=place,
        latitude=lat,
        longitude=lon,
        radius_km=radius,
        date_text=date_text,
        time_window_text=time_window,
        duration_minutes=90 if activity in {ActivityId.hiking, ActivityId.golf, ActivityId.outdoor_event} else 60,
        constraints=constraints,
        missing_location=missing_location and lat is None,
        unsupported=unsupported,
        confidence=0.55 if activity else 0.3,
        parser="heuristic",
    )


def parse_intent(question: str, request: RecommendRequest | None = None) -> ParsedIntent:
    heuristic = heuristic_parse(question, request)
    raw = MODEL.generate(
        "You extract structured intent for an outdoor activity assistant. "
        + INTENT_SCHEMA_HINT
        + " Do not invent places or weather.",
        question,
    )
    data = extract_json_object(raw or "")
    if not data:
        return heuristic
    try:
        llm_intent = ParsedIntent.model_validate({**data, "parser": "llm"})
    except ValidationError:
        return heuristic
    # Never trust LLM coordinates or place if heuristic/request already has evidence,
    # and never keep LLM-invented coords when the question had none.
    if heuristic.latitude is None and request is None:
        llm_intent.latitude = None
        llm_intent.longitude = None
    if request and request.latitude is not None:
        llm_intent.latitude = request.latitude
        llm_intent.longitude = request.longitude
    if request and request.location:
        llm_intent.place_name = request.location
        llm_intent.missing_location = False
    if heuristic.place_name and not llm_intent.place_name:
        llm_intent.place_name = heuristic.place_name
    if heuristic.activity and llm_intent.activity is None:
        llm_intent.activity = heuristic.activity
    if heuristic.constraints and not llm_intent.constraints:
        llm_intent.constraints = heuristic.constraints
    if heuristic.radius_km and (not data.get("radius_km")):
        llm_intent.radius_km = heuristic.radius_km
    llm_intent.parser = "hybrid" if MODEL.ready else "llm"
    if llm_intent.place_name is None and llm_intent.latitude is None:
        llm_intent.missing_location = True
    return llm_intent
