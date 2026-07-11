"""Tests for sell-win runner."""

from datetime import time
from unittest.mock import MagicMock, patch

from src.api.data_client import LivePosition
from src.trade.sell_win import SellWinTier
from src.trade.sell_win_runner import run_sell_win_check


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


class TestSellWinRunner:
    @patch("src.trade.sell_win_runner.fetch_user_positions")
    def test_no_positions_skips(self, mock_fetch):
        mock_fetch.return_value = []
        result = run_sell_win_check(dry_run=True, wallet_address="0xabc")
        assert result["status"] == "skipped"
        assert result["reason"] == "no_positions"

    @patch("src.trade.sell_win_runner.TradeExecutor")
    @patch("src.trade.sell_win_runner.LiveOpenOrderChecker")
    @patch("src.trade.sell_win_runner.get_sell_price", return_value=0.88)
    @patch("src.trade.sell_win_runner.refresh_market_prices")
    @patch("src.trade.sell_win_runner.resolve_event_for_position")
    @patch("src.trade.sell_win_runner.fetch_user_positions")
    def test_places_tier1_sell_order(
        self,
        mock_fetch,
        mock_resolve,
        mock_refresh_market,
        _sell_price,
        mock_open_checker_cls,
        mock_executor_cls,
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
        open_checker = MagicMock()
        open_checker.token_has_open_sell_order.return_value = (False, [])
        mock_open_checker_cls.return_value = open_checker

        executor = MagicMock(dry_run=True)
        executor.sell_yes.return_value = {
            "dry_run": True,
            "side": "SELL",
            "price": 0.91,
            "size": 10,
        }
        mock_executor_cls.return_value = executor

        with patch(
            "src.trade.sell_win_runner.active_sell_win_tier",
            return_value=(
                SellWinTier("tier1", time(15, 0), 0.91, time(15, 55)),
                "ok",
            ),
        ), patch(
            "src.trade.sell_win_runner.sell_win_expiration_utc",
            return_value=1_900_000_000,
        ):
            result = run_sell_win_check(dry_run=True, wallet_address="0xabc")

        assert len(result["placed"]) == 1
        executor.sell_yes.assert_called_once()
        call_kwargs = executor.sell_yes.call_args.kwargs
        assert call_kwargs["order_price"] == 0.91
        assert call_kwargs["expiration_ts"] == 1_900_000_000

    @patch("src.trade.sell_win_runner.TradeExecutor")
    @patch("src.trade.sell_win_runner.LiveOpenOrderChecker")
    @patch("src.trade.sell_win_runner.get_sell_price", return_value=0.95)
    @patch("src.trade.sell_win_runner.refresh_market_prices")
    @patch("src.trade.sell_win_runner.resolve_event_for_position")
    @patch("src.trade.sell_win_runner.fetch_user_positions")
    def test_skips_when_open_sell_order_exists(
        self,
        mock_fetch,
        mock_resolve,
        mock_refresh_market,
        _sell_price,
        mock_open_checker_cls,
        mock_executor_cls,
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
        open_checker = MagicMock()
        open_checker.token_has_open_sell_order.return_value = (True, [{"id": "ord1"}])
        mock_open_checker_cls.return_value = open_checker
        mock_executor_cls.return_value = MagicMock(dry_run=True)

        with patch(
            "src.trade.sell_win_runner.active_sell_win_tier",
            return_value=(
                SellWinTier("tier1", time(15, 0), 0.91, time(15, 55)),
                "ok",
            ),
        ), patch(
            "src.trade.sell_win_runner.sell_win_expiration_utc",
            return_value=1_900_000_000,
        ):
            result = run_sell_win_check(dry_run=True, wallet_address="0xabc")

        assert result["placed"] == []
        assert any(s["reason"] == "open_sell_order" for s in result["skipped"])

    @patch("src.trade.sell_win_runner.TradeExecutor")
    @patch("src.trade.sell_win_runner.LiveOpenOrderChecker")
    @patch("src.trade.sell_win_runner.get_sell_price", return_value=0.1)
    @patch("src.trade.sell_win_runner.refresh_market_prices")
    @patch("src.trade.sell_win_runner.resolve_event_for_position")
    @patch("src.trade.sell_win_runner.fetch_user_positions")
    def test_skips_when_position_price_at_or_below_threshold(
        self,
        mock_fetch,
        mock_resolve,
        mock_refresh_market,
        _sell_price,
        mock_open_checker_cls,
        mock_executor_cls,
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
        mock_open_checker_cls.return_value = MagicMock()
        mock_executor_cls.return_value = MagicMock(dry_run=True)

        with patch(
            "src.trade.sell_win_runner.active_sell_win_tier",
            return_value=(
                SellWinTier("tier1", time(15, 0), 0.91, time(15, 55)),
                "ok",
            ),
        ):
            result = run_sell_win_check(dry_run=True, wallet_address="0xabc")

        assert result["placed"] == []
        assert any(s["reason"] == "price_too_low" for s in result["skipped"])
        mock_executor_cls.return_value.sell_yes.assert_not_called()
