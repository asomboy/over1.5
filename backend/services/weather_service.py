import os
import sys
import logging
import httpx
from typing import Dict, Any, Optional

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logger = logging.getLogger(__name__)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

# City/Coordinates lookup mapping for major venues
CITY_COORDINATES: Dict[str, Dict[str, Any]] = {
    "london": {"lat": 51.5074, "lon": -0.1278, "city": "London"},
    "manchester": {"lat": 53.4808, "lon": -2.2426, "city": "Manchester"},
    "liverpool": {"lat": 53.4084, "lon": -2.9916, "city": "Liverpool"},
    "madrid": {"lat": 40.4168, "lon": -3.7038, "city": "Madrid"},
    "barcelona": {"lat": 41.3851, "lon": 2.1734, "city": "Barcelona"},
    "munich": {"lat": 48.1351, "lon": 11.5820, "city": "Munich"},
    "milan": {"lat": 45.4642, "lon": 9.1900, "city": "Milan"},
    "rome": {"lat": 41.9028, "lon": 12.4964, "city": "Rome"},
    "paris": {"lat": 48.8566, "lon": 2.3522, "city": "Paris"},
    "dortmund": {"lat": 51.5136, "lon": 7.4653, "city": "Dortmund"},
}


class WeatherService:
    """
    Environmental & Weather context service for fixture goal expectations.
    Uses live OpenWeather telemetry when configured, and otherwise returns
    neutral/unavailable metadata without synthetic random fabrication.
    """

    @classmethod
    def get_weather_for_venue(cls, venue: Optional[str] = None, city_hint: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves real weather telemetry if configured for known venues/cities.
        When unavailable, returns neutral xG multiplier (1.0) and available=False.
        """
        key = (venue or city_hint or "").lower()
        matched_city = None
        for name in CITY_COORDINATES:
            if name in key:
                matched_city = name
                break

        api_key = (os.getenv("OPENWEATHER_API_KEY", "") or OPENWEATHER_API_KEY).strip()
        if api_key and matched_city:
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
                    is_wet = cond.lower() in {"rain", "drizzle", "thunderstorm"}
                    xg_mod = 1.02 if is_wet else 1.0
                    icon = "🌧️" if is_wet else ("⛅" if "cloud" in cond.lower() else "☀️")
                    temp_str = f"{temp}°C, " if temp is not None else ""
                    return {
                        "available": True,
                        "source": "openweathermap",
                        "venue": venue or coords["city"],
                        "condition": cond,
                        "temp_c": temp,
                        "icon": icon,
                        "xg_modifier": xg_mod,
                        "description": f"{temp_str}{cond}",
                    }
            except Exception as ex:
                logger.debug(f"OpenWeather lookup failed for {matched_city}: {ex}")

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
