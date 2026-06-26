"""Tests for configurable trading window."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import _parse_trading_window_time
from src.utils.time_window import (
    is_event_in_trading_window,
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


if __name__ == "__main__":
    test_parse_trading_window_time_formats()
    test_trading_window_bounds_default_hours()
    test_trading_window_bounds_custom_hours()
    test_trading_window_bounds_with_minutes()
    test_trading_window_duration_with_minutes()
    test_is_event_in_trading_window_uses_event_timezone()
    test_is_in_trading_window_legacy_city_noon_utc()
    test_trading_window_label()
    print("All tests passed.")
