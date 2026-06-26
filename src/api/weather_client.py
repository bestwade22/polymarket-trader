import json
import logging
import re
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config.settings import CITY_COORDS_FILE

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherClient:
    def __init__(self):
        self.session = requests.Session()
        self._city_coords = self._load_city_coords()

    def _load_city_coords(self) -> dict[str, dict]:
        if not CITY_COORDS_FILE.exists():
            return {}
        data = json.loads(CITY_COORDS_FILE.read_text())
        return {entry["city"].lower(): entry for entry in data}

    def get_coords_for_city(self, city: str) -> Optional[tuple[float, float]]:
        entry = self._city_coords.get(city.lower())
        if entry:
            return entry["latitude"], entry["longitude"]
        return None

    def fetch_forecast_max_temp_f(
        self,
        city: str,
        event_date: str,
        resolution_source: Optional[str] = None,
    ) -> Optional[int]:
        """Get predicted daily max temp in Fahrenheit for event_date (YYYY-MM-DD)."""
        wunderground_temp = self._fetch_wunderground_forecast(resolution_source, event_date)
        if wunderground_temp is not None:
            return wunderground_temp

        coords = self.get_coords_for_city(city)
        if not coords:
            logger.warning("No coordinates for city %s; cannot fetch Open-Meteo forecast", city)
            return None

        lat, lon = coords
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "start_date": event_date,
            "end_date": event_date,
        }
        resp = self.session.get(OPEN_METEO_URL, params=params, timeout=20)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        temps = daily.get("temperature_2m_max", [])
        if not temps:
            return None
        return int(round(temps[0]))

    def _fetch_wunderground_forecast(
        self, resolution_source: Optional[str], event_date: str
    ) -> Optional[int]:
        if not resolution_source or "wunderground.com" not in resolution_source:
            return None
        try:
            resp = self.session.get(resolution_source, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Look for high temperature in page text or structured elements
            high_patterns = [
                re.compile(r"High[:\s]+(-?\d+)", re.IGNORECASE),
                re.compile(r"max[^0-9]*(-?\d+)\s*°?F", re.IGNORECASE),
            ]
            for pattern in high_patterns:
                match = pattern.search(soup.get_text(" ", strip=True))
                if match:
                    return int(match.group(1))

            for elem in soup.select("[class*='high'], [class*='temp']"):
                text = elem.get_text(strip=True)
                num_match = re.search(r"(-?\d+)", text)
                if num_match:
                    return int(num_match.group(1))
        except Exception as exc:
            logger.warning("Wunderground fetch failed for %s: %s", resolution_source, exc)
        return None
