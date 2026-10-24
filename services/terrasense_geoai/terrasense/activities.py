from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from terrasense.schemas import ActivityId


@dataclass(frozen=True)
class WeatherProfile:
    rain_gate_mm: float
    rain_gate_prob: float
    wind_gate_kmh: float
    heat_gate_c: float
    heat_warn_c: float
    cold_warn_c: float
    uv_warn: float
    aqi_gate: float
    pm25_gate: float
    prefer_daylight: bool
    photography_golden_hour: bool


@dataclass(frozen=True)
class ActivityProfile:
    id: ActivityId
    display_name: str
    aliases: tuple[str, ...]
    venue_types: tuple[str, ...]
    matchers: tuple[dict[str, str], ...]
    weather: WeatherProfile
    weights: dict[str, float]
    geo_preferences: dict[str, str]
    safety_notes: tuple[str, ...]
    prep_categories: tuple[str, ...]
    monitor: tuple[str, ...]
    avoid_when: tuple[str, ...]


def _load_raw() -> dict[str, Any]:
    path = Path(__file__).parent / "data" / "activities.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def all_profiles() -> dict[ActivityId, ActivityProfile]:
    raw = _load_raw()["activities"]
    out: dict[ActivityId, ActivityProfile] = {}
    for key, data in raw.items():
        aid = ActivityId(key)
        w = data["weather"]
        out[aid] = ActivityProfile(
            id=aid,
            display_name=data["display_name"],
            aliases=tuple(data.get("aliases", [])),
            venue_types=tuple(data.get("venue_types", [])),
            matchers=tuple(data.get("matchers", [])),
            weather=WeatherProfile(**w),
            weights=dict(data["weights"]),
            geo_preferences=dict(data.get("geo_preferences", {})),
            safety_notes=tuple(data.get("safety_notes", [])),
            prep_categories=tuple(data.get("prep_categories", [])),
            monitor=tuple(data.get("monitor", [])),
            avoid_when=tuple(data.get("avoid_when", [])),
        )
    if len(out) != 10:
        raise RuntimeError(f"Expected 10 activity profiles, found {len(out)}")
    return out


def get_profile(activity: ActivityId) -> ActivityProfile:
    return all_profiles()[activity]


def match_activity_from_text(text: str) -> ActivityId | None:
    t = text.lower()
    best: tuple[int, ActivityId] | None = None
    for profile in all_profiles().values():
        for alias in sorted(profile.aliases, key=len, reverse=True):
            if alias.lower() in t:
                score = len(alias)
                if best is None or score > best[0]:
                    best = (score, profile.id)
                break
    return None if best is None else best[1]


def tags_match_profile(tags: dict[str, str], profile: ActivityProfile) -> tuple[bool, int]:
    """Return (eligible, specificity). Specificity is number of matched tag keys."""
    tags_l = {k.lower(): str(v).lower() for k, v in tags.items()}
    best = 0
    for matcher in profile.matchers:
        ok = True
        for k, v in matcher.items():
            actual = tags_l.get(k.lower())
            if actual is None:
                ok = False
                break
            if v.lower() not in actual and actual not in v.lower():
                ok = False
                break
        if ok:
            best = max(best, len(matcher))
    return best > 0, best


PREP_TEXT = {
    "hydration": "Carry drinking water.",
    "footwear": "Wear activity-appropriate shoes with grip.",
    "sun_protection": "Use sunscreen, a hat, and shade breaks.",
    "air_quality": "If you are sensitive to air pollution, carry a mask and check AQI on arrival.",
    "lights": "Use lights and reflective gear if riding near dusk.",
    "helmet": "Wear a helmet.",
    "rain_layer": "Pack a light rain layer; conditions can change.",
    "visibility": "Prefer well-lit, mapped routes; live traffic is not available.",
    "first_aid": "Carry a small first-aid kit for group play.",
    "shade": "Plan for shade or a canopy; midday UV can be high.",
    "insect": "Consider insect protection in vegetated parks.",
    "waste": "Pack out litter; many parks have limited bins.",
    "water": "Carry more water than you think you need.",
    "navigation": "Trails may be incompletely mapped; download an offline map.",
    "rain_plan": "Have an indoor backup if rain probability rises.",
    "weather_protection": "Protect camera gear from rain and dust.",
    "extra_battery": "Carry a spare battery; humidity and heat drain cells faster.",
    "polarizer": "A polarizer can help with water and sky glare.",
    "grip": "Bring a towel for grip if humidity is high.",
}
