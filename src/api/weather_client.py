"""Weather forecast client — Open-Meteo primary, WU scrape also recorded for compare."""

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
    """Primary forecast (Open-Meteo when available) plus optional WU scrape for compare."""

    temp_f: int
    temp_c: int
    source: str
    icao: Optional[str] = None
    resolution_source: Optional[str] = None
    wu_temp_f: Optional[int] = None
    wu_temp_c: Optional[int] = None


def _f_to_c(temp_f: float) -> int:
    return int(round((float(temp_f) - 32.0) * 5.0 / 9.0))


def _c_to_f(temp_c: float) -> int:
    return int(round(float(temp_c) * 9.0 / 5.0 + 32.0))


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

    def get_city_units(self, city: str) -> str:
        entry = get_city_entry(city, self._resolution_map)
        units = str((entry or {}).get("units") or "C").upper()
        return "F" if units.startswith("F") else "C"

    def fetch_forecast_max_temp(
        self,
        city: str,
        event_date: str,
        resolution_source: Optional[str] = None,
    ) -> Optional[ForecastMaxTemp]:
        """Predicted daily max for event_date (YYYY-MM-DD).

        Always attempts Open-Meteo and Wunderground scrape when possible so both
        can be stored for analysis compare. Primary fields prefer Open-Meteo.
        """
        entry = get_city_entry(city, self._resolution_map)
        mapped_source = (entry or {}).get("resolution_source") if entry else None
        icao = (entry or {}).get("icao") if entry else None
        source_url = mapped_source or resolution_source
        map_units = self.get_city_units(city)

        coords = None
        if entry and entry.get("latitude") is not None and entry.get("longitude") is not None:
            coords = (float(entry["latitude"]), float(entry["longitude"]))
        if coords is None:
            coords = self.get_coords_for_city(city)

        om_f: Optional[int] = None
        if coords:
            om_f = self._fetch_open_meteo_max_f(coords[0], coords[1], event_date)
            if om_f is not None:
                logger.info(
                    "forecast_source=open_meteo city=%s icao=%s date=%s temp_f=%s temp_c=%s",
                    city,
                    icao,
                    event_date,
                    om_f,
                    _f_to_c(om_f),
                )

        wu_f: Optional[int] = None
        wu_c: Optional[int] = None
        wu = self._fetch_wunderground_forecast(source_url, event_date, default_unit=map_units)
        if wu is not None:
            wu_temp, wu_unit = wu
            if wu_unit == "C":
                wu_c = int(wu_temp)
                wu_f = _c_to_f(wu_temp)
            else:
                wu_f = int(wu_temp)
                wu_c = _f_to_c(wu_temp)
            logger.info(
                "forecast_source=wunderground_scrape city=%s icao=%s date=%s "
                "temp_f=%s temp_c=%s raw_unit=%s",
                city,
                icao,
                event_date,
                wu_f,
                wu_c,
                wu_unit,
            )

        if om_f is not None:
            return ForecastMaxTemp(
                temp_f=om_f,
                temp_c=_f_to_c(om_f),
                source="open_meteo",
                icao=icao,
                resolution_source=source_url,
                wu_temp_f=wu_f,
                wu_temp_c=wu_c,
            )

        if wu_f is not None and wu_c is not None:
            return ForecastMaxTemp(
                temp_f=wu_f,
                temp_c=wu_c,
                source="wunderground_scrape",
                icao=icao,
                resolution_source=source_url,
                wu_temp_f=wu_f,
                wu_temp_c=wu_c,
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
        self,
        resolution_source: Optional[str],
        event_date: str,
        *,
        default_unit: str = "F",
    ) -> Optional[tuple[int, str]]:
        """Scrape a High temp from the WU page. Returns (temp, unit) or None."""
        del event_date  # history pages are not date-parameterized in this scrape
        if not resolution_source or "wunderground.com" not in resolution_source:
            return None
        unit_default = "F" if str(default_unit).upper().startswith("F") else "C"
        try:
            resp = self.session.get(
                resolution_source,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)

            high_patterns = [
                re.compile(r"High[:\s]+(-?\d+)\s*°?\s*([FC])\b", re.IGNORECASE),
                re.compile(r"max[^0-9]*(-?\d+)\s*°?\s*([FC])\b", re.IGNORECASE),
                re.compile(r"High[:\s]+(-?\d+)", re.IGNORECASE),
                re.compile(r"max[^0-9]*(-?\d+)\s*°?", re.IGNORECASE),
            ]
            for pattern in high_patterns:
                match = pattern.search(page_text)
                if not match:
                    continue
                temp = int(match.group(1))
                if match.lastindex and match.lastindex >= 2 and match.group(2):
                    unit = match.group(2).upper()
                else:
                    unit = unit_default
                return temp, unit

            for elem in soup.select("[class*='high'], [class*='temp']"):
                text = elem.get_text(strip=True)
                unit_match = re.search(r"(-?\d+)\s*°?\s*([FC])\b", text, re.IGNORECASE)
                if unit_match:
                    return int(unit_match.group(1)), unit_match.group(2).upper()
                num_match = re.search(r"(-?\d+)", text)
                if num_match:
                    return int(num_match.group(1)), unit_default
        except Exception as exc:
            logger.warning("Wunderground fetch failed for %s: %s", resolution_source, exc)
        return None
