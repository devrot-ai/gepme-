export interface ReasonChip {
  factor: string;
  text: string;
  emoji: "check" | "warning" | "danger" | "info";
  penalty: number;
}

export interface ModeScore {
  score: number;
  label: "Perfect" | "Good" | "Meh" | "Bad" | "Nope";
  reasons: ReasonChip[];
  hard_gated: boolean;
}

export interface ScoredHour {
  hour_utc: string;
  wave_height_m: number | null;
  wave_period_s: number | null;
  air_temp_c: number | null;
  feelslike_c: number | null;
  wind_ms: number | null;
  gust_ms: number | null;
  precip_prob_pct: number | null;
  precip_mm: number | null;
  uv_index: number | null;
  eu_aqi: number | null;
  pm10: number | null;
  pm2_5: number | null;
  scores: {
    swim_solo: ModeScore;
    swim_dog: ModeScore;
    run_solo: ModeScore;
    run_dog: ModeScore;
  };
}

export interface DailySunTime {
  date: string;         // "2026-02-25"
  sunrise_utc: string;  // ISO string
  sunset_utc: string;   // ISO string
}

export interface ScoredForecastResponse {
  area_id: string;
  updated_at_utc: string;
  provider: string;
  freshness: "fresh" | "stale";
  forecast_age_minutes: number;
  horizon_days: number;
  scoring_version: string;
  hours: ScoredHour[];
  daily: DailySunTime[];
}

export interface HealthForecast {
  area_id: string;
  updated_at_utc: string;
  age_minutes: number;
  freshness: "fresh" | "stale";
  ingest_status: string;
  hours_count: number;
}

export interface HealthResponse {
  status: "healthy" | "degraded" | "unhealthy";
  version: string;
  scoring_version: string;
  forecast: HealthForecast;
  timestamp_utc: string;
}

export type ActivityMode = "swim_solo" | "swim_dog" | "run_solo" | "run_dog";

export const MODE_LABELS: Record<ActivityMode, string> = {
  swim_solo: "Swim",
  swim_dog: "Swim + Dog",
  run_solo: "Run",
  run_dog: "Run + Dog",
};

// Microcopy vibe lines from the UX spec
export const VIBE_LINES: Record<string, string[]> = {
  Perfect: [
    "The coast is calling. Go now.",
    "Sea glass smooth. You know what to do.",
  ],
  Good: [
    "Pretty good out there. A few things to know.",
    "Not bad at all. Check the details.",
  ],
  Meh: [
    "Could go either way. Your call.",
    "Meh, but you've seen worse.",
  ],
  Bad: [
    "Hard pass today. Netflix won't judge.",
    "The coast says not today. Trust it.",
  ],
  Nope: [
    "Somewhere between 'no' and 'absolutely not.'",
  ],
};

// --- TerraSense GeoAI Types ---

export type TerraSenseActivity =
  | "running"
  | "cycling"
  | "golf"
  | "cricket"
  | "football"
  | "tennis"
  | "hiking"
  | "picnic"
  | "outdoor_event"
  | "photography";

export type TerraSenseConstraint =
  | "asthma"
  | "children"
  | "older_adults"
  | "dog"
  | "heat_sensitive";

export interface TerraSenseRecommendRequest {
  question: string;
  activity?: TerraSenseActivity;
  location?: string;
  radius_km?: number;
  latitude?: number;
  longitude?: number;
}

export interface TerraSenseCandidate {
  rank: number;
  place_name: string;
  coordinates: { latitude: number; longitude: number };
  osm: {
    id: number;
    type: string;
    tags: Record<string, string>;
    distance_from_origin_m?: number;
    name?: string;
    geometry_type: string;
  };
  local_time: string;
  timezone: string;
  score: {
    weather: number;
    venue_geo: number;
    accessibility: number;
    total: number;
    label: string;
    hard_gated: boolean;
    gate_reasons: string[];
    plus_factors: string[];
    minus_factors: string[];
    missing_data: string[];
  };
  weather: {
    time_iso: string;
    temperature_c?: number;
    apparent_temperature_c?: number;
    precipitation_mm?: number;
    precipitation_probability_pct?: number;
    wind_speed_kmh?: number;
    wind_direction_deg?: number;
    wind_gusts_kmh?: number;
    uv_index?: number;
    european_aqi?: number;
    pm2_5?: number;
    pm10?: number;
    weather_code?: number;
    missing_fields: string[];
  };
  spatial: {
    candidate_id: string;
    nearest_green_m?: number;
    nearest_green_name?: string;
    nearest_water_m?: number;
    nearest_water_name?: string;
    nearest_industrial_m?: number;
    downwind_from_industrial: boolean;
    route_distance_m?: number;
    route_duration_s?: number;
    live_traffic_available: boolean;
    completeness_notes: string[];
  };
  carry: string[];
  monitor: string[];
  avoid_if: string[];
  evidence_ids: string[];
}

export interface TerraSenseRecommendationResponse {
  request_id: string;
  question: string;
  intent: {
    activity?: TerraSenseActivity;
    place_name?: string;
    latitude?: number;
    longitude?: number;
    radius_km: number;
    date_text?: string;
    time_window_text?: string;
    duration_minutes: number;
    constraints: TerraSenseConstraint[];
    preferences: string[];
    unsupported: boolean;
    missing_location: boolean;
    confidence: number;
    parser: "llm" | "heuristic" | "hybrid";
  };
  origin?: { latitude: number; longitude: number };
  origin_label?: string;
  timezone?: string;
  recommendation: "go" | "caution" | "avoid" | "clarify" | "unsupported" | "no_venues";
  summary: string;
  candidates: TerraSenseCandidate[];
  avoid_reasons: string[];
  limitations: string[];
  sources: Array<{
    id: string;
    source: string;
    retrieved_at_utc: string;
    confidence: number;
    caveats: string[];
  }>;
  model: Record<string, unknown>;
  attribution: string[];
  generated_at_utc: string;
}
