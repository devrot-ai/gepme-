# Implementation Audit — TerraSense GeoAI (Go Now)

**Date:** 2026-10-24  
**Branch:** `geoai-hackathon`  
**Upstream:** [NitBuk/go-now](https://github.com/NitBuk/go-now) (MIT)  
**Event:** GeoAI Hackathon 2026, CSRE, IIT Bombay — PS-01 *Where & when to head outdoors*

## Original test status (unchanged codebase)

Recorded before TerraSense code was added:

| Suite | Command | Result |
|-------|---------|--------|
| Scoring engine | `cd services/scoring_engine && uv run pytest tests/ -q` | **39 passed** |
| FastAPI | `cd services/api_fastapi && uv run pytest tests/ -q` | **23 passed** |
| Ingest worker | `cd services/ingest_worker && uv run pytest tests/ -q` | **35 passed** |

Licence: MIT, Copyright (c) 2025 NitBuk — preserved.

## Discovered architecture (Go Now V1)

Go Now is a **Tel Aviv coast swim/run scoring product**, not a multi-venue NL assistant.

```
Open-Meteo (+ AQICN for Tel Aviv) → ingest_worker → GCS/BQ/Firestore
                                                     → api_fastapi /v1/public/{forecast,scores,health}
                                                     → Next.js dashboard + iOS app
```

### Reusable modules (do not duplicate)

| Capability | Path | Notes |
|------------|------|-------|
| Open-Meteo fetch + normalize | `services/ingest_worker/provider/open_meteo.py` | Weather, marine, CAMS PM; wind km/h→m/s |
| AQICN ground sensor | `services/ingest_worker/provider/aqicn.py` | Token required; Tel Aviv station `@5783` — **not** suitable as the only AQI source for arbitrary Indian cities |
| Scoring (swim/run, dog gates) | `services/scoring_engine/scoring_engine/engine.py` | Deterministic 0–100, hard gates, linear ramps |
| Thresholds | `services/scoring_engine/scoring_engine/thresholds.py` | Balanced preset |
| Public API | `services/api_fastapi/routers/public.py` | Firestore-backed, `area_id=tel_aviv_coast` only |
| Dashboard | `apps/dashboard_nextjs` | Consumes `/v1/public/scores` |
| Contracts | `services/shared_contracts/python/` | Forecast/scoring DTOs |

### Not present (hackathon gaps)

- Natural-language intent extraction / SLM ≤3B
- Nominatim geocoding, Overpass venue discovery, OSRM routing
- Activity profiles beyond swim/run (need 10 activities)
- Place-and-time ranking across multiple OSM venues
- Roads / industry / water / green-cover spatial analysis
- Wind downwind heuristic vs industry
- Evidence-grounded narrative
- `GET /health` + `POST /v1/recommend` as specified
- Hugging Face model repo, presentation, evaluation set

### What must not be overwritten

- Existing FastAPI public forecast/scores contract
- Firestore ingest pipeline
- iOS app and existing dashboard forecast UX
- MIT licence header and NitBuk attribution

## Planned changes

Add a **new service** `services/terrasense_geoai` (TerraSense GeoAI) that:

1. Loads **Qwen/Qwen2.5-1.5B-Instruct** (~1.54B params, Apache-2.0) once at startup for intent JSON + grounded narration.
2. Geocodes with Nominatim; discovers venues with Overpass; routes with public OSRM.
3. Fetches **Open-Meteo forecast + Open-Meteo air-quality** live per query (no AQICN token; CAMS resolution disclosed).
4. Ranks **venue × hour** combinations with activity-specific weights and non-overridable safety gates.
5. Exposes `GET /health` and `POST /v1/recommend`.
6. Adds a Next.js `/geoai` judge UI with OSM map, without replacing the Tel Aviv forecast home.

Reuse scoring ideas (hard gates, linear ramps, missing-data info chips) but **do not** reuse Tel Aviv-only AQICN or `area_id` lock-in for the assistant.

## Verified SLM configuration (pre-implementation)

Fetched `https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct/raw/main/config.json`:

- `hidden_size` 1536, `num_hidden_layers` 28, `intermediate_size` 8960
- `num_attention_heads` 12, `num_key_value_heads` 2, `vocab_size` 151936
- `tie_word_embeddings` true → ~**1.54B** parameters (under 3B)
- Licence: Apache-2.0 (open)

## Official constraints followed

- Open weights, self-hosted, no GPT/Claude/Gemini in the running assistant
- Live free OSM + Open-Meteo data, no hardcoded example answers
- Honest reporting of missing HF upload / public URL until verified
