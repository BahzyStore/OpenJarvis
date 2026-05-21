"""FastAPI router for /v1/weather — lightweight current-weather endpoint.

Reads the OpenWeatherMap API key from the existing WeatherConnector config
file (``~/.config/openjarvis/connectors/weather.json``) so it shares
credentials with the connector. Returns a small dict optimized for HUD
display (no Document overhead).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

try:
    from fastapi import APIRouter, HTTPException
except ImportError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_DEFAULT_LOCATION = "Dubai,AE"
_DEFAULT_UNITS = "metric"  # °C
_CACHE_TTL_SEC = 600  # 10 minutes — OpenWeather free tier limit-friendly
_cache: Dict[str, Any] = {"key": None, "data": None, "ts": 0.0}


def _config_path() -> Path:
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return DEFAULT_CONFIG_DIR / "connectors" / "weather.json"


def _load_api_key() -> str:
    path = _config_path()
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "OpenWeatherMap API key not configured. "
                f"Create {path} with {{\"api_key\": \"...\", \"location\": \"Dubai,AE\"}}"
            ),
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to read weather config: {exc}",
        )
    api_key = data.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="api_key missing from weather.json",
        )
    return api_key


def _load_default_location() -> str:
    """Best-effort: prefer the location from weather.json, else built-in default."""
    path = _config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            loc = data.get("location", "").strip()
            if loc:
                return loc
        except (json.JSONDecodeError, OSError):
            pass
    return _DEFAULT_LOCATION


def create_weather_router():
    """Return an APIRouter exposing /v1/weather/current."""
    if APIRouter is None:
        raise ImportError("fastapi is required for the weather router")

    router = APIRouter(prefix="/v1/weather", tags=["weather"])

    @router.get("/current")
    async def current_weather(
        location: str = "",
        units: str = _DEFAULT_UNITS,
    ) -> Dict[str, Any]:
        """Return current weather for *location* (default Dubai,AE).

        Response shape:
        {
          "location": "Dubai,AE",
          "temp": 36.1,
          "temp_unit": "C",
          "description": "clear sky",
          "humidity": 48,
          "wind_speed": 3.6,
          "icon": "01d",
          "cached": false
        }
        """
        import time

        import httpx

        loc = location.strip() or _load_default_location()
        units_norm = "imperial" if units.lower() in ("imperial", "f") else "metric"
        cache_key = f"{loc}::{units_norm}"

        # Serve from cache if fresh
        now = time.time()
        if (
            _cache["key"] == cache_key
            and _cache["data"] is not None
            and (now - _cache["ts"]) < _CACHE_TTL_SEC
        ):
            cached = dict(_cache["data"])
            cached["cached"] = True
            return cached

        api_key = _load_api_key()

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    "https://api.openweathermap.org/data/2.5/weather",
                    params={"q": loc, "appid": api_key, "units": units_norm},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"OpenWeather request failed: {exc}")

        if resp.status_code == 401:
            raise HTTPException(
                status_code=503,
                detail="OpenWeather rejected the API key (401). Check the key.",
            )
        if resp.status_code == 404:
            raise HTTPException(
                status_code=422,
                detail=f"Location not found: '{loc}'. Try 'City,CountryCode' (e.g. 'Dubai,AE').",
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=503,
                detail=f"OpenWeather error {resp.status_code}: {resp.text[:200]}",
            )

        data = resp.json()
        main = data.get("main", {})
        wlist = data.get("weather", [])
        primary_w = wlist[0] if wlist else {}

        result = {
            "location": loc,
            "temp": main.get("temp"),
            "temp_unit": "C" if units_norm == "metric" else "F",
            "feels_like": main.get("feels_like"),
            "description": primary_w.get("description", ""),
            "humidity": main.get("humidity"),
            "wind_speed": (data.get("wind") or {}).get("speed"),
            "icon": primary_w.get("icon", ""),
            "cached": False,
        }

        _cache["key"] = cache_key
        _cache["data"] = result
        _cache["ts"] = now
        return result

    @router.get("/health")
    async def weather_health() -> Dict[str, Any]:
        """Lightweight health check — tells the HUD whether weather is configured."""
        path = _config_path()
        if not path.exists():
            return {"available": False, "reason": f"{path} not found"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("api_key", "").strip():
                return {"available": False, "reason": "api_key missing"}
            return {
                "available": True,
                "location": data.get("location", _DEFAULT_LOCATION),
            }
        except (json.JSONDecodeError, OSError) as exc:
            return {"available": False, "reason": str(exc)}

    return router
