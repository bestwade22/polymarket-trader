"""Weather forecast client — Weather.com prediction + WU hourly scrape compare."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from src.api.city_resolution_map import get_city_entry, load_city_coords, load_resolution_map

logger = logging.getLogger(__name__)

# Predicted daily max (not observed/archive). Weather.com powers Wunderground forecasts.
WEATHER_COM_DAILY_URL = "https://api.weather.com/v3/wx/forecast/daily/15day"
# Public WU/TWC frontend key (same one embedded on wunderground.com pages).
WEATHER_COM_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"


@dataclass
class ForecastMaxTemp:
    """Predicted daily max plus optional WU hourly-page scrape for compare.

    Primary is Weather.com calendar-day high (forward forecast). WU hourly narrative
    is recorded separately for compare; used as primary only if Weather.com misses.
    """

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


# Narrative from WU hourly day summary, e.g.:
# "Scattered thunderstorms ... High around 30C. Winds W ..."
# "High near 85F." / "High 86F."
_WU_HIGH_PATTERNS = (
    re.compile(r"High\s+around\s+(-?\d+)\s*°?\s*([CF])\b", re.IGNORECASE),
    re.compile(r"High\s+near\s+(-?\d+)\s*°?\s*([CF])\b", re.IGNORECASE),
    re.compile(r"High\s+(-?\d+)\s*°?\s*([CF])\b", re.IGNORECASE),
    re.compile(r"High[:\s]+(-?\d+)\s*°?\s*([CF])\b", re.IGNORECASE),
    re.compile(r"High\s+around\s+(-?\d+)\b", re.IGNORECASE),
    re.compile(r"High\s+near\s+(-?\d+)\b", re.IGNORECASE),
    re.compile(r"High[:\s]+(-?\d+)\b", re.IGNORECASE),
)


def hourly_forecast_url(resolution_source: str) -> Optional[str]:
    """Map a WU history/resolution URL to the hourly forecast page.

    Example:
      https://www.wunderground.com/history/daily/us/ga/atlanta/KATL
      → https://www.wunderground.com/hourly/us/ga/atlanta/KATL
    """
    if not resolution_source or "wunderground.com" not in resolution_source:
        return None
    url = resolution_source.strip()
    if "/hourly/" in url:
        return url.split("?")[0].rstrip("/")
    if "/history/daily/" in url:
        return url.replace("/history/daily/", "/hourly/", 1).split("?")[0].rstrip("/")
    # Generic: .../wunderground.com/<anything>/<icao> → keep path, swap history→hourly if present
    if "/history/" in url:
        return re.sub(r"/history/[^/]+/", "/hourly/", url, count=1).split("?")[0].rstrip("/")
    return url.split("?")[0].rstrip("/")


def _mmdd_from_event_date(event_date: str) -> Optional[str]:
    """YYYY-MM-DD → MM/DD for matching WU day headers like 'Today 08/03'."""
    m = re.match(r"^\d{4}-(\d{2})-(\d{2})$", (event_date or "").strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def _extract_today_forecast_block(page_text: str, event_date: str = "") -> str:
    """Prefer the 'Today MM/DD …' narrative (or matching event date) over full page."""
    text = page_text or ""
    mmdd = _mmdd_from_event_date(event_date)

    # Today 08/03 … High … Tonight / next section
    today_re = re.compile(
        r"Today\s+(\d{2}/\d{2})\s*(.{0,600}?)(?=\s+Tonight\b|\s+Next Day\b|\s+Sun\b|$)",
        re.IGNORECASE | re.DOTALL,
    )
    m = today_re.search(text)
    if m:
        if not mmdd or m.group(1) == mmdd:
            return f"Today {m.group(1)} {m.group(2)}"

    if mmdd:
        dated = re.search(
            rf"(?:Today|Tonight|Mon|Tue|Wed|Thu|Fri|Sat|Sun)?\s*{re.escape(mmdd)}\s+"
            rf"(.{{0,600}}?)(?=\s+(?:Tonight|Today|Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b|\s+\d{{2}}/\d{{2}}\b|$)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if dated:
            return dated.group(0)

    return text


def parse_wu_high_from_text(
    page_text: str,
    *,
    event_date: str = "",
    default_unit: str = "F",
) -> Optional[tuple[int, str]]:
    """Parse daily high from WU hourly page narrative. Returns (temp, unit)."""
    unit_default = "F" if str(default_unit).upper().startswith("F") else "C"
    block = _extract_today_forecast_block(page_text, event_date)
    for pattern in _WU_HIGH_PATTERNS:
        match = pattern.search(block)
        if not match:
            continue
        temp = int(match.group(1))
        if match.lastindex and match.lastindex >= 2 and match.group(2):
            unit = match.group(2).upper()
        else:
            unit = unit_default
        return temp, unit
    return None


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

        Primary: Weather.com daily forecast high for the station ICAO.
        Also scrapes WU hourly narrative for wu_* compare; that scrape is the
        fallback primary if Weather.com has no row for the date.
        """
        entry = get_city_entry(city, self._resolution_map)
        mapped_source = (entry or {}).get("resolution_source") if entry else None
        icao = (entry or {}).get("icao") if entry else None
        if icao is not None:
            icao = str(icao).upper()
        source_url = mapped_source or resolution_source
        map_units = self.get_city_units(city)

        primary_f: Optional[int] = None
        primary_c: Optional[int] = None
        primary_source: Optional[str] = None

        if icao:
            wc = self._fetch_weather_com_forecast_max(icao, event_date, units=map_units)
            if wc is not None:
                primary_c, primary_f = wc
                primary_source = "weather_com"
                logger.info(
                    "forecast_source=weather_com city=%s icao=%s date=%s "
                    "temp_f=%s temp_c=%s",
                    city,
                    icao,
                    event_date,
                    primary_f,
                    primary_c,
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

        if primary_f is not None and primary_c is not None and primary_source:
            return ForecastMaxTemp(
                temp_f=primary_f,
                temp_c=primary_c,
                source=primary_source,
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
            "No forecast for city=%s date=%s (Weather.com / WU miss)",
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

    def _fetch_weather_com_forecast_max(
        self,
        icao: str,
        event_date: str,
        *,
        units: str = "C",
    ) -> Optional[tuple[int, int]]:
        """Predicted calendar-day high from Weather.com. Returns (temp_c, temp_f)."""
        twc_units = "m" if str(units).upper().startswith("C") else "e"
        params = {
            "apiKey": WEATHER_COM_API_KEY,
            "icaoCode": icao,
            "units": twc_units,
            "language": "en-US",
            "format": "json",
        }
        try:
            resp = self.session.get(WEATHER_COM_DAILY_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            times = data.get("validTimeLocal") or []
            highs = data.get("calendarDayTemperatureMax") or []
            target = (event_date or "").strip()
            for i, local_t in enumerate(times):
                day = str(local_t)[:10]
                if day != target or i >= len(highs) or highs[i] is None:
                    continue
                raw = int(round(float(highs[i])))
                if twc_units == "m":
                    return raw, _c_to_f(raw)
                return _f_to_c(raw), raw
        except Exception as exc:
            logger.warning(
                "Weather.com forecast failed icao=%s date=%s: %s",
                icao,
                event_date,
                exc,
            )
        return None

    def _fetch_wunderground_forecast(
        self,
        resolution_source: Optional[str],
        event_date: str,
        *,
        default_unit: str = "F",
    ) -> Optional[tuple[int, str]]:
        """Scrape today's High from the WU hourly forecast page. Returns (temp, unit).

        Uses /hourly/{country}/{city}/{ICAO} (rewritten from history/daily resolution
        URLs). Parses the Today narrative, e.g. 'High around 30C' / 'High 86F'.
        """
        hourly_url = hourly_forecast_url(resolution_source or "")
        if not hourly_url:
            return None
        try:
            resp = self.session.get(
                hourly_url,
                timeout=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            page_text = soup.get_text(" ", strip=True)
            parsed = parse_wu_high_from_text(
                page_text,
                event_date=event_date,
                default_unit=default_unit,
            )
            if parsed is not None:
                return parsed
            # Fallback: raw HTML may embed narrative JSON before get_text flattens well
            parsed = parse_wu_high_from_text(
                resp.text,
                event_date=event_date,
                default_unit=default_unit,
            )
            if parsed is not None:
                return parsed
            logger.info(
                "Wunderground hourly page had no High narrative url=%s date=%s",
                hourly_url,
                event_date,
            )
        except Exception as exc:
            logger.warning("Wunderground hourly fetch failed for %s: %s", hourly_url, exc)
        return None
