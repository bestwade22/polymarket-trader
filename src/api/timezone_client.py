import json
import logging
from typing import Optional

import requests

from config.settings import (
    API_NINJAS_BASE,
    CITY_COORDS_FILE,
    CITY_TIMEZONES_FILE,
    settings,
)

logger = logging.getLogger(__name__)

OPEN_METEO_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"


class TimezoneClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.api_ninjas_key
        self.session = requests.Session()
        self._cache: dict[str, dict] = {}
        self._local_coords = self._load_local_coords()
        self._local_timezones = self._load_local_timezones()

    def _load_local_coords(self) -> dict[str, dict]:
        if not CITY_COORDS_FILE.exists():
            return {}
        data = json.loads(CITY_COORDS_FILE.read_text())
        return {entry["city"].lower(): entry for entry in data}

    def _load_local_timezones(self) -> dict[str, str]:
        """City -> IANA timezone from the checked-in city_timezones.json map.

        This map covers every city the bot trades, so resolving a timezone is a
        local lookup instead of a geocoding round trip. Falling back to the
        network for all ~50 cities made the daily fetch take a minute and put
        it at risk of running out of time or disk.
        """
        if not CITY_TIMEZONES_FILE.exists():
            return {}
        try:
            data = json.loads(CITY_TIMEZONES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(city).lower(): str(tz) for city, tz in data.items() if tz}

    def _fallback_from_local(self, city: str) -> Optional[dict]:
        entry = self._local_coords.get(city.lower())
        if not entry:
            return None
        return {
            "timezone": entry["timezone"],
            "utc_offset": None,
            "city": city,
            "latitude": entry.get("latitude"),
            "longitude": entry.get("longitude"),
            "source": "city_coords.json",
        }

    def _fetch_from_open_meteo(self, city: str) -> Optional[dict]:
        try:
            resp = self.session.get(
                OPEN_METEO_GEOCODING,
                params={"name": city, "count": 1, "language": "en", "format": "json"},
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("results") or []
            if not results:
                return None
            hit = results[0]
            return {
                "timezone": hit["timezone"],
                "utc_offset": None,
                "city": city,
                "latitude": hit.get("latitude"),
                "longitude": hit.get("longitude"),
                "source": "open-meteo-geocoding",
            }
        except requests.RequestException as exc:
            logger.debug("Open-Meteo geocoding failed for %s: %s", city, exc)
            return None

    def _fetch_from_api_ninjas(self, city: str) -> Optional[dict]:
        headers = {"X-Api-Key": self.api_key}
        param_variants = [{"city": city}, {"city": city, "country": "US"}]
        if " " in city:
            param_variants.insert(1, {"city": city.split()[0]})

        last_error = None
        for params in param_variants:
            try:
                resp = self.session.get(
                    API_NINJAS_BASE, params=params, headers=headers, timeout=15
                )
                if resp.status_code == 400:
                    continue
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and data:
                    return data[0]
                if isinstance(data, dict) and data.get("timezone"):
                    return data
            except requests.RequestException as exc:
                last_error = exc

        if last_error:
            logger.debug("API Ninjas lookup failed for %s: %s", city, last_error)
        return None

    def _fallback_from_timezone_map(self, city: str) -> Optional[dict]:
        tz_name = self._local_timezones.get(city.lower())
        if not tz_name:
            return None
        coords = self._local_coords.get(city.lower()) or {}
        return {
            "timezone": tz_name,
            "utc_offset": None,
            "city": city,
            "latitude": coords.get("latitude"),
            "longitude": coords.get("longitude"),
            "source": "city_timezones.json",
        }

    def get_timezone_for_city(self, city: str) -> Optional[dict]:
        cache_key = city.lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._fallback_from_local(city)
        if not result:
            result = self._fallback_from_timezone_map(city)
        if not result and self.api_key:
            result = self._fetch_from_api_ninjas(city)
        if not result:
            result = self._fetch_from_open_meteo(city)

        if not result:
            logger.warning("No timezone data for %s", city)
            return None

        self._cache[cache_key] = result
        logger.info(
            "Timezone for %s: %s (via %s)",
            city,
            result.get("timezone"),
            result.get("source", "api-ninjas"),
        )
        return result
