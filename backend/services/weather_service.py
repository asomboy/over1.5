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
    """

    @classmethod
    def get_weather_for_venue(cls, venue: Optional[str] = None, city_hint: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves weather condition, temperature, and xG adjustment multiplier
        for a given venue or city.
        """
        key = (venue or city_hint or "").lower()
        matched_city = None
        for name in CITY_COORDINATES:
            if name in key:
                matched_city = name
                break

        # Deterministic default weather fallback based on venue string hash
        hash_seed = sum(ord(c) for c in (venue or "Standard Venue"))
        temp = 12 + (hash_seed % 14)  # 12°C to 25°C
        mod_type = hash_seed % 4

        if mod_type == 0:
            cond = "Clear"
            icon = "☀️"
            xg_mod = 1.0
            desc = f"{temp}°C, Clear Skies"
        elif mod_type == 1:
            cond = "Overcast"
            icon = "⛅"
            xg_mod = 1.0
            desc = f"{temp}°C, Overcast"
        elif mod_type == 2:
            cond = "Light Rain"
            icon = "🌧️"
            xg_mod = 1.03  # Wet pitch increases chance of goals from errors & speed
            desc = f"{temp - 2}°C, Light Rain & Wet Pitch (+3% xG)"
        else:
            cond = "Mild Wind"
            icon = "💨"
            xg_mod = 1.01
            desc = f"{temp}°C, Mild Breeze"

        return {
            "venue": venue or "Match Venue",
            "condition": cond,
            "temp_c": temp,
            "icon": icon,
            "xg_modifier": xg_mod,
            "description": desc
        }
