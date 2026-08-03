"""Tests for city resolution map, forecast fetch, and trade-history forecast fields."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.analysis.models import TradeRecord
from src.analysis.pattern_enrichment import apply_selection_pattern_fields, forecast_delta_c
from src.analysis.selection_enrichment import apply_enrichment_to_record
from src.api.city_resolution_map import (
    build_city_resolution_map,
    extract_resolution_from_event,
    upsert_events_into_map,
)
from src.api.weather_client import (
    ForecastMaxTemp,
    WeatherClient,
    hourly_forecast_url,
    parse_wu_high_from_text,
)
from src.trade.hourly_runner import attach_forecasts_to_selections, attach_forecasts_to_skipped
from src.trade.strategies.base import MarketSelection


TOKYO_EVENT = {
    "city": "Tokyo",
    "resolutionSource": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT",
    "description": (
        "This market will resolve to the temperature range that contains the highest "
        "temperature recorded at the Tokyo Haneda Airport Station in degrees Celsius "
        "on 31 Jul '26.\n\nThe resolution source for this market will be information "
        "from Wunderground, specifically the highest temperature recorded for all times "
        "on this day for the Tokyo Haneda Airport Station, available here: "
        "https://www.wunderground.com/history/daily/jp/tokyo/RJTT."
    ),
}


def test_extract_tokyo_resolution_from_event():
    entry = extract_resolution_from_event(TOKYO_EVENT)
    assert entry is not None
    assert entry["city"] == "Tokyo"
    assert entry["icao"] == "RJTT"
    assert entry["provider"] == "wunderground"
    assert entry["resolution_source"].endswith("/RJTT")
    assert entry["station_name"] == "Tokyo Haneda Airport Station"
    assert entry["units"] == "C"


def test_extract_resolution_from_description_when_field_empty():
    event = {
        "city": "Hong Kong",
        "resolutionSource": "",
        "description": "See https://www.weather.gov.hk/en/cis/climat.htm for details in degrees Celsius.",
    }
    entry = extract_resolution_from_event(event)
    assert entry is not None
    assert entry["provider"] == "hko"
    assert "weather.gov.hk" in entry["resolution_source"]


def test_build_city_resolution_map_from_fixture(tmp_path: Path):
    events_path = tmp_path / "events_2026-07-31.json"
    events_path.write_text(json.dumps([TOKYO_EVENT]))
    out = tmp_path / "city_resolution_sources.json"
    with patch("src.api.city_resolution_map.load_resolution_map", return_value={}):
        with patch("src.api.city_resolution_map.load_city_coords", return_value={}):
            with patch(
                "src.api.city_resolution_map.geocode_city",
                return_value=(35.55, 139.78),
            ):
                mapping = build_city_resolution_map(
                    data_dir=tmp_path,
                    out_path=out,
                    geocode_missing=True,
                )
    assert "Tokyo" in mapping
    assert mapping["Tokyo"]["icao"] == "RJTT"
    assert mapping["Tokyo"]["latitude"] == 35.55
    assert out.exists()
    saved = json.loads(out.read_text())
    assert saved["Tokyo"]["icao"] == "RJTT"


def test_upsert_does_not_overwrite_existing_coords():
    existing = {
        "Tokyo": {
            "city": "Tokyo",
            "icao": "RJTT",
            "resolution_source": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT",
            "latitude": 1.0,
            "longitude": 2.0,
            "provider": "wunderground",
            "units": "C",
            "station_name": None,
        }
    }
    mapping = upsert_events_into_map(
        [TOKYO_EVENT],
        mapping=existing,
        geocode_missing=False,
    )
    assert mapping["Tokyo"]["latitude"] == 1.0
    assert mapping["Tokyo"]["longitude"] == 2.0
    assert mapping["Tokyo"]["station_name"] == "Tokyo Haneda Airport Station"


def test_hourly_forecast_url_rewrites_history_daily():
    assert (
        hourly_forecast_url(
            "https://www.wunderground.com/history/daily/us/ga/atlanta/KATL"
        )
        == "https://www.wunderground.com/hourly/us/ga/atlanta/KATL"
    )
    assert (
        hourly_forecast_url("https://www.wunderground.com/hourly/gb/london/EGLC")
        == "https://www.wunderground.com/hourly/gb/london/EGLC"
    )
    assert hourly_forecast_url("https://example.com/other") is None


def test_parse_wu_high_from_today_narrative_celsius():
    text = (
        "Hourly Forecast for Today, Monday 08/03 "
        "Today 08/03 35% / 0.54 mm "
        "Scattered thunderstorms developing this afternoon. High around 30C. "
        "Winds W at 10 to 15 km/h. Chance of rain 40%. "
        "Tonight 08/03 40% / 0.1 mm Rain showers this evening. Low 22C."
    )
    assert parse_wu_high_from_text(text, event_date="2026-08-03", default_unit="C") == (
        30,
        "C",
    )


def test_parse_wu_high_from_today_narrative_fahrenheit():
    text = (
        "Today 08/03 34 % / 0.06 in A few isolated thunderstorms developing this "
        "afternoon. High 86F. Winds W at 5 to 10 mph. Chance of rain 30%. "
        "Tonight 08/03 44 % / 0.2 in Showers early. Low 72F."
    )
    assert parse_wu_high_from_text(text, event_date="2026-08-03", default_unit="F") == (
        86,
        "F",
    )


def test_weather_client_uses_weather_com_prediction(tmp_path: Path, monkeypatch):
    map_path = tmp_path / "map.json"
    map_path.write_text(
        json.dumps(
            {
                "Tokyo": {
                    "city": "Tokyo",
                    "icao": "RJTT",
                    "resolution_source": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT",
                    "latitude": 35.55,
                    "longitude": 139.78,
                    "provider": "wunderground",
                    "units": "C",
                }
            }
        )
    )
    monkeypatch.setattr("src.api.weather_client.load_resolution_map", lambda: json.loads(map_path.read_text()))
    monkeypatch.setattr("src.api.weather_client.load_city_coords", lambda: {})

    client = WeatherClient()
    mock_wc = MagicMock()
    mock_wc.raise_for_status = MagicMock()
    mock_wc.json.return_value = {
        "validTimeLocal": ["2026-07-31T00:00:00+0900", "2026-08-01T00:00:00+0900"],
        "calendarDayTemperatureMax": [33, 34],
    }
    mock_wu = MagicMock()
    mock_wu.raise_for_status = MagicMock()
    mock_wu.text = (
        "Today 07/31 35% / 0.54 mm Scattered thunderstorms developing this afternoon. "
        "High around 32C. Winds W at 10 to 15 km/h. Chance of rain 40%. "
        "Tonight 07/31 Low 25C."
    )

    called_urls: list[str] = []

    def fake_get(url, *args, **kwargs):
        called_urls.append(str(url))
        if "api.weather.com" in str(url):
            return mock_wc
        if "wunderground" in str(url):
            return mock_wu
        raise AssertionError(f"unexpected url {url}")

    with patch.object(client.session, "get", side_effect=fake_get):
        result = client.fetch_forecast_max_temp("Tokyo", "2026-07-31")
    assert result is not None
    assert result.source == "weather_com"
    assert result.temp_c == 33
    assert result.temp_f == 91
    assert result.icao == "RJTT"
    assert result.wu_temp_c == 32
    assert result.wu_temp_f == 90
    assert any("api.weather.com" in u for u in called_urls)
    assert any("/hourly/jp/tokyo/RJTT" in u for u in called_urls)


def test_weather_client_falls_back_to_wu_scrape(tmp_path: Path, monkeypatch):
    map_path = tmp_path / "map.json"
    map_path.write_text(
        json.dumps(
            {
                "Tokyo": {
                    "city": "Tokyo",
                    "icao": "RJTT",
                    "resolution_source": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT",
                    "latitude": 35.55,
                    "longitude": 139.78,
                    "provider": "wunderground",
                    "units": "C",
                }
            }
        )
    )
    monkeypatch.setattr("src.api.weather_client.load_resolution_map", lambda: json.loads(map_path.read_text()))
    monkeypatch.setattr("src.api.weather_client.load_city_coords", lambda: {})

    client = WeatherClient()
    mock_wc = MagicMock()
    mock_wc.raise_for_status = MagicMock()
    # Event date not in Weather.com horizon → primary miss
    mock_wc.json.return_value = {
        "validTimeLocal": ["2026-08-01T00:00:00+0900"],
        "calendarDayTemperatureMax": [34],
    }
    mock_wu = MagicMock()
    mock_wu.raise_for_status = MagicMock()
    mock_wu.text = (
        "Today 07/31 High around 31C. Winds W at 10 to 15 km/h. "
        "Tonight 07/31 Low 25C."
    )

    def fake_get(url, *args, **kwargs):
        if "api.weather.com" in str(url):
            return mock_wc
        if "wunderground" in str(url):
            return mock_wu
        raise AssertionError(f"unexpected url {url}")

    with patch.object(client.session, "get", side_effect=fake_get):
        result = client.fetch_forecast_max_temp("Tokyo", "2026-07-31")
    assert result is not None
    assert result.source == "wunderground_scrape"
    assert result.temp_c == 31
    assert result.wu_temp_c == 31


def test_attach_forecasts_to_selections():
    sel = MarketSelection(
        event_id="1",
        city="Tokyo",
        market_id="m1",
        group_item_title="32°C",
        yes_price=0.5,
        yes_token_id="tok",
        buy_price=0.5,
        share_count=10,
        neg_risk=False,
        tick_size="0.01",
        order_min_size=5,
        strategy="highest_yes",
        event={"event_date": "2026-07-31", "resolutionSource": "https://example.com"},
    )
    fake = ForecastMaxTemp(
        temp_f=90,
        temp_c=32,
        source="open_meteo",
        icao="RJTT",
        wu_temp_f=91,
        wu_temp_c=33,
    )
    client = MagicMock()
    client.fetch_forecast_max_temp.return_value = fake
    attach_forecasts_to_selections([sel], weather_client=client)
    assert sel.forecast_temp_f == 90
    assert sel.forecast_temp_c == 32
    assert sel.forecast_source == "open_meteo"
    assert sel.forecast_wu_temp_c == 33
    client.fetch_forecast_max_temp.assert_called_once()


def test_attach_forecasts_to_skipped():
    skipped = [
        {
            "event_id": "1",
            "city": "Tokyo",
            "reason": "yes_price_max",
            "group_item_title": "30°C",
            "event_slug": "highest-temperature-in-tokyo-on-july-31-2026",
        }
    ]
    fake = ForecastMaxTemp(
        temp_f=90,
        temp_c=32,
        source="open_meteo",
        wu_temp_f=88,
        wu_temp_c=31,
    )
    client = MagicMock()
    client.fetch_forecast_max_temp.return_value = fake
    events = [
        {
            "id": "1",
            "city": "Tokyo",
            "event_date": "2026-07-31",
            "resolutionSource": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT",
        }
    ]
    attach_forecasts_to_skipped(skipped, events=events, weather_client=client)
    assert skipped[0]["forecast_temp_c"] == 32
    assert skipped[0]["forecast_wu_temp_c"] == 31
    assert skipped[0]["forecast_delta_c"] == pytest.approx(2.0)

def test_enrichment_fills_forecast_temps_and_delta():
    rec = TradeRecord(
        date="2026-07-31",
        city="Tokyo",
        bought_temp="30°C",
        trade_window="14:00–16:00",
        bought_at="2026-07-31T06:00:00+00:00",
        sold_at=None,
        redeemed_at=None,
        shares=10.0,
        result="open",
        final_value_usd=None,
        winning_temp=None,
        win_temp_vs_bought="unknown",
        price_drop_below_threshold_at=None,
        sold_but_would_have_won=False,
        buy_price=0.5,
        sell_price=None,
        cost_basis_usd=5.0,
        realized_pnl_usd=None,
        roi_pct=None,
        sell_value_pct=None,
        held_hours=None,
        event_slug="highest-temperature-in-tokyo-on-july-31-2026",
        token_id="tok1",
        condition_id="cond1",
        transaction_hash=None,
    )
    filled = apply_enrichment_to_record(
        rec,
        {"forecast_temp_f": 90, "forecast_temp_c": 32},
    )
    assert "forecast_temp_c" in filled or rec.forecast_temp_c == 32
    assert rec.forecast_temp_c == 32
    assert rec.forecast_temp_f == 90
    assert rec.forecast_delta_c == pytest.approx(2.0)
    assert forecast_delta_c("30°C", forecast_temp_c=32) == 2.0


def test_apply_selection_pattern_fields_forecast_only():
    rec = TradeRecord(
        date="2026-07-31",
        city="Tokyo",
        bought_temp="28°C",
        trade_window="14:00–16:00",
        bought_at="2026-07-31T06:00:00+00:00",
        sold_at=None,
        redeemed_at=None,
        shares=10.0,
        result="open",
        final_value_usd=None,
        winning_temp=None,
        win_temp_vs_bought="unknown",
        price_drop_below_threshold_at=None,
        sold_but_would_have_won=False,
        buy_price=0.5,
        sell_price=None,
        cost_basis_usd=5.0,
        realized_pnl_usd=None,
        roi_pct=None,
        sell_value_pct=None,
        held_hours=None,
        event_slug="slug",
        token_id="t2",
        condition_id="c2",
        transaction_hash=None,
    )
    filled = apply_selection_pattern_fields(
        rec, {"forecast_temp_c": 31, "forecast_temp_f": 88}
    )
    assert "forecast_temp_c" in filled
    assert "forecast_delta_c" in filled
    assert rec.forecast_temp_c == 31
    assert rec.forecast_delta_c == 3.0
