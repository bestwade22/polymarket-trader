"""Tests for events-based trade gate and lightweight gate data fetch."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _sample_event(event_date: str, tz: str, city: str) -> dict:
    return {
        "id": f"{city}-{event_date}",
        "city": city,
        "event_date": event_date,
        "timezone": tz,
    }


class TestShouldRunTradeEvents:
    def test_no_tradable_events_in_file_returns_false(self, tmp_path: Path):
        import importlib.util

        path = PROJECT_ROOT / "scripts" / "should_run_trade.py"
        spec = importlib.util.spec_from_file_location("should_run_trade", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        from src.utils.time_window import trading_window_bounds_utc

        bounds = trading_window_bounds_utc(
            "2026-06-28",
            "America/New_York",
            start_hour=12,
            start_minute=0,
            end_hour=14,
            end_minute=0,
        )
        assert bounds is not None
        _start, _end = bounds
        # 22:00 UTC — NYC Jun 28 window (16–18 UTC) has passed
        now = datetime(2026, 6, 28, 22, 0, tzinfo=timezone.utc)
        events_path = tmp_path / "events_2026-06-28.json"
        events_path.write_text(json.dumps([_sample_event("2026-06-28", "America/New_York", "NYC")]))

        assert mod.should_run_trade(now_utc=now, data_dir=tmp_path) is False

    def test_tradable_event_in_file_returns_true(self, tmp_path: Path, monkeypatch):
        from config import settings as settings_mod

        monkeypatch.setattr(settings_mod.settings, "trading_window_start_hour", 12)
        monkeypatch.setattr(settings_mod.settings, "trading_window_start_minute", 30)
        monkeypatch.setattr(settings_mod.settings, "trading_window_end_hour", 14)
        monkeypatch.setattr(settings_mod.settings, "trading_window_end_minute", 30)
        import importlib.util

        path = PROJECT_ROOT / "scripts" / "should_run_trade.py"
        spec = importlib.util.spec_from_file_location("should_run_trade", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        from src.utils.time_window import trading_window_bounds_utc

        bounds = trading_window_bounds_utc(
            "2026-06-28",
            "America/New_York",
            start_hour=12,
            start_minute=30,
            end_hour=14,
            end_minute=30,
        )
        assert bounds is not None
        start, _end = bounds
        now = start + timedelta(minutes=15)
        events_path = tmp_path / f"events_{now.date().isoformat()}.json"
        events_path.write_text(json.dumps([_sample_event("2026-06-28", "America/New_York", "NYC")]))

        assert mod.should_run_trade(now_utc=now, data_dir=tmp_path) is True
        assert mod.tradable_event_file_dates(now_utc=now, data_dir=tmp_path) == [now.date().isoformat()]


class TestDescribeEventGate:
    def test_in_window_event(self, monkeypatch):
        from config import settings as settings_mod

        monkeypatch.setattr(settings_mod.settings, "trading_window_start_hour", 12)
        monkeypatch.setattr(settings_mod.settings, "trading_window_start_minute", 30)
        monkeypatch.setattr(settings_mod.settings, "trading_window_end_hour", 14)
        monkeypatch.setattr(settings_mod.settings, "trading_window_end_minute", 30)

        import importlib.util

        path = PROJECT_ROOT / "scripts" / "should_run_trade.py"
        spec = importlib.util.spec_from_file_location("should_run_trade", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        from src.utils.time_window import trading_window_bounds_utc

        bounds = trading_window_bounds_utc("2026-06-28", "America/New_York")
        assert bounds is not None
        start, _end = bounds
        now = start + timedelta(minutes=15)
        event = _sample_event("2026-06-28", "America/New_York", "NYC")

        detail = mod.describe_event_gate(event, now)
        assert detail["tradable"] is True
        assert detail["reason"] == "in_window"
        assert detail["local_now"].startswith("2026-06-28")

    def test_after_window_event(self, monkeypatch):
        from config import settings as settings_mod

        monkeypatch.setattr(settings_mod.settings, "trading_window_start_hour", 12)
        monkeypatch.setattr(settings_mod.settings, "trading_window_start_minute", 30)
        monkeypatch.setattr(settings_mod.settings, "trading_window_end_hour", 14)
        monkeypatch.setattr(settings_mod.settings, "trading_window_end_minute", 30)

        import importlib.util

        path = PROJECT_ROOT / "scripts" / "should_run_trade.py"
        spec = importlib.util.spec_from_file_location("should_run_trade", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        from src.utils.time_window import trading_window_bounds_utc

        bounds = trading_window_bounds_utc("2026-06-28", "America/New_York")
        assert bounds is not None
        _start, end = bounds
        now = end + timedelta(minutes=5)
        event = _sample_event("2026-06-28", "America/New_York", "NYC")

        detail = mod.describe_event_gate(event, now)
        assert detail["tradable"] is False
        assert detail["reason"] == "after_window"


class TestGateDataFetch:
    @patch("lambda_handlers.gate_data.requests.get")
    def test_fetch_skips_404(self, mock_get, tmp_path):
        from lambda_handlers import gate_data

        gate_data.GATE_DATA_DIR = tmp_path
        missing = MagicMock(status_code=404)
        mock_get.return_value = missing

        gate_data.fetch_events_for_gate("pat", "owner/repo", "main", ["2026-06-28"])
        assert not (tmp_path / "events_2026-06-28.json").exists()

    @patch("lambda_handlers.gate_data.requests.get")
    def test_fetch_writes_file(self, mock_get, tmp_path):
        from lambda_handlers import gate_data

        gate_data.GATE_DATA_DIR = tmp_path
        ok = MagicMock(status_code=200, text='[{"id": "1"}]')
        ok.raise_for_status = MagicMock()
        mock_get.return_value = ok

        gate_data.fetch_events_for_gate("pat", "owner/repo", "main", ["2026-06-28"])
        assert json.loads((tmp_path / "events_2026-06-28.json").read_text()) == [{"id": "1"}]
