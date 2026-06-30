"""Tests for configurable trading window."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import _parse_trading_window_time
from src.utils.time_window import (
    any_city_in_trading_window,
    is_event_in_trading_window,
    is_event_tradable_now,
    is_in_trading_window,
    trading_window_bounds_utc,
    trading_window_duration,
    trading_window_label,
)


def test_parse_trading_window_time_formats():
    assert _parse_trading_window_time("12", "TEST", allow_hour_24=False) == (12, 0)
    assert _parse_trading_window_time("12:30", "TEST", allow_hour_24=False) == (12, 30)
    assert _parse_trading_window_time("1230", "TEST", allow_hour_24=False) == (12, 30)
    assert _parse_trading_window_time("24:00", "TEST", allow_hour_24=True) == (24, 0)


def test_trading_window_bounds_default_hours():
    start, end = trading_window_bounds_utc(
        "2026-06-19",
        "America/New_York",
        start_hour=12,
        start_minute=0,
        end_hour=14,
        end_minute=0,
    )
    assert start is not None and end is not None
    assert end - start == timedelta(hours=2)


def test_trading_window_bounds_custom_hours():
    start, end = trading_window_bounds_utc(
        "2026-06-19",
        "America/New_York",
        start_hour=10,
        start_minute=0,
        end_hour=16,
        end_minute=0,
    )
    assert start is not None and end is not None
    assert end - start == timedelta(hours=6)


def test_trading_window_bounds_with_minutes():
    start, end = trading_window_bounds_utc(
        "2026-06-19",
        "America/New_York",
        start_hour=12,
        start_minute=30,
        end_hour=15,
        end_minute=0,
    )
    assert start is not None and end is not None
    assert end - start == timedelta(hours=2, minutes=30)
    local_start = start.astimezone(ZoneInfo("America/New_York"))
    local_end = end.astimezone(ZoneInfo("America/New_York"))
    assert local_start.hour == 12 and local_start.minute == 30
    assert local_end.hour == 15 and local_end.minute == 0


def test_trading_window_duration_with_minutes():
    duration = trading_window_duration(
        start_hour=12,
        start_minute=30,
        end_hour=15,
        end_minute=0,
    )
    assert duration == timedelta(hours=2, minutes=30)


def test_is_event_in_trading_window_uses_event_timezone():
    event = {
        "event_date": "2026-06-19",
        "timezone": "America/New_York",
        "city": "NYC",
    }
    bounds = trading_window_bounds_utc(
        event["event_date"],
        event["timezone"],
        start_hour=12,
        start_minute=0,
        end_hour=14,
        end_minute=0,
    )
    assert bounds is not None
    start, _end = bounds
    assert is_event_in_trading_window(event, now_utc=start + timedelta(minutes=30))
    assert not is_event_in_trading_window(event, now_utc=start - timedelta(minutes=1))


def test_is_in_trading_window_legacy_city_noon_utc():
    noon = datetime(2026, 6, 19, 16, 0, tzinfo=timezone.utc)
    assert is_in_trading_window(noon.isoformat(), now_utc=noon + timedelta(hours=1))
    assert not is_in_trading_window(noon.isoformat(), now_utc=noon + timedelta(hours=3))


def test_trading_window_label():
    assert trading_window_label(12, 0, 14, 0) == "12:00–14:00"
    assert trading_window_label(12, 30, 15, 0) == "12:30–15:00"


def test_any_city_in_trading_window_matches_one_timezone():
    bounds = trading_window_bounds_utc(
        "2026-06-19",
        "America/New_York",
        start_hour=12,
        start_minute=0,
        end_hour=14,
        end_minute=0,
    )
    assert bounds is not None
    start, _end = bounds
    assert any_city_in_trading_window(
        ["America/Los_Angeles", "America/New_York"],
        ["2026-06-19"],
        now_utc=start + timedelta(minutes=30),
    )
    assert not any_city_in_trading_window(
        ["America/New_York"],
        ["2026-06-19"],
        now_utc=start - timedelta(hours=2),
    )


def _patch_trading_window(monkeypatch, start_h=12, start_m=30, end_h=14, end_m=30):
    from config import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "trading_window_start_hour", start_h)
    monkeypatch.setattr(settings_mod.settings, "trading_window_start_minute", start_m)
    monkeypatch.setattr(settings_mod.settings, "trading_window_end_hour", end_h)
    monkeypatch.setattr(settings_mod.settings, "trading_window_end_minute", end_m)


def test_is_event_tradable_now_matches_trading_window(monkeypatch):
    _patch_trading_window(monkeypatch)
    event = {
        "event_date": "2026-06-19",
        "timezone": "America/New_York",
        "city": "NYC",
    }
    bounds = trading_window_bounds_utc(
        event["event_date"],
        event["timezone"],
        start_hour=12,
        start_minute=30,
        end_hour=14,
        end_minute=30,
    )
    assert bounds is not None
    start, end = bounds
    assert is_event_tradable_now(event, now_utc=start + timedelta(minutes=10))
    assert is_event_tradable_now(event, now_utc=start + timedelta(minutes=40))
    assert is_event_tradable_now(event, now_utc=end)
    assert not is_event_tradable_now(event, now_utc=start - timedelta(minutes=1))
    assert not is_event_tradable_now(event, now_utc=end + timedelta(minutes=1))


def test_should_run_trade_script(tmp_path: Path, monkeypatch):
    _patch_trading_window(monkeypatch)
    import importlib.util

    path = PROJECT_ROOT / "scripts" / "should_run_trade.py"
    spec = importlib.util.spec_from_file_location("should_run_trade", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    bounds = trading_window_bounds_utc(
        "2026-06-19",
        "Asia/Seoul",
        start_hour=12,
        start_minute=30,
        end_hour=14,
        end_minute=30,
    )
    assert bounds is not None
    start, _end = bounds
    event = {"event_date": "2026-06-19", "timezone": "Asia/Seoul", "city": "Seoul"}
    in_slot = start + timedelta(minutes=15)
    events_path = tmp_path / f"events_{in_slot.date().isoformat()}.json"
    events_path.write_text(
        __import__("json").dumps([event])
    )
    assert mod.should_run_trade(now_utc=in_slot, data_dir=tmp_path) is True
    assert mod.should_run_trade(now_utc=start + timedelta(minutes=45), data_dir=tmp_path) is True
    assert mod.should_run_trade(now_utc=start - timedelta(minutes=1), data_dir=tmp_path) is False


if __name__ == "__main__":
    test_parse_trading_window_time_formats()
    test_trading_window_bounds_default_hours()
    test_trading_window_bounds_custom_hours()
    test_trading_window_bounds_with_minutes()
    test_trading_window_duration_with_minutes()
    test_is_event_in_trading_window_uses_event_timezone()
    test_is_in_trading_window_legacy_city_noon_utc()
    test_trading_window_label()
    test_any_city_in_trading_window_matches_one_timezone()
    test_is_event_tradable_now_matches_trading_window()
    test_should_run_trade_script()
    print("All tests passed.")
