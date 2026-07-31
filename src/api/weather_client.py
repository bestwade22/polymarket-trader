"""Weather forecast client — Open-Meteo primary, resolution-map station coords."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from src.api.city_resolution_map import get_city_entry, load_city_coords, load_resolution_map

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class ForecastMaxTemp:
    temp_f: int
    temp_c: int
    source: str
    icao: Optional[str] = None
    resolution_source: Optional[str] = None


def _f_to_c(temp_f: float) -> int:
    return int(round((float(temp_f) - 32.0) * 5.0 / 9.0))


class WeatherClient:
    def __init__(self):
        self.session = requests.Session()
        self._city_coords = load_city_coords()
        self._resolution_map = load_resolution_map()

    def reload_maps(self) -> None:
        self._city_coords = load_city_coords()
        self._resolution_map = load_resolution_map()

    def get_coords_for_city(self, city: str) -> Optional[tuple[float, float]]:
        entry = get_city_entry(city, self._resolution_map)
        if entry and entry.get("latitude") is not None and entry.get("longitude") is not None:
            return float(entry["latitude"]), float(entry["longitude"])
        known = self._city_coords.get((city or "").lower())
        if known:
            return known["latitude"], known["longitude"]
        return None

    def get_resolution_source(self, city: str, fallback: Optional[str] = None) -> Optional[str]:
        entry = get_city_entry(city, self._resolution_map)
        if entry and entry.get("resolution_source"):
            return str(entry["resolution_source"])
        return fallback

    def fetch_forecast_max_temp(
        self,
        city: str,
        event_date: str,
        resolution_source: Optional[str] = None,
    ) -> Optional[ForecastMaxTemp]:
        """Predicted daily max for event_date (YYYY-MM-DD). Open-Meteo first."""
        entry = get_city_entry(city, self._resolution_map)
        mapped_source = (entry or {}).get("resolution_source") if entry else None
        icao = (entry or {}).get("icao") if entry else None
        source_url = mapped_source or resolution_source

        coords = None
        if entry and entry.get("latitude") is not None and entry.get("longitude") is not None:
            coords = (float(entry["latitude"]), float(entry["longitude"]))
        if coords is None:
            coords = self.get_coords_for_city(city)

        if coords:
            temp_f = self._fetch_open_meteo_max_f(coords[0], coords[1], event_date)
            if temp_f is not None:
                result = ForecastMaxTemp(
                    temp_f=temp_f,
                    temp_c=_f_to_c(temp_f),
                    source="open_meteo",
                    icao=icao,
                    resolution_source=source_url,
                )
                logger.info(
                    "forecast_source=open_meteo city=%s icao=%s date=%s temp_f=%s temp_c=%s",
                    city,
                    icao,
                    event_date,
                    result.temp_f,
                    result.temp_c,
                )
                return result

        # Optional last-resort: scrape Wunderground page (often history, not forecast).
        wu = self._fetch_wunderground_forecast(source_url, event_date)
        if wu is not None:
            logger.info(
                "forecast_source=wunderground_scrape city=%s icao=%s date=%s temp_f=%s",
                city,
                icao,
                event_date,
                wu,
            )
            return ForecastMaxTemp(
                temp_f=wu,
                temp_c=_f_to_c(wu),
                source="wunderground_scrape",
                icao=icao,
                resolution_source=source_url,
            )

        logger.warning(
            "No forecast for city=%s date=%s (no coords / API miss)",
            city,
            event_date,
        )
        return None

    def fetch_forecast_max_temp_f(
        self,
        city: str,
        event_date: str,
        resolution_source: Optional[str] = None,
    ) -> Optional[int]:
        """Backward-compatible: return predicted daily max °F only."""
        result = self.fetch_forecast_max_temp(city, event_date, resolution_source)
        return result.temp_f if result else None

    def _fetch_open_meteo_max_f(
        self,
        lat: float,
        lon: float,
        event_date: str,
    ) -> Optional[int]:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "start_date": event_date,
            "end_date": event_date,
        }
        try:
            resp = self.session.get(OPEN_METEO_URL, params=params, timeout=20)
            resp.raise_for_status()
            daily = resp.json().get("daily", {})
            temps = daily.get("temperature_2m_max", [])
            if not temps or temps[0] is None:
                return None
            return int(round(float(temps[0])))
        except Exception as exc:
            logger.warning("Open-Meteo forecast failed lat=%s lon=%s: %s", lat, lon, exc)
            return None

    def _fetch_wunderground_forecast(
        self, resolution_source: Optional[str], event_date: str
    ) -> Optional[int]:
        del event_date  # history pages are not date-parameterized in this scrape
        if not resolution_source or "wunderground.com" not in resolution_source:
            return None
        try:
            resp = self.session.get(
                resolution_source,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

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
