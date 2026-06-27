"""Tests for AWS Lambda handler helpers."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from lambda_handlers.fetch_daily import resolve_fetch_date
from lambda_handlers.trade_hourly import resolve_trade_date, should_run


class TestResolveFetchDate:
    def test_explicit_date(self):
        assert resolve_fetch_date({"date": "2026-06-14"}) == "2026-06-14"

    def test_default_hkt_today(self):
        fixed = datetime(2026, 6, 27, 15, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        with patch("lambda_handlers.fetch_daily.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            assert resolve_fetch_date({}) == "2026-06-27"


class TestResolveTradeDate:
    def test_explicit_date(self):
        assert resolve_trade_date({"date": "2026-06-19"}) == "2026-06-19"

    def test_default_utc_today(self):
        fixed = datetime(2026, 6, 27, 23, 30, tzinfo=timezone.utc)
        with patch("lambda_handlers.trade_hourly.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            assert resolve_trade_date({}) == "2026-06-27"


class TestShouldRun:
    def test_force_bypasses_gate(self):
        assert should_run(force=True) is True

    @patch("scripts.should_run_trade.should_run_trade", return_value=False)
    def test_skips_outside_window(self, _mock_gate):
        assert should_run(force=False) is False

    @patch("scripts.should_run_trade.should_run_trade", return_value=True)
    def test_runs_inside_window(self, _mock_gate):
        assert should_run(force=False) is True


class TestFetchDailyHandler:
    @patch("lambda_handlers.fetch_daily.commit_and_push", return_value=True)
    @patch("lambda_handlers.fetch_daily.run_fetch_daily")
    @patch("lambda_handlers.fetch_daily.clone_or_update")
    @patch("lambda_handlers.fetch_daily.git_settings_from_env", return_value=("o/r", "main", "pat"))
    @patch("lambda_handlers.fetch_daily.apply_secrets")
    def test_handler_ok(self, _secrets, _git, mock_clone, mock_run, mock_commit):
        mock_clone.return_value = MagicMock()
        from lambda_handlers.fetch_daily import handler

        result = handler({"date": "2026-06-27"}, None)
        assert result["status"] == "ok"
        assert result["date"] == "2026-06-27"
        assert result["committed"] is True
        mock_run.assert_called_once()
        mock_commit.assert_called_once()


class TestTradeHourlyHandler:
    @patch("lambda_handlers.trade_hourly.should_run", return_value=False)
    @patch("lambda_handlers.trade_hourly.apply_secrets")
    def test_skipped_outside_window(self, _secrets, _gate):
        from lambda_handlers.trade_hourly import handler

        result = handler({}, None)
        assert result["status"] == "skipped"
        assert result["reason"] == "outside_trading_window"
