from __future__ import annotations

from dataclasses import dataclass

from terrasense.activities import ActivityProfile
from terrasense.schemas import (
    HourlyConditions,
    PersonalConstraint,
    ScoreBreakdown,
    SpatialContext,
)

THUNDER = {95, 96, 99}


def score_label(score: float, gated: bool) -> str:
    if gated:
        return "Avoid"
    if score >= 80:
        return "Good"
    if score >= 60:
        return "Fair"
    if score >= 40:
        return "Poor"
    return "Avoid"


def _lin(value: float | None, ok: float, bad: float) -> tuple[float | None, bool]:
    if value is None:
        return None, True
    if bad == ok:
        return (1.0 if value <= ok else 0.0), False
    if ok < bad:
        if value <= ok:
            return 1.0, False
        if value >= bad:
            return 0.0, False
        return 1.0 - (value - ok) / (bad - ok), False
    if value >= ok:
        return 1.0, False
    if value <= bad:
        return 0.0, False
    return 1.0 - (ok - value) / (ok - bad), False


def _tighten(profile: ActivityProfile, constraints: list[PersonalConstraint]) -> ActivityProfile:
    """Conservative copy of numeric gates; does not mutate the cached profile."""
    w = profile.weather
    aqi_gate = w.aqi_gate
    pm25_gate = w.pm25_gate
    heat_gate = w.heat_gate_c
    rain_gate = w.rain_gate_mm
    if PersonalConstraint.asthma in constraints:
        aqi_gate = min(aqi_gate, 80)
        pm25_gate = min(pm25_gate, 35)
    if PersonalConstraint.children in constraints:
        heat_gate = min(heat_gate, 34)
        aqi_gate = min(aqi_gate, 100)
    if PersonalConstraint.older_adults in constraints:
        heat_gate = min(heat_gate, 34)
        aqi_gate = min(aqi_gate, 100)
    if PersonalConstraint.heat_sensitive in constraints or PersonalConstraint.dog in constraints:
        heat_gate = min(heat_gate, 32)
    from dataclasses import replace

    weather = replace(w, aqi_gate=aqi_gate, pm25_gate=pm25_gate, heat_gate_c=heat_gate, rain_gate_mm=rain_gate)
    return replace(profile, weather=weather)


@dataclass
class RankedCombo:
    venue_name: str
    hour: HourlyConditions
    spatial: SpatialContext
    breakdown: ScoreBreakdown


def rank_combo(
    profile: ActivityProfile,
    hour: HourlyConditions,
    spatial: SpatialContext,
    match_specificity: int,
    constraints: list[PersonalConstraint],
    origin_distance_m: float | None,
) -> ScoreBreakdown:
    p = _tighten(profile, constraints)
    w = p.weather
    plus: list[str] = []
    minus: list[str] = []
    missing: list[str] = list(hour.missing_fields)
    gates: list[str] = []

    if hour.weather_code in THUNDER:
        gates.append("Thunderstorm weather code from Open-Meteo.")
    if hour.precipitation_mm is not None and hour.precipitation_mm >= w.rain_gate_mm:
        gates.append(f"Precipitation {hour.precipitation_mm} mm exceeds activity rain gate {w.rain_gate_mm} mm.")
    if hour.precipitation_probability_pct is not None and hour.precipitation_probability_pct >= w.rain_gate_prob:
        gates.append(
            f"Rain probability {hour.precipitation_probability_pct}% exceeds gate {w.rain_gate_prob}%."
        )
    gust = hour.wind_gusts_kmh if hour.wind_gusts_kmh is not None else hour.wind_speed_kmh
    if gust is not None and gust >= w.wind_gate_kmh:
        gates.append(f"Wind {gust} km/h exceeds gate {w.wind_gate_kmh} km/h.")
    temp = hour.apparent_temperature_c if hour.apparent_temperature_c is not None else hour.temperature_c
    if temp is not None and temp >= w.heat_gate_c:
        gates.append(f"Apparent/air temperature {temp}°C exceeds heat gate {w.heat_gate_c}°C.")
    if hour.european_aqi is not None and hour.european_aqi >= w.aqi_gate:
        gates.append(f"European AQI {hour.european_aqi} exceeds gate {w.aqi_gate}.")
    if hour.pm2_5 is not None and hour.pm2_5 >= w.pm25_gate:
        gates.append(f"PM2.5 {hour.pm2_5} µg/m³ exceeds gate {w.pm25_gate}.")
    if w.prefer_daylight and hour.is_day is False:
        gates.append("Activity prefers daylight; forecast marks this hour as night.")

    # Weather subscore 0-100
    rain_s, rain_m = _lin(hour.precipitation_mm, 0.0, w.rain_gate_mm)
    heat_s, heat_m = _lin(temp, w.heat_warn_c - 6, w.heat_gate_c)
    wind_s, wind_m = _lin(gust, 15.0, w.wind_gate_kmh)
    aqi_s, aqi_m = _lin(hour.european_aqi, 40.0, w.aqi_gate)
    uv_s, uv_m = _lin(hour.uv_index, 4.0, max(w.uv_warn + 2, 11.0))
    parts = [x for x in [rain_s, heat_s, wind_s, aqi_s, uv_s] if x is not None]
    miss_flags = [rain_m, heat_m, wind_m, aqi_m, uv_m]
    if not parts:
        weather_score = 50.0
        missing.append("No weather variables available; weather score set to neutral 50, not assumed good.")
    else:
        weather_score = 100.0 * (sum(parts) / len(parts))
        if any(miss_flags):
            weather_score *= 0.9
            minus.append("Weather score reduced 10% because some observations are missing.")

    if w.photography_golden_hour and hour.sunset:
        # Reward hours within 90 minutes of sunset if precipitation is low
        try:
            from datetime import datetime

            sunset = datetime.fromisoformat(hour.sunset)
            cur = datetime.fromisoformat(hour.time_local)
            delta = abs((cur - sunset).total_seconds()) / 60.0
            if delta <= 90 and (hour.precipitation_mm or 0) < 0.5:
                weather_score = min(100.0, weather_score + 12)
                plus.append("Photography golden-hour window near sunset.")
        except ValueError:
            pass

    if temp is not None and temp <= w.cold_warn_c:
        weather_score = max(0.0, weather_score - 10)
        minus.append(f"Cool conditions ({temp}°C).")
    if hour.uv_index is not None and hour.uv_index >= w.uv_warn:
        minus.append(f"High UV index {hour.uv_index}.")
    else:
        if hour.uv_index is not None and hour.uv_index <= 4:
            plus.append(f"Moderate/low UV ({hour.uv_index}).")

    # Venue / geospatial 0-100
    geo = 40.0 + 10.0 * min(match_specificity, 3)
    prefs = p.geo_preferences

    def proximity_bonus(metres: float | None, kind: str, prefer: str) -> None:
        nonlocal geo
        if metres is None:
            missing.append(f"{kind} proximity unknown (not mapped or query failed).")
            return
        if prefer == "prefer" and metres < 250:
            geo += 12
            plus.append(f"Close mapped {kind} (~{metres:.0f} m).")
        elif prefer == "prefer" and metres < 800:
            geo += 6
        if prefer == "avoid" and metres < 300:
            geo -= 18
            minus.append(f"Mapped {kind} within {metres:.0f} m.")
        elif prefer == "avoid" and metres < 800:
            geo -= 8
            minus.append(f"Mapped {kind} within {metres:.0f} m.")

    proximity_bonus(spatial.nearest_green_m, "green space", prefs.get("green_cover", "optional"))
    proximity_bonus(spatial.nearest_water_m, "water", prefs.get("water", "optional"))
    proximity_bonus(spatial.nearest_industry_m, "industry", prefs.get("industry", "avoid"))
    if spatial.downwind_of_industry:
        geo -= 15
        minus.append("Heuristic: candidate is approximately downwind of mapped industry.")
    if prefs.get("roads") == "prefer_quiet" and spatial.nearest_major_road_m is not None:
        if spatial.nearest_major_road_m < 80:
            geo -= 10
            minus.append("Very close to a major mapped road.")
        elif spatial.nearest_major_road_m > 250:
            geo += 6
            plus.append("Separated from nearest major mapped road.")
    if prefs.get("roads") == "prefer_cycle_infra":
        if spatial.nearest_cycleway_m is not None and spatial.nearest_cycleway_m < 200:
            geo += 12
            plus.append("Mapped cycleway nearby.")
        else:
            minus.append("No nearby mapped cycleway; using general OSM features.")
    geo = max(0.0, min(100.0, geo))

    # Accessibility 0-100
    access = 55.0
    d = origin_distance_m if origin_distance_m is not None else spatial.route_distance_m
    if d is None:
        missing.append("Distance/route missing; accessibility set near-neutral.")
        access = 50.0
    else:
        # closer is better; 0m -> 100, 10km -> ~20
        access = max(15.0, 100.0 - (d / 100.0))
        plus.append(f"About {d/1000:.1f} km from the requested origin (geodesic or OSRM).")
    if spatial.route_duration_s is not None:
        plus.append(f"OSRM free-flow travel ~{int(spatial.route_duration_s/60)} min (not live traffic).")
    access = max(0.0, min(100.0, access))

    weights = p.weights
    total = (
        weather_score * weights["weather"]
        + geo * weights["venue_geo"]
        + access * weights["accessibility"]
    )
    gated = bool(gates)
    if gated:
        total = 0.0
    return ScoreBreakdown(
        weather=round(weather_score, 2),
        venue_geo=round(geo, 2),
        accessibility=round(access, 2),
        total=round(total, 2),
        label=score_label(total, gated),
        hard_gated=gated,
        gate_reasons=gates,
        plus_factors=plus,
        minus_factors=minus,
        missing_data=missing,
    )


def pick_top(
    combos: list[tuple[ScoreBreakdown, object]],
    k: int = 3,
) -> list[tuple[ScoreBreakdown, object]]:
    viable = [(s, o) for s, o in combos if not s.hard_gated]
    viable.sort(key=lambda x: (-x[0].total, x[0].minus_factors.__len__()))
    return viable[:k]
