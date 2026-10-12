"""Weather from Open-Meteo (https://open-meteo.com): free, no API key.

Data is cached in memory for 15 minutes per location, so the server does not
hit the API on every page load. Attribution: Open-Meteo, CC BY 4.0.
"""
from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import urlencode

from .sources import SourceError, http_get

OPEN_METEO = os.environ.get("DAILYHUB_WEATHER_API", "https://api.open-meteo.com/v1/forecast").rstrip("/")
CACHE_SECONDS = 15 * 60
HOURS_AHEAD = 6

# WMO weather codes (as used by Open-Meteo) -> Vietnamese labels.
WMO = {
    0: "Trời quang", 1: "Ít mây", 2: "Có mây rải rác", 3: "Nhiều mây",
    45: "Sương mù", 48: "Sương mù đóng băng",
    51: "Mưa phùn nhẹ", 53: "Mưa phùn", 55: "Mưa phùn dày", 56: "Mưa phùn lạnh", 57: "Mưa phùn lạnh dày",
    61: "Mưa nhẹ", 63: "Mưa vừa", 65: "Mưa to", 66: "Mưa lạnh nhẹ", 67: "Mưa lạnh to",
    71: "Tuyết nhẹ", 73: "Tuyết vừa", 75: "Tuyết to", 77: "Hạt tuyết",
    80: "Mưa rào nhẹ", 81: "Mưa rào", 82: "Mưa rào to", 85: "Tuyết rào nhẹ", 86: "Tuyết rào to",
    95: "Dông", 96: "Dông kèm mưa đá nhỏ", 99: "Dông kèm mưa đá lớn",
}

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def describe(code) -> str:
    try:
        return WMO.get(int(code), "Không xác định")
    except (TypeError, ValueError):
        return "Không xác định"


def parse_forecast(raw: dict, now_local_iso: str | None = None) -> dict:
    """Turn Open-Meteo JSON into the small structure the UI shows. Missing fields become None."""
    cur = raw.get("current") or {}
    hourly = raw.get("hourly") or {}
    times = hourly.get("time") or []
    start = 0
    if now_local_iso:
        for i, t in enumerate(times):
            if t >= now_local_iso[:16]:
                start = i
                break
    upcoming = []
    for i in range(start, min(start + HOURS_AHEAD, len(times))):
        upcoming.append({
            "time": times[i],
            "temperature": _at(hourly.get("temperature_2m"), i),
            "rain_probability": _at(hourly.get("precipitation_probability"), i),
            "description": describe(_at(hourly.get("weather_code"), i)),
        })
    return {
        "timezone": raw.get("timezone"),
        "current": {
            "time": cur.get("time"),
            "temperature": cur.get("temperature_2m"),
            "feels_like": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"),
            "precipitation": cur.get("precipitation"),
            "wind_speed": cur.get("wind_speed_10m"),
            "description": describe(cur.get("weather_code")),
            "units": {"temperature": (raw.get("current_units") or {}).get("temperature_2m", "°C"),
                      "wind": (raw.get("current_units") or {}).get("wind_speed_10m", "km/h")},
        },
        "upcoming": upcoming,
        "attribution": "Dữ liệu thời tiết: Open-Meteo.com (CC BY 4.0)",
    }


def _at(seq, i):
    try:
        return seq[i]
    except (TypeError, IndexError):
        return None


def fetch_forecast(lat: float, lon: float, tz: str, *, sleep=time.sleep) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "timezone": tz,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "forecast_hours": 24,
    }
    status, _h, body = http_get(OPEN_METEO + "?" + urlencode(params), retries=1, backoff=1.0, sleep=sleep, timeout=10)
    if status != 200:
        raise SourceError(f"Open-Meteo trả về HTTP {status}")
    try:
        raw = json.loads(body.decode("utf-8"))
    except ValueError as err:
        raise SourceError("phản hồi thời tiết không đúng định dạng") from err
    now_local = (raw.get("current") or {}).get("time")
    return parse_forecast(raw, now_local)


def get_weather(location: dict, *, force: bool = False, sleep=time.sleep) -> dict:
    """Cached weather for a location. Raises SourceError if the upstream fails and no cache exists."""
    key = (round(float(location["latitude"]), 4), round(float(location["longitude"]), 4), location.get("timezone", "auto"))
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and not force and now - hit[0] < CACHE_SECONDS:
        return dict(hit[1], cached=True, location=location)
    try:
        data = fetch_forecast(key[0], key[1], key[2], sleep=sleep)
    except SourceError:
        if hit:  # stale data is better than nothing; the UI shows the age
            return dict(hit[1], cached=True, stale=True, location=location)
        raise
    with _cache_lock:
        _cache[key] = (now, data)
    return dict(data, cached=False, location=location)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
