"""Unit tests for TerraSense GeoAI — scoring, intent, geo, spatial, ranker, narrator.

All tests use deterministic fixtures and do NOT require external services.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from terrasense.activities import (
    ActivityProfile,
    all_profiles,
    get_profile,
    match_activity_from_text,
    tags_match_profile,
)
from terrasense.geo import (
    bearing_deg,
    circular_delta_deg,
    downwind_direction,
    geodesic_m,
    haversine_m,
    is_roughly_downwind,
)
from terrasense.intent_parser import heuristic_parse
from terrasense.narrator import _carry_items, _monitor_items, template_narrate
from terrasense.ranker import rank_combo, score_label, pick_top
from terrasense.schemas import (
    ActivityId,
    GeoPoint,
    HourlyConditions,
    OsmFeature,
    ParsedIntent,
    PersonalConstraint,
    RankedCandidate,
    RecommendRequest,
    ScoreBreakdown,
    SpatialContext,
)
from terrasense.spatial import analyze_site
from terrasense.time_window import resolve_window


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _make_hour(**overrides) -> HourlyConditions:
    defaults = dict(
        time_local="2026-10-25T09:00",
        time_utc="2026-10-25T03:30",
        temperature_c=28.0,
        apparent_temperature_c=30.0,
        precipitation_mm=0.0,
        precipitation_probability_pct=10.0,
        wind_speed_kmh=12.0,
        wind_direction_deg=270.0,
        wind_gusts_kmh=18.0,
        uv_index=5.0,
        is_day=True,
        weather_code=0,
        european_aqi=40.0,
        us_aqi=50.0,
        pm2_5=12.0,
        pm10=25.0,
        ozone=60.0,
        nitrogen_dioxide=15.0,
        sunrise="2026-10-25T06:15",
        sunset="2026-10-25T17:50",
        fetched_at_utc="2026-10-25T03:30:00+00:00",
    )
    defaults.update(overrides)
    h = HourlyConditions(**defaults)
    h.missing_fields = []
    return h


def _make_spatial(**overrides) -> SpatialContext:
    defaults = dict(
        nearest_major_road_m=500.0,
        nearest_cycleway_m=200.0,
        nearest_path_m=50.0,
        nearest_industry_m=2000.0,
        nearest_industry_name="Test Factory",
        industry_bearing_deg=180.0,
        downwind_of_industry=False,
        wind_relationship_note="Not downwind.",
        nearest_water_m=300.0,
        nearest_water_name="Test Lake",
        nearest_green_m=100.0,
        nearest_green_name="Test Park",
        route_distance_m=1200.0,
        route_duration_s=900.0,
        live_traffic_available=False,
    )
    defaults.update(overrides)
    return SpatialContext(**defaults)


def _make_osm_feature(**overrides) -> OsmFeature:
    defaults = dict(
        osm_type="way",
        osm_id=123456,
        name="Test Park",
        lat=19.1234,
        lon=72.8765,
        tags={"leisure": "park", "name": "Test Park"},
        distance_m=500.0,
        retrieved_at_utc="2026-10-25T03:30:00+00:00",
    )
    defaults.update(overrides)
    return OsmFeature(**defaults)


def _make_candidate(rank=1, score_total=75.0, **overrides) -> RankedCandidate:
    return RankedCandidate(
        rank=rank,
        place_name=overrides.get("place_name", "Test Park"),
        coordinates=GeoPoint(lat=19.1234, lon=72.8765),
        osm=_make_osm_feature(),
        local_time="2026-10-25T09:00",
        timezone="Asia/Kolkata",
        score=ScoreBreakdown(
            weather=80.0, venue_geo=70.0, accessibility=60.0,
            total=score_total, label="Good", hard_gated=False,
        ),
        weather=_make_hour(),
        spatial=_make_spatial(),
        carry=["Water", "Sunscreen"],
        monitor=["heat", "air_quality"],
        avoid_if=["heavy_rain"],
        evidence_ids=["osm:way/123456", "weather:2026-10-25T03:30"],
    )


# ─── Activity Profiles ──────────────────────────────────────────────────────

class TestActivityProfiles:
    def test_all_ten_activities_loaded(self):
        profiles = all_profiles()
        assert len(profiles) == 10
        expected = {
            ActivityId.running, ActivityId.cycling, ActivityId.golf,
            ActivityId.cricket, ActivityId.football, ActivityId.tennis,
            ActivityId.hiking, ActivityId.picnic, ActivityId.outdoor_event,
            ActivityId.photography,
        }
        assert set(profiles.keys()) == expected

    @pytest.mark.parametrize("activity_id", list(ActivityId))
    def test_each_profile_has_required_fields(self, activity_id):
        p = get_profile(activity_id)
        assert p.display_name
        assert p.aliases
        assert p.venue_types
        assert p.matchers
        assert p.weather.rain_gate_mm > 0
        assert p.weather.wind_gate_kmh > 0
        assert p.weather.heat_gate_c > 0
        assert 0 < p.weights["weather"] <= 1
        assert 0 < p.weights["venue_geo"] <= 1
        assert 0 < p.weights["accessibility"] <= 1
        w_sum = p.weights["weather"] + p.weights["venue_geo"] + p.weights["accessibility"]
        assert abs(w_sum - 1.0) < 0.01, f"Weights must sum to 1.0, got {w_sum}"

    def test_match_activity_running(self):
        assert match_activity_from_text("I want to go running") == ActivityId.running

    def test_match_activity_cycling(self):
        assert match_activity_from_text("cycling near the lake") == ActivityId.cycling

    def test_match_activity_golf(self):
        assert match_activity_from_text("play golf this weekend") == ActivityId.golf

    def test_match_activity_cricket(self):
        assert match_activity_from_text("cricket practice tomorrow") == ActivityId.cricket

    def test_match_activity_football(self):
        assert match_activity_from_text("play football five-a-side") == ActivityId.football

    def test_match_activity_tennis(self):
        assert match_activity_from_text("outdoor tennis") == ActivityId.tennis

    def test_match_activity_hiking(self):
        assert match_activity_from_text("hiking in the hills") == ActivityId.hiking

    def test_match_activity_picnic(self):
        assert match_activity_from_text("picnic in the park") == ActivityId.picnic

    def test_match_activity_outdoor_event(self):
        assert match_activity_from_text("outdoor event gathering") == ActivityId.outdoor_event

    def test_match_activity_photography(self):
        assert match_activity_from_text("sunset photography session") == ActivityId.photography

    def test_match_no_activity(self):
        assert match_activity_from_text("buy groceries") is None

    def test_tags_match_running_park(self):
        profile = get_profile(ActivityId.running)
        ok, spec = tags_match_profile({"leisure": "park"}, profile)
        assert ok is True
        assert spec >= 1

    def test_tags_match_golf_course(self):
        profile = get_profile(ActivityId.golf)
        ok, spec = tags_match_profile({"leisure": "golf_course"}, profile)
        assert ok is True

    def test_tags_no_match_golf_from_highway(self):
        profile = get_profile(ActivityId.golf)
        ok, _ = tags_match_profile({"highway": "motorway"}, profile)
        assert ok is False


# ─── Geo Calculations ───────────────────────────────────────────────────────

class TestGeoCalculations:
    def test_haversine_same_point(self):
        assert haversine_m(19.0, 72.0, 19.0, 72.0) == 0.0

    def test_geodesic_same_point(self):
        assert geodesic_m(19.0, 72.0, 19.0, 72.0) == 0.0

    def test_haversine_known_distance(self):
        # Powai to Bandra: ~13 km
        d = haversine_m(19.1199, 72.9076, 19.0596, 72.8295)
        assert 8000 < d < 15000

    def test_geodesic_agrees_with_haversine(self):
        d1 = haversine_m(19.0, 72.0, 20.0, 73.0)
        d2 = geodesic_m(19.0, 72.0, 20.0, 73.0)
        assert abs(d1 - d2) / d1 < 0.01  # within 1%

    def test_bearing_north(self):
        b = bearing_deg(0.0, 0.0, 1.0, 0.0)
        assert abs(b - 0.0) < 1.0 or abs(b - 360.0) < 1.0

    def test_bearing_east(self):
        b = bearing_deg(0.0, 0.0, 0.0, 1.0)
        assert abs(b - 90.0) < 1.0

    def test_downwind_direction(self):
        assert downwind_direction(0.0) == 180.0
        assert downwind_direction(90.0) == 270.0
        assert downwind_direction(180.0) == 0.0
        assert downwind_direction(270.0) == 90.0

    def test_circular_delta(self):
        assert circular_delta_deg(10.0, 350.0) == 20.0
        assert circular_delta_deg(0.0, 180.0) == 180.0
        assert circular_delta_deg(90.0, 90.0) == 0.0

    def test_is_roughly_downwind_yes(self):
        # Wind from north (0°), industry is to the south (180° bearing to candidate)
        assert is_roughly_downwind(180.0, 0.0) is True

    def test_is_roughly_downwind_no(self):
        # Wind from north, industry is also to the north of candidate
        assert is_roughly_downwind(0.0, 0.0) is False


# ─── Intent Parsing ──────────────────────────────────────────────────────────

class TestIntentParsing:
    def test_basic_running_question(self):
        intent = heuristic_parse("Where should I go running tomorrow morning near Powai?")
        assert intent.activity == ActivityId.running
        assert intent.place_name is not None
        assert "powai" in intent.place_name.lower()
        assert intent.date_text == "tomorrow"
        assert intent.time_window_text == "morning"

    def test_cycling_with_radius(self):
        intent = heuristic_parse("cycling within 5 km of Bandra")
        assert intent.activity == ActivityId.cycling
        assert intent.radius_km == 5.0

    def test_asthma_constraint(self):
        intent = heuristic_parse("I have mild asthma and want to run near IIT Bombay")
        assert PersonalConstraint.asthma in intent.constraints

    def test_children_constraint(self):
        intent = heuristic_parse("picnic with kids near Juhu")
        assert PersonalConstraint.children in intent.constraints
        assert intent.activity == ActivityId.picnic

    def test_dog_constraint(self):
        intent = heuristic_parse("walking with my dog in Powai")
        assert PersonalConstraint.dog in intent.constraints

    def test_missing_location(self):
        intent = heuristic_parse("Where should I play tennis?")
        assert intent.missing_location is True

    def test_coordinates_in_text(self):
        intent = heuristic_parse("running near 19.1176, 72.9060")
        assert intent.latitude is not None
        assert abs(intent.latitude - 19.1176) < 0.01

    def test_radius_in_meters(self):
        intent = heuristic_parse("cycling within 500 meters of Andheri")
        assert intent.radius_km == 0.5

    def test_weekend_date(self):
        intent = heuristic_parse("hiking this weekend")
        assert intent.date_text is not None
        assert "weekend" in intent.date_text

    def test_evening_window(self):
        intent = heuristic_parse("photography evening near Gateway of India")
        assert intent.time_window_text == "evening"

    def test_after_work_window(self):
        intent = heuristic_parse("running after work near office")
        assert intent.time_window_text == "after work"

    def test_unsupported_question(self):
        intent = heuristic_parse("What is the stock price today?")
        assert intent.unsupported is True

    def test_request_overrides(self):
        req = RecommendRequest(
            question="go running",
            activity=ActivityId.cycling,
            location="Mumbai",
            radius_km=3.0,
        )
        intent = heuristic_parse(req.question, req)
        assert intent.activity == ActivityId.cycling
        assert intent.place_name == "Mumbai"
        assert intent.radius_km == 3.0

    def test_walk_defaults_to_hiking(self):
        intent = heuristic_parse("I want to walk around Marine Drive")
        assert intent.activity == ActivityId.hiking


# ─── Time Window Resolution ─────────────────────────────────────────────────

class TestTimeWindow:
    def test_tomorrow_morning(self):
        intent = ParsedIntent(
            activity=ActivityId.running,
            date_text="tomorrow",
            time_window_text="morning",
        )
        tz = "Asia/Kolkata"
        now = datetime(2026, 10, 24, 14, 0, tzinfo=ZoneInfo(tz))
        window = resolve_window(intent, tz, now=now)
        assert window.start.day == 25
        assert window.start.hour == 6
        assert window.end.hour == 10

    def test_today_afternoon(self):
        intent = ParsedIntent(
            activity=ActivityId.picnic,
            date_text="today",
            time_window_text="afternoon",
        )
        tz = "Asia/Kolkata"
        now = datetime(2026, 10, 24, 10, 0, tzinfo=ZoneInfo(tz))
        window = resolve_window(intent, tz, now=now)
        assert window.start.hour == 12
        assert window.end.hour == 17

    def test_day_after_tomorrow(self):
        intent = ParsedIntent(
            activity=ActivityId.golf,
            date_text="day after tomorrow",
        )
        tz = "Asia/Kolkata"
        now = datetime(2026, 10, 24, 10, 0, tzinfo=ZoneInfo(tz))
        window = resolve_window(intent, tz, now=now)
        assert window.start.day == 26


# ─── Spatial Analysis ────────────────────────────────────────────────────────

class TestSpatialAnalysis:
    def test_no_context_features(self):
        ctx = analyze_site(19.12, 72.87, [], wind_from_deg=None,
                          route_distance_m=None, route_duration_s=None)
        assert ctx.nearest_major_road_m is None
        assert ctx.nearest_industry_m is None
        assert ctx.downwind_of_industry is None
        assert "missing" in ctx.wind_relationship_note.lower() or "not computed" in ctx.wind_relationship_note.lower()

    def test_industry_downwind(self):
        # Create industry feature to the north (bearing ~0°)
        industry = _make_osm_feature(
            lat=19.13, lon=72.87, tags={"landuse": "industrial"}, name="Steel Works"
        )
        # Wind from south (180°), downwind direction = 0° (north).
        # Candidate is north of the context center => roughly downwind.
        # Actually we check bearing from industry to candidate.
        ctx = analyze_site(19.14, 72.87, [industry], wind_from_deg=180.0,
                          route_distance_m=None, route_duration_s=None)
        assert ctx.nearest_industry_m is not None

    def test_green_detection(self):
        park = _make_osm_feature(
            lat=19.121, lon=72.871, tags={"leisure": "park", "name": "Green Park"}
        )
        ctx = analyze_site(19.12, 72.87, [park], wind_from_deg=270.0,
                          route_distance_m=1000.0, route_duration_s=600.0)
        assert ctx.nearest_green_m is not None
        assert ctx.nearest_green_m < 500

    def test_water_detection(self):
        lake = _make_osm_feature(
            lat=19.125, lon=72.875, tags={"natural": "water", "name": "Powai Lake"}
        )
        ctx = analyze_site(19.12, 72.87, [lake], wind_from_deg=270.0,
                          route_distance_m=None, route_duration_s=None)
        assert ctx.nearest_water_m is not None

    def test_road_detection(self):
        road = _make_osm_feature(
            lat=19.12, lon=72.871, tags={"highway": "primary"}
        )
        ctx = analyze_site(19.12, 72.87, [road], wind_from_deg=None,
                          route_distance_m=None, route_duration_s=None)
        assert ctx.nearest_major_road_m is not None


# ─── Ranker ──────────────────────────────────────────────────────────────────

class TestRanker:
    def test_good_conditions_score(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour()
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert score.total > 50
        assert score.hard_gated is False
        assert score.label in {"Good", "Fair"}

    def test_heavy_rain_gate(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour(precipitation_mm=10.0)
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert score.hard_gated is True
        assert score.total == 0.0
        assert score.label == "Avoid"

    def test_extreme_heat_gate(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour(apparent_temperature_c=42.0)
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert score.hard_gated is True

    def test_aqi_gate(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour(european_aqi=160.0)
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert score.hard_gated is True

    def test_pm25_gate(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour(pm2_5=80.0)
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert score.hard_gated is True

    def test_thunderstorm_gate(self):
        profile = get_profile(ActivityId.picnic)
        hour = _make_hour(weather_code=95)
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert score.hard_gated is True
        assert any("thunderstorm" in r.lower() for r in score.gate_reasons)

    def test_wind_gate(self):
        profile = get_profile(ActivityId.tennis)
        hour = _make_hour(wind_gusts_kmh=40.0)
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert score.hard_gated is True

    def test_asthma_tightens_aqi(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour(european_aqi=90.0)
        spatial = _make_spatial()
        score_no = rank_combo(profile, hour, spatial, match_specificity=2,
                             constraints=[], origin_distance_m=1000.0)
        score_asthma = rank_combo(profile, hour, spatial, match_specificity=2,
                                 constraints=[PersonalConstraint.asthma],
                                 origin_distance_m=1000.0)
        # Asthma should gate at AQI 80, so AQI 90 is gated
        assert score_asthma.hard_gated is True
        assert score_no.hard_gated is False

    def test_children_tightens_heat(self):
        profile = get_profile(ActivityId.football)
        hour = _make_hour(apparent_temperature_c=35.0)
        spatial = _make_spatial()
        score_no = rank_combo(profile, hour, spatial, match_specificity=2,
                             constraints=[], origin_distance_m=1000.0)
        score_kids = rank_combo(profile, hour, spatial, match_specificity=2,
                               constraints=[PersonalConstraint.children],
                               origin_distance_m=1000.0)
        assert score_kids.hard_gated is True
        assert score_no.hard_gated is False

    def test_night_gate_for_daylight_activity(self):
        profile = get_profile(ActivityId.golf)  # prefer_daylight = True
        hour = _make_hour(is_day=False)
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert score.hard_gated is True

    def test_missing_weather_penalty(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour(temperature_c=None, wind_speed_kmh=None)
        hour.missing_fields = ["temperature_c", "wind_speed_kmh"]
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, match_specificity=2,
                          constraints=[], origin_distance_m=1000.0)
        assert len(score.missing_data) > 0

    def test_deterministic_ranking(self):
        """Same inputs → same scores."""
        profile = get_profile(ActivityId.hiking)
        hour = _make_hour()
        spatial = _make_spatial()
        s1 = rank_combo(profile, hour, spatial, 2, [], 1000.0)
        s2 = rank_combo(profile, hour, spatial, 2, [], 1000.0)
        assert s1.total == s2.total
        assert s1.weather == s2.weather

    def test_pick_top_excludes_gated(self):
        good = ScoreBreakdown(weather=80, venue_geo=70, accessibility=60,
                              total=70, label="Good", hard_gated=False)
        gated = ScoreBreakdown(weather=0, venue_geo=0, accessibility=0,
                               total=0, label="Avoid", hard_gated=True,
                               gate_reasons=["Heavy rain"])
        combos = [(good, "a"), (gated, "b")]
        top = pick_top(combos, k=3)
        assert len(top) == 1
        assert top[0][1] == "a"

    def test_pick_top_ranks_by_score(self):
        s1 = ScoreBreakdown(weather=90, venue_geo=80, accessibility=70,
                            total=82, label="Good", hard_gated=False)
        s2 = ScoreBreakdown(weather=70, venue_geo=60, accessibility=50,
                            total=60, label="Fair", hard_gated=False)
        combos = [(s2, "b"), (s1, "a")]
        top = pick_top(combos, k=2)
        assert top[0][1] == "a"  # higher score first

    def test_all_gated_returns_empty(self):
        gated = ScoreBreakdown(weather=0, venue_geo=0, accessibility=0,
                               total=0, label="Avoid", hard_gated=True,
                               gate_reasons=["Rain"])
        combos = [(gated, "a"), (gated, "b")]
        top = pick_top(combos, k=3)
        assert len(top) == 0

    @pytest.mark.parametrize("activity_id", list(ActivityId))
    def test_each_activity_scores_without_error(self, activity_id):
        """Every activity must score without exceptions on valid input."""
        profile = get_profile(activity_id)
        hour = _make_hour()
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, 2, [], 1000.0)
        assert 0 <= score.total <= 100
        assert score.label in {"Good", "Fair", "Poor", "Avoid"}

    def test_industry_downwind_penalty(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour()
        spatial_ok = _make_spatial(downwind_of_industry=False)
        spatial_bad = _make_spatial(downwind_of_industry=True)
        s_ok = rank_combo(profile, hour, spatial_ok, 2, [], 1000.0)
        s_bad = rank_combo(profile, hour, spatial_bad, 2, [], 1000.0)
        assert s_bad.venue_geo < s_ok.venue_geo

    def test_cycleway_bonus_for_cycling(self):
        profile = get_profile(ActivityId.cycling)
        hour = _make_hour()
        spatial_with = _make_spatial(nearest_cycleway_m=100.0)
        spatial_without = _make_spatial(nearest_cycleway_m=None)
        s_with = rank_combo(profile, hour, spatial_with, 2, [], 1000.0)
        s_without = rank_combo(profile, hour, spatial_without, 2, [], 1000.0)
        assert s_with.venue_geo > s_without.venue_geo

    def test_golden_hour_photography_bonus(self):
        profile = get_profile(ActivityId.photography)
        # Hour near sunset
        hour_golden = _make_hour(
            time_local="2026-10-25T17:30",
            sunset="2026-10-25T17:50",
            precipitation_mm=0.0,
        )
        hour_noon = _make_hour(
            time_local="2026-10-25T12:00",
            sunset="2026-10-25T17:50",
        )
        spatial = _make_spatial()
        s_golden = rank_combo(profile, hour_golden, spatial, 2, [], 1000.0)
        s_noon = rank_combo(profile, hour_noon, spatial, 2, [], 1000.0)
        assert s_golden.weather >= s_noon.weather


# ─── Narrator ────────────────────────────────────────────────────────────────

class TestNarrator:
    def test_template_go_recommendation(self):
        intent = ParsedIntent(activity=ActivityId.running)
        candidates = [_make_candidate(rank=1, score_total=80.0)]
        text = template_narrate(intent, candidates, "go", [])
        assert "Test Park" in text
        assert "80" in text

    def test_template_avoid_recommendation(self):
        gated_candidate = _make_candidate(rank=1, score_total=0.0)
        gated_candidate.score.hard_gated = True
        gated_candidate.score.gate_reasons = ["Heavy rain"]
        intent = ParsedIntent(activity=ActivityId.picnic)
        text = template_narrate(intent, [gated_candidate], "avoid", [])
        assert "avoid" in text.lower()

    def test_template_clarify(self):
        intent = ParsedIntent(missing_location=True)
        text = template_narrate(intent, [], "clarify", [])
        assert "location" in text.lower()

    def test_template_no_venues(self):
        intent = ParsedIntent(activity=ActivityId.golf)
        text = template_narrate(intent, [], "no_venues", [])
        assert "no suitable" in text.lower() or "no mapped" in text.lower() or "not found" in text.lower()

    def test_template_with_alternatives(self):
        c1 = _make_candidate(rank=1, score_total=80.0, place_name="Park A")
        c2 = _make_candidate(rank=2, score_total=65.0, place_name="Park B")
        intent = ParsedIntent(activity=ActivityId.running)
        text = template_narrate(intent, [c1, c2], "go", [])
        assert "Park B" in text

    def test_carry_items_running(self):
        items = _carry_items(ActivityId.running, [])
        assert any("water" in i.lower() for i in items)

    def test_carry_items_asthma(self):
        items = _carry_items(ActivityId.running, [PersonalConstraint.asthma])
        assert any("mask" in i.lower() or "pm2.5" in i.lower() for i in items)

    def test_monitor_items(self):
        items = _monitor_items(ActivityId.running)
        assert len(items) > 0


# ─── Score Labels ────────────────────────────────────────────────────────────

class TestScoreLabels:
    def test_good(self):
        assert score_label(85.0, False) == "Good"

    def test_fair(self):
        assert score_label(65.0, False) == "Fair"

    def test_poor(self):
        assert score_label(45.0, False) == "Poor"

    def test_avoid_low(self):
        assert score_label(30.0, False) == "Avoid"

    def test_gated(self):
        assert score_label(95.0, True) == "Avoid"


# ─── Schema Validation ──────────────────────────────────────────────────────

class TestSchemaValidation:
    def test_recommend_request_min_question(self):
        with pytest.raises(Exception):
            RecommendRequest(question="ab")  # min_length=3

    def test_recommend_request_valid(self):
        r = RecommendRequest(question="Where to run near Powai?")
        assert r.question

    def test_parsed_intent_defaults(self):
        intent = ParsedIntent()
        assert intent.radius_km == 10.0
        assert intent.duration_minutes == 60
        assert intent.constraints == []

    def test_parsed_intent_constraint_norm(self):
        intent = ParsedIntent(constraints=None)
        assert intent.constraints == []

    def test_invalid_radius(self):
        with pytest.raises(Exception):
            RecommendRequest(question="test test", radius_km=-5)


# ─── Wind Direction Calculations ─────────────────────────────────────────────

class TestWindDirection:
    def test_downwind_from_north(self):
        assert downwind_direction(0.0) == 180.0

    def test_downwind_from_south(self):
        assert downwind_direction(180.0) == 0.0

    def test_downwind_from_west(self):
        assert downwind_direction(270.0) == 90.0

    def test_circular_wrap(self):
        assert circular_delta_deg(5.0, 355.0) == 10.0

    def test_downwind_tolerance(self):
        # Wind from 0° (north), industry bearing to candidate is 170°
        # downwind dir is 180°, delta is 10° < 35° tolerance
        assert is_roughly_downwind(170.0, 0.0, tolerance_deg=35.0) is True
        # bearing 120° → delta 60° > 35° → not downwind
        assert is_roughly_downwind(120.0, 0.0, tolerance_deg=35.0) is False


# ─── Safety Gate Integrity ───────────────────────────────────────────────────

class TestSafetyGates:
    """Ensure hard gates cannot be overridden by high subscores."""

    def test_rain_gate_overrides_good_venue(self):
        profile = get_profile(ActivityId.picnic)
        hour = _make_hour(precipitation_mm=5.0)  # well above 0.8 mm gate
        spatial = _make_spatial(nearest_green_m=10.0, nearest_water_m=10.0)  # great venue
        score = rank_combo(profile, hour, spatial, match_specificity=3,
                          constraints=[], origin_distance_m=100.0)
        assert score.hard_gated is True
        assert score.total == 0.0

    def test_all_candidates_gated_returns_avoid(self):
        """When every combo is gated, pick_top should return empty."""
        g = ScoreBreakdown(
            weather=0, venue_geo=100, accessibility=100,
            total=0, label="Avoid", hard_gated=True,
            gate_reasons=["Extreme rain"],
        )
        combos = [(g, "park1"), (g, "park2"), (g, "park3")]
        assert pick_top(combos, k=3) == []

    def test_personal_constraint_cannot_be_overridden(self):
        """Asthma with high AQI must gate regardless of other factors."""
        profile = get_profile(ActivityId.running)
        hour = _make_hour(european_aqi=85.0, pm2_5=12.0)
        spatial = _make_spatial(nearest_green_m=10.0)
        score = rank_combo(profile, hour, spatial, 3,
                          constraints=[PersonalConstraint.asthma],
                          origin_distance_m=100.0)
        assert score.hard_gated is True


# ─── Missing / Stale Data ───────────────────────────────────────────────────

class TestMissingData:
    def test_all_weather_missing(self):
        hour = _make_hour(
            temperature_c=None, apparent_temperature_c=None,
            precipitation_mm=None, precipitation_probability_pct=None,
            wind_speed_kmh=None, wind_gusts_kmh=None,
            uv_index=None, european_aqi=None, pm2_5=None,
        )
        hour.missing_fields = ["temperature_c", "wind_speed_kmh", "uv_index", "european_aqi", "pm2_5"]
        profile = get_profile(ActivityId.hiking)
        spatial = _make_spatial()
        score = rank_combo(profile, hour, spatial, 2, [], 1000.0)
        # Should not crash, should note missing data
        assert score.weather == 50.0 * 0.9 or score.weather == 50.0  # neutral
        assert len(score.missing_data) > 0

    def test_no_route_data(self):
        profile = get_profile(ActivityId.running)
        hour = _make_hour()
        spatial = _make_spatial(route_distance_m=None, route_duration_s=None)
        score = rank_combo(profile, hour, spatial, 2, [], None)
        assert any("distance" in m.lower() or "route" in m.lower() for m in score.missing_data)


# ─── Evidence Provenance ─────────────────────────────────────────────────────

class TestProvenance:
    def test_candidate_has_evidence_ids(self):
        c = _make_candidate()
        assert len(c.evidence_ids) >= 2
        assert any("osm:" in eid for eid in c.evidence_ids)
        assert any("weather:" in eid for eid in c.evidence_ids)
