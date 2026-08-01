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
from src.api.weather_client import ForecastMaxTemp, WeatherClient
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


def test_weather_client_uses_map_coords_for_open_meteo(tmp_path: Path, monkeypatch):
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
    mock_om = MagicMock()
    mock_om.raise_for_status = MagicMock()
    mock_om.json.return_value = {"daily": {"temperature_2m_max": [89.6]}}
    mock_wu = MagicMock()
    mock_wu.raise_for_status = MagicMock()
    mock_wu.text = "High: 33 °C Today forecast"

    def fake_get(url, *args, **kwargs):
        if "open-meteo" in str(url) or "open-meteo.com" in str(url):
            return mock_om
        if isinstance(url, str) and "wunderground" in url:
            return mock_wu
        # session.get(OPEN_METEO_URL, params=...) — url is constant
        params = kwargs.get("params") or {}
        if "latitude" in params:
            return mock_om
        return mock_wu

    with patch.object(client.session, "get", side_effect=fake_get) as get:
        result = client.fetch_forecast_max_temp("Tokyo", "2026-07-31")
    assert result is not None
    assert result.source == "open_meteo"
    assert result.temp_f == 90
    assert result.temp_c == 32
    assert result.icao == "RJTT"
    assert result.wu_temp_c == 33
    assert result.wu_temp_f == 91
    assert get.call_count >= 2


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
