import type { ScoredForecastResponse, HealthResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
const AREA_ID = "tel_aviv_coast";

export async function fetchScores(days: number = 7): Promise<ScoredForecastResponse> {
  const res = await fetch(
    `${API_BASE}/v1/public/scores?area_id=${AREA_ID}&days=${days}`,
    { cache: 'no-store' }
  );
  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);
  }
  return res.json();
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/v1/public/health`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);
  }
  return res.json();
}

const TERRASENSE_BASE =
  process.env.NEXT_PUBLIC_TERRASENSE_URL || "http://localhost:8081";

export async function fetchTerraSenseRecommendation(
  body: import("./types").TerraSenseRecommendRequest
): Promise<import("./types").TerraSenseRecommendationResponse> {
  const res = await fetch(`${TERRASENSE_BASE}/v1/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(
      errorData.error || `TerraSense GeoAI returned status ${res.status}`
    );
  }
  return res.json();
}

export async function fetchTerraSenseHealth(): Promise<Record<string, unknown>> {
  const res = await fetch(`${TERRASENSE_BASE}/health`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`TerraSense health error: ${res.status}`);
  }
  return res.json();
}
