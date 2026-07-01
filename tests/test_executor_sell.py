"""Tests for TradeExecutor.sell_yes."""

from unittest.mock import MagicMock, patch

import pytest

from src.trade.executor import TradeExecutor
from src.trade.strategies.base import MarketSelection


def _selection() -> MarketSelection:
    return MarketSelection(
        event_id="1",
        city="NYC",
        market_id="100",
        group_item_title="72-73°F",
        yes_price=0.5,
        yes_token_id="token1",
        buy_price=0.5,
        share_count=10,
        neg_risk=False,
        tick_size="0.01",
        order_min_size=5,
        strategy="stop_loss",
        market={"id": "100", "midpoint": 0.24, "bestBid": 0.23},
    )


class TestSellYes:
    @patch.object(TradeExecutor, "_resolve_sell_price", return_value=0.24)
    def test_dry_run_sell(self, _price):
        executor = TradeExecutor(dry_run=True)
        result = executor.sell_yes(_selection(), share_count=10)
        assert result["dry_run"] is True
        assert result["side"] == "SELL"
        assert result["price"] == 0.24
        assert result["size"] == 10
        assert result["token_id"] == "token1"

    @patch.object(TradeExecutor, "_place_order")
    @patch.object(TradeExecutor, "_resolve_sell_price", return_value=0.24)
    def test_live_sell_calls_place_order(self, _price, mock_place):
        mock_place.return_value = {"dry_run": False, "side": "SELL", "order_id": "abc"}
        executor = TradeExecutor(dry_run=False)
        result = executor.sell_yes(_selection(), share_count=8)
        mock_place.assert_called_once()
        assert mock_place.call_args.kwargs["side_name"] == "SELL"
        assert mock_place.call_args.kwargs["share_count"] == 8.0
        assert result["order_id"] == "abc"

    @patch("src.trade.executor.compute_order_expiration", return_value=(999, "GTD"))
    @patch.object(TradeExecutor, "_resolve_sell_price", return_value=0.24)
    def test_sell_uses_stop_loss_order_expiry(self, _price, mock_expiry):
        from config.settings import settings

        executor = TradeExecutor(dry_run=True)
        executor.sell_yes(_selection(), share_count=10)
        mock_expiry.assert_called_once_with(settings.stop_loss_order_expiry_hours)

    @patch("src.trade.executor._import_clob")
    @patch.object(TradeExecutor, "_get_client")
    def test_place_order_clamps_low_price_to_min(self, mock_get_client, mock_import):
        fake_client = MagicMock()
        fake_client.create_and_post_order.return_value = {"orderID": "o1", "status": "live"}
        mock_get_client.return_value = fake_client

        class FakeOrderArgs:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeOrderType:
            GTD = "GTD"
            GTC = "GTC"

        class FakePartialOptions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        mock_import.return_value = (
            "v2",
            object(),
            FakeOrderArgs,
            FakeOrderType,
            FakePartialOptions,
            "BUY",
            "SELL",
            None,
        )

        executor = TradeExecutor(dry_run=False)
        result = executor._place_order(
            selection=_selection(),
            order_price=0.0005,
            share_count=10.0,
            side_name="SELL",
            order_type_name="GTD",
            expiration=1,
            expires_at="2026-01-01T00:00:00+00:00",
        )

        call_args = fake_client.create_and_post_order.call_args.args[0]
        assert call_args.kwargs["price"] == 0.001
        assert result["price"] == 0.001
