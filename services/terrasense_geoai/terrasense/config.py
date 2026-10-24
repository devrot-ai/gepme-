from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TERRASENSE_", env_file=".env", extra="ignore")

    app_name: str = "TerraSense GeoAI"
    log_level: str = "INFO"
    port: int = 8090
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,*"

    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    osrm_url: str = "https://router.project-osrm.org"
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_aq_url: str = "https://air-quality-api.open-meteo.com/v1/air-quality"

    user_agent: str = "TerraSenseGeoAI/0.1 (GeoAI Hackathon CSRE IITB; https://github.com/NitBuk/go-now)"
    http_timeout_s: float = 20.0
    cache_ttl_s: int = 600
    max_candidates: int = 8
    max_hours: int = 12
    top_k: int = 3

    model_id: str = "Qwen/Qwen2.5-1.5B-Instruct"
    model_revision: str = "main"
    load_model: bool = False
    model_device: str = "cpu"
    model_max_new_tokens: int = 256
    quantize: bool = False
    inference_timeout_s: float = 45.0

    request_timeout_s: float = 55.0
    max_concurrency: int = 4


settings = Settings()
