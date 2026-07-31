"""City → Polymarket resolution-source mapping (Wunderground / NOAA / HKO)."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

import requests

from config.settings import CITY_COORDS_FILE, CITY_RESOLUTION_SOURCES_FILE, DATA_DIR

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

_URL_IN_TEXT = re.compile(r"https?://[^\s\)\"']+", re.IGNORECASE)
_WU_ICAO = re.compile(
    r"wunderground\.com/history/daily/(?:[^/\s]+/){1,3}([A-Z0-9]{3,4})/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_STATION_NAME = re.compile(
    r"recorded at the\s+(.+?)\s+in degrees",
    re.IGNORECASE | re.DOTALL,
)
_UNITS_C = re.compile(r"degrees\s+Celsius|°C|\bCelsius\b", re.IGNORECASE)
_UNITS_F = re.compile(r"degrees\s+Fahrenheit|°F|\bFahrenheit\b", re.IGNORECASE)


def _provider_for_url(url: str) -> str:
    lower = (url or "").lower()
    if "wunderground.com" in lower:
        return "wunderground"
    if "weather.gov.hk" in lower:
        return "hko"
    if "noaa.gov" in lower or "weather.gov" in lower:
        return "noaa"
    return "other"


def _icao_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = _WU_ICAO.search(url.rstrip("/"))
    if m:
        return m.group(1).upper()
    # Trailing path segment that looks like ICAO
    parts = [p for p in unquote(url).rstrip("/").split("/") if p]
    if parts:
        tail = parts[-1].split("?")[0]
        if re.fullmatch(r"[A-Za-z0-9]{3,4}", tail):
            return tail.upper()
    return None


def _station_name_from_description(description: str) -> Optional[str]:
    if not description:
        return None
    m = _STATION_NAME.search(description)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    return name or None


def _units_from_description(description: str) -> str:
    if description and _UNITS_C.search(description):
        return "C"
    if description and _UNITS_F.search(description):
        return "F"
    return "C"


def _first_url_in_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = _URL_IN_TEXT.search(text)
    return m.group(0).rstrip(".,;") if m else None


def extract_resolution_from_event(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Build a map entry fragment from a Gamma/enriched event dict."""
    city = (event.get("city") or "").strip()
    if not city:
        return None

    resolution_source = (event.get("resolutionSource") or "").strip()
    description = event.get("description") or ""
    if not resolution_source and event.get("markets"):
        market0 = event["markets"][0] if isinstance(event["markets"], list) else {}
        if isinstance(market0, dict):
            resolution_source = (market0.get("resolutionSource") or "").strip()
            if not description:
                description = market0.get("description") or ""

    if not resolution_source:
        resolution_source = _first_url_in_text(description) or ""

    provider = _provider_for_url(resolution_source) if resolution_source else "other"
    icao = _icao_from_url(resolution_source) if resolution_source else None
    station_name = _station_name_from_description(description)
    units = _units_from_description(description)

    return {
        "city": city,
        "station_name": station_name,
        "provider": provider,
        "icao": icao,
        "resolution_source": resolution_source or None,
        "latitude": None,
        "longitude": None,
        "units": units,
    }


def load_city_coords(path: Optional[Path] = None) -> dict[str, dict[str, float]]:
    coords_path = path or CITY_COORDS_FILE
    if not coords_path.exists():
        return {}
    try:
        data = json.loads(coords_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, float]] = {}
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        city = (row.get("city") or "").strip()
        if not city:
            continue
        try:
            out[city.lower()] = {
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def load_resolution_map(path: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    map_path = path or CITY_RESOLUTION_SOURCES_FILE
    if not map_path.exists():
        return {}
    try:
        data = json.loads(map_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read city resolution map: %s", map_path)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_resolution_map(
    mapping: dict[str, dict[str, Any]],
    path: Optional[Path] = None,
) -> Path:
    out = path or CITY_RESOLUTION_SOURCES_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(mapping.items(), key=lambda item: item[0].lower()))
    with tempfile.NamedTemporaryFile("w", delete=False, dir=out.parent, suffix=".tmp") as tmp:
        json.dump(ordered, tmp, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(out)
    return out


def get_city_entry(
    city: str,
    mapping: Optional[dict[str, dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    data = mapping if mapping is not None else load_resolution_map()
    if not city:
        return None
    if city in data:
        return data[city]
    lower = city.lower()
    for key, value in data.items():
        if key.lower() == lower:
            return value
    return None


def geocode_city(city: str, session: Optional[requests.Session] = None) -> Optional[tuple[float, float]]:
    """One-time Open-Meteo geocode for a city name."""
    sess = session or requests.Session()
    try:
        resp = sess.get(
            GEOCODING_URL,
            params={"name": city, "count": 1, "language": "en", "format": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return None
        row = results[0]
        return float(row["latitude"]), float(row["longitude"])
    except Exception as exc:
        logger.warning("Geocode failed for %s: %s", city, exc)
        return None


def _merge_entry(
    existing: Optional[dict[str, Any]],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge without overwriting hand-fixed coords/source unless empty."""
    if not existing:
        return dict(incoming)
    out = dict(existing)
    for key, value in incoming.items():
        if value is None or value == "":
            continue
        prior = out.get(key)
        if prior is None or prior == "":
            out[key] = value
        elif key in ("resolution_source", "icao", "station_name", "provider", "units"):
            # Prefer newer non-empty resolution metadata from events
            out[key] = value
        # latitude/longitude: never overwrite existing numeric coords
    return out


def fill_coordinates(
    entry: dict[str, Any],
    *,
    city_coords: Optional[dict[str, dict[str, float]]] = None,
    geocode: bool = True,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    out = dict(entry)
    if out.get("latitude") is not None and out.get("longitude") is not None:
        return out
    city = out.get("city") or ""
    coords_index = city_coords if city_coords is not None else load_city_coords()
    known = coords_index.get(city.lower())
    if known:
        out["latitude"] = known["latitude"]
        out["longitude"] = known["longitude"]
        return out
    if geocode and city:
        pair = geocode_city(city, session=session)
        if pair:
            out["latitude"], out["longitude"] = pair
    return out


def upsert_events_into_map(
    events: list[dict[str, Any]],
    *,
    mapping: Optional[dict[str, dict[str, Any]]] = None,
    geocode_missing: bool = True,
    session: Optional[requests.Session] = None,
) -> dict[str, dict[str, Any]]:
    """Merge event cities into the resolution map; fill lat/lon when missing."""
    data = dict(mapping if mapping is not None else load_resolution_map())
    coords = load_city_coords()
    sess = session or requests.Session()
    for event in events:
        frag = extract_resolution_from_event(event)
        if not frag:
            continue
        city = frag["city"]
        merged = _merge_entry(data.get(city), frag)
        needs_coords = merged.get("latitude") is None or merged.get("longitude") is None
        if needs_coords:
            merged = fill_coordinates(
                merged,
                city_coords=coords,
                geocode=geocode_missing,
                session=sess,
            )
        data[city] = merged
    return data


def scan_events_files(
    data_dir: Optional[Path] = None,
    *,
    limit_files: Optional[int] = None,
) -> list[dict[str, Any]]:
    root = data_dir or DATA_DIR
    paths = sorted(root.glob("events_*.json"))
    if limit_files is not None:
        paths = paths[-limit_files:]
    events: list[dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list):
            events.extend(row for row in payload if isinstance(row, dict))
    return events


def build_city_resolution_map(
    *,
    data_dir: Optional[Path] = None,
    out_path: Optional[Path] = None,
    geocode_missing: bool = True,
    limit_files: Optional[int] = None,
) -> dict[str, dict[str, Any]]:
    """Scan events_*.json and write city_resolution_sources.json."""
    events = scan_events_files(data_dir, limit_files=limit_files)
    mapping = upsert_events_into_map(events, geocode_missing=geocode_missing)
    path = save_resolution_map(mapping, path=out_path)
    logger.info("Wrote %d city resolution sources to %s", len(mapping), path)
    return mapping
