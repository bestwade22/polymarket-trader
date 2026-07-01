"""Tests for stop-loss runner."""

from unittest.mock import MagicMock, patch

import pytest

from src.api.data_client import LivePosition
from src.trade.stop_loss_runner import (
    resolve_event_for_position,
    run_stop_loss_check,
    selection_from_position,
)


def _position(**kwargs) -> LivePosition:
    defaults = {
        "token_id": "tok1",
        "market_id": "m1",
        "size": 10.0,
        "avg_price": 0.60,
        "title": "72-73°F",
        "event_slug": "highest-temperature-in-nyc-on-june-28",
        "event_id": "ev1",
        "condition_id": "m1",
    }
    defaults.update(kwargs)
    return LivePosition(**defaults)


class TestStopLossRunner:
    @patch("src.trade.stop_loss_runner.fetch_user_positions")
    def test_no_positions_skips(self, mock_fetch):
        mock_fetch.return_value = []
        result = run_stop_loss_check(dry_run=True, wallet_address="0xabc")
        assert result["status"] == "skipped"
        assert result["reason"] == "no_positions"

    @patch("src.trade.stop_loss_runner.save_stop_loss_run")
    @patch("src.trade.stop_loss_runner.TradeExecutor")
    @patch("src.trade.stop_loss_runner.refresh_market_prices")
    @patch("src.trade.stop_loss_runner.resolve_event_for_position")
    @patch("src.trade.stop_loss_runner.fetch_user_positions")
    def test_skips_non_temp_before_refresh(
        self,
        mock_fetch,
        mock_resolve,
        mock_refresh_market,
        mock_executor_cls,
        mock_save,
    ):
        mock_fetch.return_value = [
            _position(event_slug="will-x-win-election", title="Will X win?")
        ]
        mock_save.side_effect = lambda r: __import__("pathlib").Path("/tmp/run.json")
        mock_executor_cls.return_value = MagicMock(dry_run=True)

        result = run_stop_loss_check(dry_run=True, wallet_address="0xabc")

        assert result["status"] == "ok"
        assert any(s["reason"] == "not_temp_market" for s in result["skipped"])
        mock_resolve.assert_not_called()
        mock_refresh_market.assert_not_called()

    @patch("src.trade.stop_loss_runner.save_stop_loss_run")
    @patch("src.trade.stop_loss_runner.TradeExecutor")
    @patch("src.trade.stop_loss_runner.LiveOpenOrderChecker")
    @patch("src.trade.stop_loss_runner.get_sell_price", return_value=0.30)
    @patch("src.trade.stop_loss_runner.refresh_market_prices")
    @patch("src.trade.stop_loss_runner.resolve_event_for_position")
    @patch("src.trade.stop_loss_runner.fetch_user_positions")
    def test_sells_below_threshold(
        self,
        mock_fetch,
        mock_resolve,
        mock_refresh_market,
        _sell_price,
        mock_open_checker_cls,
        mock_executor_cls,
        mock_save,
    ):
        market = {
            "id": "m1",
            "groupItemTitle": "72-73°F",
            "clobTokenIds": '["tok1"]',
            "orderMinSize": 5,
            "orderPriceMinTickSize": "0.01",
        }
        event = {
            "id": "ev1",
            "title": "Highest temperature in NYC on June 28?",
            "slug": "highest-temperature-in-nyc-on-june-28",
            "city": "NYC",
            "event_date": "2026-06-28",
            "timezone": "America/New_York",
            "markets": [market],
        }
        mock_fetch.return_value = [_position()]
        mock_resolve.return_value = event
        mock_refresh_market.return_value = market
        mock_save.side_effect = lambda r: __import__("pathlib").Path("/tmp/run.json")
        open_checker = MagicMock()
        open_checker.token_has_open_sell_order.return_value = (False, [])
        mock_open_checker_cls.return_value = open_checker

        executor = MagicMock(dry_run=True)
        executor.sell_yes.return_value = {
            "dry_run": True,
            "side": "SELL",
            "price": 0.30,
            "size": 10,
        }
        mock_executor_cls.return_value = executor

        with patch(
            "src.trade.stop_loss_runner.is_stop_loss_local_time_eligible",
            return_value=(True, "ok"),
        ):
            result = run_stop_loss_check(dry_run=True, wallet_address="0xabc")

        assert len(result["sold"]) == 1
        executor.sell_yes.assert_called_once()
        mock_refresh_market.assert_called_once()

    @patch("src.trade.stop_loss_runner.save_stop_loss_run")
    @patch("src.trade.stop_loss_runner.TradeExecutor")
    @patch("src.trade.stop_loss_runner.LiveOpenOrderChecker")
    @patch("src.trade.stop_loss_runner.get_sell_price", return_value=0.35)
    @patch("src.trade.stop_loss_runner.refresh_market_prices")
    @patch("src.trade.stop_loss_runner.resolve_event_for_position")
    @patch("src.trade.stop_loss_runner.fetch_user_positions")
    def test_holds_above_threshold(
        self,
        mock_fetch,
        mock_resolve,
        mock_refresh_market,
        _sell_price,
        mock_open_checker_cls,
        mock_executor_cls,
        mock_save,
    ):
        market = {
            "id": "m1",
            "groupItemTitle": "72-73°F",
            "clobTokenIds": '["tok1"]',
        }
        event = {
            "id": "ev1",
            "slug": "highest-temperature-in-nyc-on-june-28",
            "event_date": "2026-06-28",
            "timezone": "America/New_York",
            "markets": [market],
        }
        mock_fetch.return_value = [_position(avg_price=0.60)]
        mock_resolve.return_value = event
        mock_refresh_market.return_value = market
        mock_save.side_effect = lambda r: __import__("pathlib").Path("/tmp/run.json")
        open_checker = MagicMock()
        open_checker.token_has_open_sell_order.return_value = (False, [])
        mock_open_checker_cls.return_value = open_checker
        mock_executor_cls.return_value = MagicMock(dry_run=True)

        with patch(
            "src.trade.stop_loss_runner.is_stop_loss_local_time_eligible",
            return_value=(True, "ok"),
        ):
            result = run_stop_loss_check(dry_run=True, wallet_address="0xabc")

        assert result["sold"] == []
        assert any(s["reason"] == "above_threshold" for s in result["skipped"])

    @patch("src.trade.stop_loss_runner.save_stop_loss_run")
    @patch("src.trade.stop_loss_runner.TradeExecutor")
    @patch("src.trade.stop_loss_runner.LiveOpenOrderChecker")
    @patch("src.trade.stop_loss_runner.get_sell_price", return_value=0.30)
    @patch("src.trade.stop_loss_runner.refresh_market_prices")
    @patch("src.trade.stop_loss_runner.resolve_event_for_position")
    @patch("src.trade.stop_loss_runner.fetch_user_positions")
    def test_skips_when_open_sell_order_exists(
        self,
        mock_fetch,
        mock_resolve,
        mock_refresh_market,
        _sell_price,
        mock_open_checker_cls,
        mock_executor_cls,
        mock_save,
    ):
        market = {
            "id": "m1",
            "groupItemTitle": "72-73°F",
            "clobTokenIds": '["tok1"]',
            "orderMinSize": 5,
            "orderPriceMinTickSize": "0.01",
        }
        event = {
            "id": "ev1",
            "title": "Highest temperature in NYC on June 28?",
            "slug": "highest-temperature-in-nyc-on-june-28",
            "city": "NYC",
            "event_date": "2026-06-28",
            "timezone": "America/New_York",
            "markets": [market],
        }
        mock_fetch.return_value = [_position()]
        mock_resolve.return_value = event
        mock_refresh_market.return_value = market
        mock_save.side_effect = lambda r: __import__("pathlib").Path("/tmp/run.json")

        open_checker = MagicMock()
        open_checker.token_has_open_sell_order.return_value = (
            True,
            [{"id": "order-s1", "asset_id": "tok1", "side": "SELL"}],
        )
        mock_open_checker_cls.return_value = open_checker

        executor = MagicMock(dry_run=True)
        mock_executor_cls.return_value = executor

        with patch(
            "src.trade.stop_loss_runner.is_stop_loss_local_time_eligible",
            return_value=(True, "ok"),
        ):
            result = run_stop_loss_check(dry_run=True, wallet_address="0xabc")

        assert result["sold"] == []
        assert any(s["reason"] == "open_sell_order" for s in result["skipped"])
        executor.sell_yes.assert_not_called()

    @patch("src.trade.stop_loss_runner.save_stop_loss_run")
    @patch("src.trade.stop_loss_runner.TradeExecutor")
    @patch("src.trade.stop_loss_runner.resolve_event_for_position")
    @patch("src.trade.stop_loss_runner.fetch_user_positions")
    def test_skips_before_min_local_time(
        self,
        mock_fetch,
        mock_resolve,
        mock_executor_cls,
        mock_save,
    ):
        market = {
            "id": "m1",
            "groupItemTitle": "72-73°F",
            "clobTokenIds": '["tok1"]',
        }
        event = {
            "id": "ev1",
            "slug": "highest-temperature-in-nyc-on-june-28",
            "event_date": "2026-06-28",
            "timezone": "America/New_York",
            "markets": [market],
        }
        mock_fetch.return_value = [_position()]
        mock_resolve.return_value = event
        mock_save.side_effect = lambda r: __import__("pathlib").Path("/tmp/run.json")
        mock_executor_cls.return_value = MagicMock(dry_run=True)

        with patch(
            "src.trade.stop_loss_runner.is_stop_loss_local_time_eligible",
            return_value=(False, "before_min_local_time"),
        ):
            result = run_stop_loss_check(dry_run=True, wallet_address="0xabc")

        assert result["sold"] == []
        assert any(s["reason"] == "before_min_local_time" for s in result["skipped"])


class TestSelectionFromPosition:
    def test_builds_selection(self):
        event = {"id": "ev1", "city": "NYC"}
        market = {
            "id": "m1",
            "groupItemTitle": "72-73°F",
            "clobTokenIds": '["tok1"]',
            "orderMinSize": 5,
            "orderPriceMinTickSize": "0.01",
        }
        pos = _position()
        sel = selection_from_position(event, market, pos, 10.0)
        assert sel.yes_token_id == "tok1"
        assert sel.share_count >= 5
        assert sel.strategy == "stop_loss"
