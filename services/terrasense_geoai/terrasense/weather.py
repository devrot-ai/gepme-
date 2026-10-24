from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from terrasense.config import settings
from terrasense.http_util import cached_get_json, cached_get_json_sync
from terrasense.schemas import HourlyConditions, Provenance

HOURLY_WEATHER = ",".join(
    [
        "temperature_2m",
        "apparent_temperature",
        "precipitation",
        "precipitation_probability",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_gusts_10m",
        "uv_index",
        "is_day",
        "weather_code",
    ]
)
HOURLY_AQ = "european_aqi,us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide"


def grid_key(lat: float, lon: float) -> tuple[float, float]:
    """Snap to ~5 km grid so nearby parks share one CAMS/GFS cell."""
    return (round(lat * 20) / 20, round(lon * 20) / 20)


def _index(hourly: dict[str, list], field: str, i: int) -> Any:
    values = hourly.get(field) or []
    if i >= len(values):
        return None
    return values[i]


def _missing(hour: HourlyConditions) -> list[str]:
    fields = {
        "temperature_c": hour.temperature_c,
        "precipitation_mm": hour.precipitation_mm,
        "wind_speed_kmh": hour.wind_speed_kmh,
        "wind_direction_deg": hour.wind_direction_deg,
        "uv_index": hour.uv_index,
        "european_aqi": hour.european_aqi,
        "pm2_5": hour.pm2_5,
    }
    return [k for k, v in fields.items() if v is None]


async def fetch_forecast(lat: float, lon: float) -> tuple[list[HourlyConditions], str, list[Provenance], list[str]]:
    glat, glon = grid_key(lat, lon)
    now = datetime.now(UTC).isoformat()
    notes: list[str] = []
    provenances: list[Provenance] = []

    wurl = settings.open_meteo_url
    wparams = {
        "latitude": glat,
        "longitude": glon,
        "hourly": HOURLY_WEATHER,
        "daily": "sunrise,sunset",
        "forecast_days": 7,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    }
    aurl = settings.open_meteo_aq_url
    aparams = {
        "latitude": glat,
        "longitude": glon,
        "hourly": HOURLY_AQ,
        "forecast_days": 7,
        "timezone": "auto",
    }
    weather = await cached_get_json(wurl, wparams, cache_key=f"omw:{glat}:{glon}")
    aq = await cached_get_json(aurl, aparams, cache_key=f"omaq:{glat}:{glon}")
    return _merge(weather, aq, now, notes, provenances)


def fetch_forecast_sync(lat: float, lon: float) -> tuple[list[HourlyConditions], str, list[Provenance], list[str]]:
    glat, glon = grid_key(lat, lon)
    now = datetime.now(UTC).isoformat()
    notes: list[str] = []
    provenances: list[Provenance] = []
    wparams = {
        "latitude": glat,
        "longitude": glon,
        "hourly": HOURLY_WEATHER,
        "daily": "sunrise,sunset",
        "forecast_days": 7,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    }
    aparams = {
        "latitude": glat,
        "longitude": glon,
        "hourly": HOURLY_AQ,
        "forecast_days": 7,
        "timezone": "auto",
    }
    weather = cached_get_json_sync(settings.open_meteo_url, wparams, cache_key=f"omw:{glat}:{glon}")
    aq = cached_get_json_sync(settings.open_meteo_aq_url, aparams, cache_key=f"omaq:{glat}:{glon}")
    return _merge(weather, aq, now, notes, provenances)


def _merge(
    weather: dict | None,
    aq: dict | None,
    now: str,
    notes: list[str],
    provenances: list[Provenance],
) -> tuple[list[HourlyConditions], str, list[Provenance], list[str]]:
    tz = "UTC"
    if weather is None:
        notes.append("Weather provider failed; no forecast values were invented.")
        provenances.append(
            Provenance(source="Open-Meteo forecast", fetched_at_utc=now, failed=True, missing=True)
        )
        if aq is None:
            provenances.append(
                Provenance(source="Open-Meteo air-quality", fetched_at_utc=now, failed=True, missing=True)
            )
        return [], tz, provenances, notes

    tz = weather.get("timezone") or "UTC"
    hourly = weather.get("hourly") or {}
    times = hourly.get("time") or []
    daily = weather.get("daily") or {}
    sunrise_map: dict[str, str] = {}
    sunset_map: dict[str, str] = {}
    for i, d in enumerate(daily.get("time") or []):
        if i < len(daily.get("sunrise") or []):
            sunrise_map[d] = daily["sunrise"][i]
        if i < len(daily.get("sunset") or []):
            sunset_map[d] = daily["sunset"][i]

    aq_hourly = (aq or {}).get("hourly") or {}
    aq_idx = {t: i for i, t in enumerate(aq_hourly.get("time") or [])}

    if aq is None:
        notes.append("Air-quality provider failed; AQI/PM values are missing, not guessed.")
        provenances.append(
            Provenance(source="Open-Meteo air-quality", fetched_at_utc=now, failed=True, missing=True)
        )
    else:
        notes.append(
            "Air quality is a CAMS-scale model (~10 km), not a park-level sensor. "
            "Nearby venues often share the same cell."
        )
        provenances.append(
            Provenance(
                source="Open-Meteo air-quality (CAMS)",
                fetched_at_utc=now,
                units={"pm2_5": "µg/m³", "european_aqi": "EAQI"},
            )
        )

    provenances.append(
        Provenance(
            source="Open-Meteo forecast",
            fetched_at_utc=now,
            units={"temperature_2m": "°C", "wind_speed_10m": "km/h", "precipitation": "mm"},
        )
    )

    hours: list[HourlyConditions] = []
    for i, t in enumerate(times):
        date_key = t[:10]
        j = aq_idx.get(t)
        hour = HourlyConditions(
            time_local=t,
            time_utc=t,
            temperature_c=_index(hourly, "temperature_2m", i),
            apparent_temperature_c=_index(hourly, "apparent_temperature", i),
            precipitation_mm=_index(hourly, "precipitation", i),
            precipitation_probability_pct=_index(hourly, "precipitation_probability", i),
            wind_speed_kmh=_index(hourly, "wind_speed_10m", i),
            wind_direction_deg=_index(hourly, "wind_direction_10m", i),
            wind_gusts_kmh=_index(hourly, "wind_gusts_10m", i),
            uv_index=_index(hourly, "uv_index", i),
            is_day=bool(_index(hourly, "is_day", i)) if _index(hourly, "is_day", i) is not None else None,
            weather_code=_index(hourly, "weather_code", i),
            european_aqi=_index(aq_hourly, "european_aqi", j) if j is not None else None,
            us_aqi=_index(aq_hourly, "us_aqi", j) if j is not None else None,
            pm2_5=_index(aq_hourly, "pm2_5", j) if j is not None else None,
            pm10=_index(aq_hourly, "pm10", j) if j is not None else None,
            ozone=_index(aq_hourly, "ozone", j) if j is not None else None,
            nitrogen_dioxide=_index(aq_hourly, "nitrogen_dioxide", j) if j is not None else None,
            sunrise=sunrise_map.get(date_key),
            sunset=sunset_map.get(date_key),
            fetched_at_utc=now,
        )
        hour.missing_fields = _missing(hour)
        hours.append(hour)
    return hours, tz, provenances, notes
