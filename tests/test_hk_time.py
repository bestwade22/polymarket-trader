"""Tests for Hong Kong time log helpers."""

from datetime import datetime, timezone

from src.utils.hk_time import format_hk, format_hk_range, utc_clock_label


def test_format_hk():
    dt = datetime(2026, 7, 1, 14, 55, 51, tzinfo=timezone.utc)
    assert format_hk(dt) == "2026-07-01 22:55:51 HKT"


def test_format_hk_range_crosses_midnight():
    start = datetime(2026, 7, 1, 15, 30, tzinfo=timezone.utc)
    end = datetime(2026, 7, 1, 17, 30, tzinfo=timezone.utc)
    assert format_hk_range(start, end) == "23:30–01:30 HKT"


def test_utc_clock_label():
    assert utc_clock_label(6, 0) == "14:00 HKT"
    assert utc_clock_label(0, 30) == "08:30 HKT"
