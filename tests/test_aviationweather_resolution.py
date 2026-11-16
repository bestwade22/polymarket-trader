"""Tests for the aviationweather resolution-source fixes.

Three defects are covered:

1. `ids=` in an aviationweather.gov URL was not parsed, so Istanbul, Moscow and
   Tel Aviv had no station ICAO and therefore no forecast at all.
2. The station ICAO named in the market description (e.g. "(KDEN)") was
   ignored, so Denver forecast the wrong airport (KBKF instead of KDEN).
3. The forecast was requested in the resolution unit (always Celsius) even on
   Fahrenheit markets, rounding the forecast through Celsius.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.api.city_resolution_map import (
    _icao_from_description,
    _icao_from_url,
    extract_resolution_from_event,
)
from src.api.weather_client import WeatherClient, wu_hourly_url_for_icao
from src.utils.market_parser import market_unit, market_unit_from_title


AV_RESOLUTION = (
    "https://aviationweather.gov/api/data/metar?ids=KDEN&format=raw"
    "&date=2026-11-16T10:00:00Z&hours=30"
)


# --- 1. ids= parsing ---------------------------------------------------------


def test_icao_from_url_parses_aviationweather_ids_param():
    assert (
        _icao_from_url(
            "https://aviationweather.gov/api/data/metar?ids=LTFM&format=raw"
            "&date=2026-11-13T00:00:00Z&hours=30"
        )
        == "LTFM"
    )
    assert (
        _icao_from_url(
            "https://aviationweather.gov/api/data/metar?ids=UUWW&format=raw&hours=30"
        )
        == "UUWW"
    )


def test_icao_from_url_still_handles_wunderground():
    assert (
        _icao_from_url("https://www.wunderground.com/history/daily/jp/tokyo/RJTT")
        == "RJTT"
    )


def test_extract_resolution_aviationweather_icao_from_ids():
    event = {
        "city": "Istanbul",
        "resolutionSource": (
            "https://aviationweather.gov/api/data/metar?ids=LTFM&format=raw"
            "&date=2026-11-13T00:00:00Z&hours=30"
        ),
        "description": (
            "This market will resolve to the temperature range that contains the "
            "highest temperature reported at Istanbul Airport (LTFM) on 12 Nov '26."
        ),
    }
    entry = extract_resolution_from_event(event)
    assert entry is not None
    assert entry["icao"] == "LTFM"


# --- 2. description-based ICAO override --------------------------------------


def test_icao_from_description():
    assert (
        _icao_from_description(
            "highest temperature reported at Denver International Airport (KDEN) "
            "on 15 Nov '26"
        )
        == "KDEN"
    )
    assert _icao_from_description("no station code here") is None


def test_description_icao_overrides_url_icao():
    """Denver resolves at KDEN even though the older URL points at KBKF."""
    event = {
        "city": "Denver",
        "resolutionSource": (
            "https://aviationweather.gov/api/data/metar?ids=KBKF&format=raw&hours=30"
        ),
        "description": (
            "This market will resolve to the temperature range that contains the "
            "highest temperature reported at Denver International Airport (KDEN) "
            "on 15 Nov '26."
        ),
    }
    entry = extract_resolution_from_event(event)
    assert entry is not None
    assert entry["icao"] == "KDEN"


# --- 3. market unit alignment ------------------------------------------------


def test_market_unit_reads_bucket_labels():
    assert market_unit([{"groupItemTitle": "54-55°F"}]) == "F"
    assert market_unit([{"groupItemTitle": "11°C"}]) == "C"
    assert market_unit([]) is None
    assert market_unit_from_title("50-51°F") == "F"
    assert market_unit_from_title("9°C") == "C"
    assert market_unit_from_title("") is None


def test_weather_client_requests_forecast_in_market_unit(tmp_path: Path, monkeypatch):
    """A Fahrenheit market must query Weather.com in Fahrenheit (units=e)."""
    map_path = tmp_path / "map.json"
    map_path.write_text(
        json.dumps(
            {
                "Atlanta": {
                    "city": "Atlanta",
                    "icao": "KATL",
                    "resolution_source": (
                        "https://aviationweather.gov/api/data/metar?ids=KATL"
                        "&format=raw&hours=30"
                    ),
                    "latitude": 33.75,
                    "longitude": -84.39,
                    "provider": "noaa",
                    "units": "C",
                }
            }
        )
    )
    monkeypatch.setattr(
        "src.api.weather_client.load_resolution_map", lambda: json.loads(map_path.read_text())
    )
    monkeypatch.setattr("src.api.weather_client.load_city_coords", lambda: {})

    client = WeatherClient()
    mock_wc = MagicMock()
    mock_wc.raise_for_status = MagicMock()
    mock_wc.json.return_value = {
        "validTimeLocal": ["2026-11-17T00:00:00-0500"],
        "calendarDayTemperatureMax": [65],
    }
    requested: list[dict] = []

    def fake_get(url, *args, **kwargs):
        if "api.weather.com" in str(url):
            requested.append(kwargs.get("params") or {})
            return mock_wc
        raise AssertionError(f"unexpected url {url}")

    with patch.object(client.session, "get", side_effect=fake_get):
        result = client.fetch_forecast_max_temp("Atlanta", "2026-11-17", units="F")

    assert result is not None
    assert requested and requested[0].get("units") == "e", requested
    assert result.temp_f == 65
    assert result.temp_c == 18


def test_weather_client_defaults_to_map_units_without_override(
    tmp_path: Path, monkeypatch
):
    map_path = tmp_path / "map.json"
    map_path.write_text(
        json.dumps(
            {
                "Paris": {
                    "city": "Paris",
                    "icao": "LFPB",
                    "resolution_source": (
                        "https://aviationweather.gov/api/data/metar?ids=LFPB"
                        "&format=raw&hours=30"
                    ),
                    "latitude": 48.85,
                    "longitude": 2.35,
                    "provider": "noaa",
                    "units": "C",
                }
            }
        )
    )
    monkeypatch.setattr(
        "src.api.weather_client.load_resolution_map", lambda: json.loads(map_path.read_text())
    )
    monkeypatch.setattr("src.api.weather_client.load_city_coords", lambda: {})

    client = WeatherClient()
    mock_wc = MagicMock()
    mock_wc.raise_for_status = MagicMock()
    mock_wc.json.return_value = {
        "validTimeLocal": ["2026-11-17T00:00:00+0100"],
        "calendarDayTemperatureMax": [17],
    }
    requested: list[dict] = []

    def fake_get(url, *args, **kwargs):
        if "api.weather.com" in str(url):
            requested.append(kwargs.get("params") or {})
            return mock_wc
        raise AssertionError(f"unexpected url {url}")

    with patch.object(client.session, "get", side_effect=fake_get):
        result = client.fetch_forecast_max_temp("Paris", "2026-11-17")

    assert result is not None
    assert requested and requested[0].get("units") == "m", requested
    assert result.temp_c == 17


# --- second forecast source for non-WU resolution sources --------------------


def test_wu_hourly_url_for_icao():
    assert wu_hourly_url_for_icao("KATL") == "https://www.wunderground.com/hourly/KATL"
    assert wu_hourly_url_for_icao(None) is None
    assert wu_hourly_url_for_icao("not-an-icao") is None


def test_aviationweather_city_gets_second_forecast_source(
    tmp_path: Path, monkeypatch
):
    """A non-Wunderground resolution source still needs a second forecast source."""
    map_path = tmp_path / "map.json"
    map_path.write_text(
        json.dumps(
            {
                "Atlanta": {
                    "city": "Atlanta",
                    "icao": "KATL",
                    "resolution_source": (
                        "https://aviationweather.gov/api/data/metar?ids=KATL"
                        "&format=raw&hours=30"
                    ),
                    "latitude": 33.75,
                    "longitude": -84.39,
                    "provider": "noaa",
                    "units": "C",
                }
            }
        )
    )
    monkeypatch.setattr(
        "src.api.weather_client.load_resolution_map", lambda: json.loads(map_path.read_text())
    )
    monkeypatch.setattr("src.api.weather_client.load_city_coords", lambda: {})

    client = WeatherClient()
    mock_wc = MagicMock()
    mock_wc.raise_for_status = MagicMock()
    mock_wc.json.return_value = {
        "validTimeLocal": ["2026-11-17T00:00:00-0500"],
        "calendarDayTemperatureMax": [65],
    }
    mock_wu = MagicMock()
    mock_wu.raise_for_status = MagicMock()
    mock_wu.text = "Today 11/17 High around 60F. Winds W at 5 to 10 mph."
    requested: list[str] = []

    def fake_get(url, *args, **kwargs):
        requested.append(str(url))
        if "api.weather.com" in str(url):
            return mock_wc
        return mock_wu

    with patch.object(client.session, "get", side_effect=fake_get):
        result = client.fetch_forecast_max_temp("Atlanta", "2026-11-17", units="F")

    assert result is not None
    assert result.source == "weather_com"
    assert result.wu_temp_f == 60
    assert any("wunderground.com/hourly/KATL" in u for u in requested), requested
