"""Weather forecast client — Weather.com prediction + WU hourly scrape compare."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config.settings import settings
from src.api.city_resolution_map import get_city_entry, load_city_coords, load_resolution_map

logger = logging.getLogger(__name__)

# Predicted daily max (not observed/archive). Weather.com powers Wunderground forecasts.
WEATHER_COM_DAILY_URL = "https://api.weather.com/v3/wx/forecast/daily/15day"
# Public WU/TWC frontend key (the one embedded on wunderground.com pages). It is
# published rather than secret, but it rotates without notice. Pin a specific key
# with WEATHER_COM_API_KEY; otherwise the client refreshes it from wunderground.com
# whenever Weather.com rejects the one it holds.
WEATHER_COM_API_KEY = "f6d2efe5720d47ea92efe5720df7eaa8"
# Keyless fallback: Open-Meteo daily high for the station coords in the city map.
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# Keyless fallback for past event dates: Open-Meteo's archive of the forecasts as
# issued (observed temperatures are not substituted — these are forecast columns).
OPEN_METEO_HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# Every Wunderground page publishes the current Weather.com frontend key.
WU_API_KEY_PATTERN = re.compile(r'"API_KEY"\s*:\s*"([a-f0-9]{32})"')
WU_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _configured_api_key() -> str:
    """Env override wins; a blank env var falls back to the built-in key."""
    raw = (os.getenv("WEATHER_COM_API_KEY") or "").strip()
    if not raw:
        raw = (getattr(settings, "weather_com_api_key", "") or "").strip()
    return raw or WEATHER_COM_API_KEY


@dataclass
class ForecastMaxTemp:
    """Predicted daily max plus optional WU hourly-page scrape for compare.

    Primary is Weather.com calendar-day high (forward forecast). WU hourly narrative
    is recorded separately for compare; used as primary only if Weather.com misses.

    `source` records which provider produced the primary value. It matters for the
    OM∩WU share bump: the two sides must come from *independent* providers, so a
    fallback that reuses the WU scrape as primary must not be treated as agreement.
    """

    temp_f: int
    temp_c: int
    source: str
    icao: Optional[str] = None
    resolution_source: Optional[str] = None
    wu_temp_f: Optional[int] = None
    wu_temp_c: Optional[int] = None
    # True when the primary value came from the WU scrape itself (i.e. primary and
    # wu_* are the same source, so their "agreement" carries no information).
    primary_is_wu_scrape: bool = False


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
        # Weather.com key currently in use, and the last known-bad key (so we do
        # not re-fetch the same rejected key on every call).
        self._api_key = _configured_api_key()
        self._rejected_key: Optional[str] = None

    def reload_maps(self) -> None:
        self._city_coords = load_city_coords()
        self._resolution_map = load_resolution_map()

    def _refresh_weather_com_api_key(self) -> Optional[str]:
        """Scrape the current Weather.com frontend key from wunderground.com.

        The key is published on every WU page and rotates without notice. Returns
        the new key, or None when it could not be determined.
        """
        try:
            resp = self.session.get(
                "https://www.wunderground.com/",
                timeout=20,
                headers={"User-Agent": WU_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Could not refresh Weather.com API key: %s", exc)
            return None

        match = WU_API_KEY_PATTERN.search(resp.text)
        if not match:
            logger.warning("No Weather.com API key found on wunderground.com")
            return None

        key = match.group(1)
        if key != self._api_key:
            logger.info("Refreshed Weather.com API key (%s… → %s…)", self._api_key[:8], key[:8])
        self._api_key = key
        self._rejected_key = None
        return key

    def _weather_com_api_key(self) -> str:
        """Return a usable Weather.com key, refreshing it if the current one failed."""
        if self._rejected_key is not None and self._api_key == self._rejected_key:
            self._refresh_weather_com_api_key()
        return self._api_key

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
        Fallbacks: Open-Meteo (keyless) then the WU hourly narrative scrape.
        The WU scrape is always fetched for the wu_* compare fields.
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

        # Weather.com missed: try the keyless Open-Meteo provider before the scrape.
        coords = self.get_coords_for_city(city)
        if coords is not None:
            om = self._fetch_open_meteo_max(
                coords[0], coords[1], event_date, units=map_units
            )
            if om is not None:
                primary_c, primary_f = om
                primary_source = "open_meteo"
                logger.info(
                    "forecast_source=open_meteo city=%s icao=%s date=%s "
                    "temp_f=%s temp_c=%s",
                    city,
                    icao,
                    event_date,
                    primary_f,
                    primary_c,
                )
                return ForecastMaxTemp(
                    temp_f=primary_f,
                    temp_c=primary_c,
                    source="open_meteo",
                    icao=icao,
                    resolution_source=source_url,
                    wu_temp_f=wu_f,
                    wu_temp_c=wu_c,
                )

        if wu_f is not None and wu_c is not None:
            # The primary IS the WU scrape here, so primary and wu_* are the same
            # source — flag it so the share bump does not read this as agreement.
            return ForecastMaxTemp(
                temp_f=wu_f,
                temp_c=wu_c,
                source="wunderground_scrape",
                icao=icao,
                resolution_source=source_url,
                wu_temp_f=wu_f,
                wu_temp_c=wu_c,
                primary_is_wu_scrape=True,
            )

        logger.warning(
            "No forecast for city=%s date=%s (Weather.com / Open-Meteo / WU miss)",
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
        target = (event_date or "").strip()

        # A rejected key is refreshed once, then the call is retried a single time.
        for attempt in (1, 2):
            api_key = self._weather_com_api_key()
            params = {
                "apiKey": api_key,
                "icaoCode": icao,
                "units": twc_units,
                "language": "en-US",
                "format": "json",
            }
            try:
                resp = self.session.get(WEATHER_COM_DAILY_URL, params=params, timeout=20)
                if resp.status_code == 401:
                    self._rejected_key = api_key
                    if attempt == 1:
                        logger.warning(
                            "Weather.com rejected API key %s…; refreshing key and retrying",
                            api_key[:8],
                        )
                        if self._refresh_weather_com_api_key():
                            continue
                    logger.warning(
                        "Weather.com forecast failed icao=%s date=%s: 401 Unauthorized",
                        icao,
                        target,
                    )
                    return None
                resp.raise_for_status()
                data = resp.json()
                times = data.get("validTimeLocal") or []
                highs = data.get("calendarDayTemperatureMax") or []
                for i, local_t in enumerate(times):
                    day = str(local_t)[:10]
                    if day != target or i >= len(highs) or highs[i] is None:
                        continue
                    raw = int(round(float(highs[i])))
                    if twc_units == "m":
                        return raw, _c_to_f(raw)
                    return _f_to_c(raw), raw
                return None
            except Exception as exc:
                logger.warning(
                    "Weather.com forecast failed icao=%s date=%s: %s",
                    icao,
                    target,
                    exc,
                )
                return None
        return None

    def _fetch_open_meteo_max(
        self,
        lat: float,
        lon: float,
        event_date: str,
        *,
        units: str = "C",
    ) -> Optional[tuple[int, int]]:
        """Keyless fallback: Open-Meteo predicted daily high. Returns (temp_c, temp_f).

        Uses the historical-forecast endpoint for past dates so the value stays a
        forecast (the archive endpoint would return observed temperatures).
        """
        target = (event_date or "").strip()
        unit_param = "celsius" if str(units).upper().startswith("C") else "fahrenheit"
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max",
            "temperature_unit": unit_param,
            "timezone": "auto",
            "start_date": target,
            "end_date": target,
        }
        urls = [OPEN_METEO_FORECAST_URL]
        try:
            if target and date.fromisoformat(target) < date.today():
                urls.insert(0, OPEN_METEO_HISTORICAL_URL)
        except ValueError:
            pass

        for url in urls:
            try:
                resp = self.session.get(url, params=params, timeout=20)
                resp.raise_for_status()
                daily = resp.json().get("daily", {})
                temps = daily.get("temperature_2m_max") or []
                if not temps or temps[0] is None:
                    continue
                raw = int(round(float(temps[0])))
                if unit_param == "celsius":
                    return raw, _c_to_f(raw)
                return _f_to_c(raw), raw
            except Exception as exc:
                logger.warning(
                    "Open-Meteo forecast failed lat=%s lon=%s date=%s (%s): %s",
                    lat,
                    lon,
                    target,
                    url,
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
                    "User-Agent": WU_USER_AGENT,
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
