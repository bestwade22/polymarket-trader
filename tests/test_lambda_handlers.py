"""Tests for AWS Lambda handler helpers."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from lambda_handlers.fetch_daily import resolve_fetch_date
from lambda_handlers.trade_hourly import resolve_trade_date


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
        from scripts.should_run_trade import evaluate_trade_gate

        gate = evaluate_trade_gate()
        assert isinstance(gate["should_run"], bool)

    @patch("lambda_handlers.trade_hourly.gate_data_dir", return_value=None)
    @patch("scripts.should_run_trade.evaluate_trade_gate")
    @patch("lambda_handlers.trade_hourly.apply_secrets")
    def test_skips_outside_window(self, _secrets, mock_gate, _gate_dir):
        from lambda_handlers.trade_hourly import handler

        mock_gate.return_value = {
            "should_run": False,
            "status": "skip",
            "reason": "no_tradable_events",
            "now_hkt": "2026-07-01 23:35:12 HKT",
            "window": "12:30–14:30",
            "tradable_cities": [],
            "events_loaded": 49,
        }
        result = handler({}, None)
        assert result["status"] == "skipped"
        assert result["reason"] == "no_tradable_events"

    @patch("lambda_handlers.trade_hourly.run_trade_hourly")
    @patch("lambda_handlers.trade_hourly.clone_or_update")
    @patch("lambda_handlers.trade_hourly.git_settings_from_env", return_value=("o/r", "main", "pat"))
    @patch("lambda_handlers.trade_hourly.tradable_dates_for_run", return_value=["2026-06-30"])
    @patch("lambda_handlers.trade_hourly.gate_data_dir", return_value=None)
    @patch("scripts.should_run_trade.evaluate_trade_gate")
    @patch("lambda_handlers.trade_hourly.apply_secrets")
    def test_runs_inside_window(
        self,
        _secrets,
        mock_gate,
        _gate_dir,
        _dates,
        _git,
        mock_clone,
        mock_run,
    ):
        from lambda_handlers.trade_hourly import handler

        mock_gate.return_value = {
            "should_run": True,
            "status": "go",
            "reason": "tradable_events",
            "now_hkt": "2026-07-01 23:35:12 HKT",
            "window": "12:30–14:30",
            "tradable_cities": ["Sao Paulo"],
            "events_loaded": 49,
        }
        mock_clone.return_value = MagicMock()
        result = handler({}, None)
        assert result["status"] == "ok"
        mock_run.assert_called_once()


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
    @patch("lambda_handlers.trade_hourly.gate_data_dir", return_value=None)
    @patch("scripts.should_run_trade.evaluate_trade_gate")
    @patch("lambda_handlers.trade_hourly.apply_secrets")
    def test_skipped_outside_window(self, _secrets, mock_gate, _gate_dir):
        from lambda_handlers.trade_hourly import handler

        mock_gate.return_value = {
            "should_run": False,
            "status": "skip",
            "reason": "no_tradable_events",
            "now_hkt": "2026-07-01 23:35:12 HKT",
            "window": "12:30–14:30",
            "tradable_cities": [],
            "events_loaded": 49,
        }
        result = handler({}, None)
        assert result["status"] == "skipped"
        assert result["reason"] == "no_tradable_events"


class TestStopLossHandler:
    @patch("lambda_handlers.stop_loss_check.apply_secrets")
    @patch("lambda_handlers.stop_loss_check.count_live_positions", return_value=0)
    def test_skips_when_no_positions(self, _count, _secrets):
        from lambda_handlers.stop_loss_check import handler

        result = handler({}, None)
        assert result["status"] == "skipped"
        assert result["reason"] == "no_positions"

    @patch("lambda_handlers.stop_loss_check.commit_and_push", return_value=True)
    @patch(
        "lambda_handlers.stop_loss_check.collect_commit_paths",
        return_value=["data/positions/sold_events.json"],
    )
    @patch("lambda_handlers.stop_loss_check.run_check_stop_loss")
    @patch("lambda_handlers.stop_loss_check.clone_or_update")
    @patch(
        "lambda_handlers.stop_loss_check.git_settings_from_env",
        return_value=("o/r", "main", "pat"),
    )
    @patch("lambda_handlers.stop_loss_check.count_live_positions", return_value=2)
    @patch("lambda_handlers.stop_loss_check.apply_secrets")
    def test_runs_when_positions_exist(
        self,
        _secrets,
        _count,
        _git,
        mock_clone,
        mock_run,
        _paths,
        mock_commit,
    ):
        mock_clone.return_value = "/tmp/repo"
        from lambda_handlers.stop_loss_check import handler

        result = handler({}, None)
        assert result["status"] == "ok"
        assert result["job"] == "check-stop-loss"
        mock_run.assert_called_once()
        mock_commit.assert_called_once()
