import os
import sys
import time
import logging
import threading
import httpx
from typing import Dict, Any, Optional, Tuple

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logger = logging.getLogger(__name__)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
WEATHER_CACHE_TTL_SEC = 3600
WEATHER_FAILURE_TTL_SEC = 300
_WEATHER_CACHE_LOCK = threading.Lock()
_WEATHER_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

# City/Coordinates lookup mapping for major football venues
CITY_COORDINATES: Dict[str, Dict[str, Any]] = {
    "london": {"lat": 51.5074, "lon": -0.1278, "city": "London"},
    "manchester": {"lat": 53.4808, "lon": -2.2426, "city": "Manchester"},
    "liverpool": {"lat": 53.4084, "lon": -2.9916, "city": "Liverpool"},
    "birmingham": {"lat": 52.4862, "lon": -1.8904, "city": "Birmingham"},
    "newcastle": {"lat": 54.9783, "lon": -1.6178, "city": "Newcastle"},
    "glasgow": {"lat": 55.8642, "lon": -4.2518, "city": "Glasgow"},
    "madrid": {"lat": 40.4168, "lon": -3.7038, "city": "Madrid"},
    "barcelona": {"lat": 41.3851, "lon": 2.1734, "city": "Barcelona"},
    "seville": {"lat": 37.3891, "lon": -5.9845, "city": "Seville"},
    "sevilla": {"lat": 37.3891, "lon": -5.9845, "city": "Seville"},
    "bilbao": {"lat": 43.2630, "lon": -2.9350, "city": "Bilbao"},
    "valencia": {"lat": 39.4699, "lon": -0.3763, "city": "Valencia"},
    "munich": {"lat": 48.1351, "lon": 11.5820, "city": "Munich"},
    "dortmund": {"lat": 51.5136, "lon": 7.4653, "city": "Dortmund"},
    "leverkusen": {"lat": 51.0459, "lon": 6.9853, "city": "Leverkusen"},
    "leipzig": {"lat": 51.3397, "lon": 12.3731, "city": "Leipzig"},
    "frankfurt": {"lat": 50.1109, "lon": 8.6821, "city": "Frankfurt"},
    "milan": {"lat": 45.4642, "lon": 9.1900, "city": "Milan"},
    "rome": {"lat": 41.9028, "lon": 12.4964, "city": "Rome"},
    "naples": {"lat": 40.8518, "lon": 14.2681, "city": "Naples"},
    "napoli": {"lat": 40.8518, "lon": 14.2681, "city": "Naples"},
    "turin": {"lat": 45.0703, "lon": 7.6869, "city": "Turin"},
    "paris": {"lat": 48.8566, "lon": 2.3522, "city": "Paris"},
    "lyon": {"lat": 45.7640, "lon": 4.8357, "city": "Lyon"},
    "marseille": {"lat": 43.2965, "lon": 5.3698, "city": "Marseille"},
    "amsterdam": {"lat": 52.3676, "lon": 4.9041, "city": "Amsterdam"},
    "lisbon": {"lat": 38.7223, "lon": -9.1393, "city": "Lisbon"},
    "porto": {"lat": 41.1579, "lon": -8.6291, "city": "Porto"},
    "istanbul": {"lat": 41.0082, "lon": 28.9784, "city": "Istanbul"},
    "buenos aires": {"lat": -34.6037, "lon": -58.3816, "city": "Buenos Aires"},
    "sao paulo": {"lat": -23.5505, "lon": -46.6333, "city": "Sao Paulo"},
    "rio de janeiro": {"lat": -22.9068, "lon": -43.1729, "city": "Rio de Janeiro"},
}


class WeatherService:
    """
    Environmental & Weather context service for fixture goal expectations.
    Uses thread-safe TTL caching around live OpenWeather telemetry when configured,
    preventing blocking per-row HTTP calls inside read endpoints.
    """

    @staticmethod
    def _neutral_payload(venue: Optional[str]) -> Dict[str, Any]:
        return {
            "available": False,
            "source": "unavailable",
            "venue": venue or "Match Venue",
            "condition": "Unavailable",
            "temp_c": None,
            "icon": "🏟️",
            "xg_modifier": 1.0,
            "description": "Live weather feed unavailable (neutral xG applied)",
        }

    @classmethod
    def clear_cache(cls) -> None:
        with _WEATHER_CACHE_LOCK:
            _WEATHER_CACHE.clear()

    @classmethod
    def get_weather_for_venue(
        cls,
        venue: Optional[str] = None,
        city_hint: Optional[str] = None,
        fetch_remote: bool = True,
    ) -> Dict[str, Any]:
        """
        Retrieves real weather telemetry if configured for known venues/cities.
        When fetch_remote=False (e.g. inside bulk read loops), serves strictly from TTL cache.
        """
        key = (venue or city_hint or "").lower()
        matched_city = None
        for name in CITY_COORDINATES:
            if name in key:
                matched_city = name
                break

        api_key = (os.getenv("OPENWEATHER_API_KEY", "") or OPENWEATHER_API_KEY).strip()
        if not api_key or not matched_city:
            return cls._neutral_payload(venue)

        now_ts = time.monotonic()
        with _WEATHER_CACHE_LOCK:
            cached = _WEATHER_CACHE.get(matched_city)
            if cached and cached[0] > now_ts:
                out = dict(cached[1])
                if venue:
                    out["venue"] = venue
                return out

        if not fetch_remote:
            return cls._neutral_payload(venue)

        coords = CITY_COORDINATES[matched_city]
        try:
            resp = httpx.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"lat": coords["lat"], "lon": coords["lon"], "appid": api_key, "units": "metric"},
                timeout=2.5,
            )
            if resp.status_code == 200:
                data = resp.json()
                main_info = data.get("main", {})
                weather_list = data.get("weather") or [{}]
                cond = str(weather_list[0].get("main") or "Observed")
                temp_raw = main_info.get("temp")
                temp = round(float(temp_raw), 1) if temp_raw is not None else None
                wind_speed = float((data.get("wind") or {}).get("speed") or 0.0)
                is_storm = cond.lower() in {"thunderstorm", "squall"} or wind_speed >= 11.0
                is_wet = cond.lower() in {"rain", "drizzle"}
                xg_mod = 0.96 if is_storm else (1.02 if is_wet else 1.0)
                icon = "⛈️" if is_storm else ("🌧️" if is_wet else ("⛅" if "cloud" in cond.lower() else "☀️"))
                temp_str = f"{temp}°C, " if temp is not None else ""
                payload = {
                    "available": True,
                    "source": "openweathermap",
                    "venue": venue or coords["city"],
                    "condition": cond,
                    "temp_c": temp,
                    "icon": icon,
                    "xg_modifier": xg_mod,
                    "description": f"{temp_str}{cond}",
                }
                with _WEATHER_CACHE_LOCK:
                    _WEATHER_CACHE[matched_city] = (now_ts + WEATHER_CACHE_TTL_SEC, dict(payload))
                return payload
        except Exception as ex:
            logger.debug(f"OpenWeather lookup failed for {matched_city}: {ex}")

        fallback = cls._neutral_payload(venue)
        with _WEATHER_CACHE_LOCK:
            _WEATHER_CACHE[matched_city] = (now_ts + WEATHER_FAILURE_TTL_SEC, dict(fallback))
        return fallback
