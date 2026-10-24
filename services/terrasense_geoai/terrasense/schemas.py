from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ActivityId(str, Enum):
    running = "running"
    cycling = "cycling"
    golf = "golf"
    cricket = "cricket"
    football = "football"
    tennis = "tennis"
    hiking = "hiking"
    picnic = "picnic"
    outdoor_event = "outdoor_event"
    photography = "photography"


class PersonalConstraint(str, Enum):
    asthma = "asthma"
    children = "children"
    older_adults = "older_adults"
    dog = "dog"
    heat_sensitive = "heat_sensitive"


class RecommendRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    activity: ActivityId | None = None
    location: str | None = None
    radius_km: float | None = Field(default=None, gt=0, le=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ParsedIntent(BaseModel):
    activity: ActivityId | None = None
    place_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float = Field(default=10.0, gt=0, le=80)
    date_text: str | None = None
    time_window_text: str | None = None
    duration_minutes: int = Field(default=60, ge=15, le=480)
    constraints: list[PersonalConstraint] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    unsupported: bool = False
    missing_location: bool = False
    confidence: float = Field(default=0.5, ge=0, le=1)
    parser: Literal["llm", "heuristic", "hybrid"] = "heuristic"

    @field_validator("constraints", mode="before")
    @classmethod
    def _norm_constraints(cls, v: Any) -> list[Any]:
        if v is None:
            return []
        return v


class GeoPoint(BaseModel):
    lat: float
    lon: float


class Provenance(BaseModel):
    source: str
    fetched_at_utc: str
    valid_time: str | None = None
    units: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    stale: bool = False
    missing: bool = False
    failed: bool = False


class OsmFeature(BaseModel):
    osm_type: str
    osm_id: int
    name: str | None = None
    lat: float
    lon: float
    tags: dict[str, str] = Field(default_factory=dict)
    distance_m: float | None = None
    retrieved_at_utc: str
    source: str = "OpenStreetMap Overpass"


class HourlyConditions(BaseModel):
    time_local: str
    time_utc: str
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability_pct: float | None = None
    wind_speed_kmh: float | None = None
    wind_direction_deg: float | None = None
    wind_gusts_kmh: float | None = None
    uv_index: float | None = None
    is_day: bool | None = None
    weather_code: int | None = None
    european_aqi: float | None = None
    us_aqi: float | None = None
    pm2_5: float | None = None
    pm10: float | None = None
    ozone: float | None = None
    nitrogen_dioxide: float | None = None
    sunrise: str | None = None
    sunset: str | None = None
    provider: str = "Open-Meteo"
    fetched_at_utc: str = ""
    missing_fields: list[str] = Field(default_factory=list)


class SpatialContext(BaseModel):
    nearest_major_road_m: float | None = None
    nearest_cycleway_m: float | None = None
    nearest_path_m: float | None = None
    nearest_industry_m: float | None = None
    nearest_industry_name: str | None = None
    industry_bearing_deg: float | None = None
    downwind_of_industry: bool | None = None
    wind_relationship_note: str
    nearest_water_m: float | None = None
    nearest_water_name: str | None = None
    nearest_green_m: float | None = None
    nearest_green_name: str | None = None
    route_distance_m: float | None = None
    route_duration_s: float | None = None
    live_traffic_available: bool = False
    completeness_notes: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    weather: float
    venue_geo: float
    accessibility: float
    total: float
    label: str
    hard_gated: bool
    gate_reasons: list[str] = Field(default_factory=list)
    plus_factors: list[str] = Field(default_factory=list)
    minus_factors: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class RankedCandidate(BaseModel):
    rank: int
    place_name: str
    coordinates: GeoPoint
    osm: OsmFeature
    local_time: str
    timezone: str
    score: ScoreBreakdown
    weather: HourlyConditions
    spatial: SpatialContext
    carry: list[str]
    monitor: list[str]
    avoid_if: list[str]
    evidence_ids: list[str]


class RecommendationResponse(BaseModel):
    request_id: str
    question: str
    intent: ParsedIntent
    origin: GeoPoint | None = None
    origin_label: str | None = None
    timezone: str | None = None
    recommendation: Literal["go", "caution", "avoid", "clarify", "unsupported", "no_venues"]
    summary: str
    candidates: list[RankedCandidate] = Field(default_factory=list)
    avoid_reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    sources: list[Provenance] = Field(default_factory=list)
    model: dict[str, Any] = Field(default_factory=dict)
    attribution: list[str] = Field(
        default_factory=lambda: [
            "© OpenStreetMap contributors",
            "Weather and air quality: Open-Meteo (https://open-meteo.com)",
            "Routing estimates: OSRM (Open Source Routing Machine)",
        ]
    )
    generated_at_utc: str


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    service: str
    version: str
    scoring_version: str
    model: dict[str, Any]
    providers: dict[str, str]
    timestamp_utc: str
