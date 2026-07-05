"""Tests for trade history analysis."""

import json
from unittest.mock import patch

import pytest

from src.analysis.history_builder import build_trade_record, group_activity_rows
from src.analysis.models import TradeRecord, summarize_records
from src.analysis.resolution import CachedResolution
from src.analysis.strategy_insights import compute_insights
from src.utils.market_parser import compare_temp_buckets, parse_temperature_bucket


def _buy_row(
    token_id: str = "tok1",
    event_slug: str = "highest-temperature-in-london-on-july-5-2026",
    title: str = "28°C",
    ts: int = 1_700_000_000,
    price: float = 0.5,
    size: float = 10.0,
) -> dict:
    return {
        "type": "TRADE",
        "side": "BUY",
        "asset": token_id,
        "conditionId": "0xabc",
        "eventSlug": event_slug,
        "title": title,
        "timestamp": ts,
        "price": price,
        "size": size,
        "transactionHash": "0xtx",
    }


def _sell_row(token_id: str = "tok1", ts: int = 1_700_100_000, price: float = 0.2) -> dict:
    return {
        "type": "TRADE",
        "side": "SELL",
        "asset": token_id,
        "eventSlug": "highest-temperature-in-london-on-july-5-2026",
        "title": "28°C",
        "timestamp": ts,
        "price": price,
        "size": 10.0,
    }


def _redeem_row(token_id: str = "tok1", ts: int = 1_700_200_000) -> dict:
    return {
        "type": "REDEEM",
        "asset": token_id,
        "eventSlug": "highest-temperature-in-london-on-july-5-2026",
        "title": "28°C",
        "timestamp": ts,
    }


class TestTemperatureParsing:
    def test_single_degree_celsius(self):
        assert parse_temperature_bucket("28°C") == (28, 28, "C")

    def test_compare_same(self):
        assert compare_temp_buckets("28°C", "28°C") == "same"

    def test_compare_higher(self):
        assert compare_temp_buckets("28°C", "30°C") == "higher"

    def test_compare_lower(self):
        assert compare_temp_buckets("30°C", "28°C") == "lower"


class TestActivityGrouping:
    def test_groups_multiple_buys(self):
        rows = [
            _buy_row(ts=100, price=0.4, size=5),
            _buy_row(ts=200, price=0.6, size=5),
        ]
        groups = group_activity_rows(rows)
        assert len(groups) == 1
        assert groups[0].shares == 10.0
        assert groups[0].buy_price == pytest.approx(0.5)

    def test_attaches_sell_and_redeem(self):
        rows = [_buy_row(), _sell_row(), _redeem_row()]
        group = group_activity_rows(rows)[0]
        assert len(group.sell_fills) == 1
        assert len(group.redeems) == 1


class TestResultClassification:
    @patch("src.analysis.history_builder.fetch_resolved_event", return_value=None)
    @patch("src.analysis.history_builder.resolve_winning_temp", return_value=None)
    def test_sold_result(self, _win, _event):
        group = group_activity_rows([_buy_row(), _sell_row()])[0]
        rec = build_trade_record(
            group,
            closed_positions=[],
            open_tokens=set(),
            clob_client=None,
            fetch_price_drop=False,
        )
        assert rec.result == "sold"
        assert rec.sell_value_pct == pytest.approx(40.0)

    @patch("src.analysis.history_builder.fetch_resolved_event", return_value=None)
    @patch("src.analysis.history_builder.resolve_winning_temp", return_value=None)
    def test_win_from_redeem(self, _win, _event):
        group = group_activity_rows([_buy_row(), _redeem_row()])[0]
        rec = build_trade_record(
            group,
            closed_positions=[],
            open_tokens=set(),
            clob_client=None,
            fetch_price_drop=False,
        )
        assert rec.result == "win"

    @patch("src.analysis.history_builder.fetch_resolved_event")
    @patch("src.analysis.history_builder.resolve_winning_temp", return_value="30°C")
    def test_sold_but_would_have_won_false_when_lower(self, _win, mock_event):
        mock_event.return_value = CachedResolution(
            closed=True,
            title="Highest temperature in London on July 5?",
            winning_temp=None,
            winning_token_id=None,
        )
        group = group_activity_rows([_buy_row(title="28°C"), _sell_row()])[0]
        rec = build_trade_record(
            group,
            closed_positions=[],
            open_tokens=set(),
            clob_client=None,
            fetch_price_drop=False,
        )
        assert rec.result == "sold"
        assert rec.win_temp_vs_bought == "higher"
        assert rec.sold_but_would_have_won is False

    @patch("src.analysis.history_builder.fetch_resolved_event", return_value=None)
    @patch("src.analysis.history_builder.resolve_winning_temp", return_value="28°C")
    def test_sold_regret_same_temp(self, _win, _event):
        group = group_activity_rows([_buy_row(title="28°C"), _sell_row()])[0]
        rec = build_trade_record(
            group,
            closed_positions=[],
            open_tokens=set(),
            clob_client=None,
            fetch_price_drop=False,
        )
        assert rec.sold_but_would_have_won is True


class TestSummary:
    def test_summarize_counts(self):
        records = [
            TradeRecord(
                date="2026-07-05",
                city="London",
                bought_temp="28°C",
                trade_window="13:30–15:30",
                bought_at="2026-07-05T12:00:00+00:00",
                sold_at=None,
                redeemed_at=None,
                shares=10,
                result="win",
                final_value_usd=5.0,
                winning_temp="28°C",
                win_temp_vs_bought="same",
                price_drop_below_threshold_at=None,
                sold_but_would_have_won=False,
                buy_price=0.5,
                sell_price=None,
                cost_basis_usd=5.0,
                realized_pnl_usd=5.0,
                roi_pct=100.0,
                sell_value_pct=None,
                held_hours=None,
                event_slug="highest-temperature-in-london-on-july-5-2026",
                token_id="tok1",
                condition_id="0xabc",
                transaction_hash="0xtx",
            ),
            TradeRecord(
                date="2026-07-04",
                city="Paris",
                bought_temp="29°C",
                trade_window="13:30–15:30",
                bought_at="2026-07-04T12:00:00+00:00",
                sold_at="2026-07-04T18:00:00+00:00",
                redeemed_at=None,
                shares=10,
                result="sold",
                final_value_usd=-3.0,
                winning_temp="29°C",
                win_temp_vs_bought="same",
                price_drop_below_threshold_at=None,
                sold_but_would_have_won=True,
                buy_price=0.5,
                sell_price=0.2,
                cost_basis_usd=5.0,
                realized_pnl_usd=-3.0,
                roi_pct=-60.0,
                sell_value_pct=40.0,
                held_hours=6.0,
                event_slug="highest-temperature-in-paris-on-july-4-2026",
                token_id="tok2",
                condition_id="0xdef",
                transaction_hash="0xtx2",
            ),
        ]
        summary = summarize_records(records)
        assert summary.total_count == 2
        assert summary.win_count == 1
        assert summary.sold_count == 1
        assert summary.sold_but_would_have_won_count == 1
        assert summary.win_pct == 50.0

    def test_insights(self):
        rec = TradeRecord(
            date="2026-07-05",
            city="London",
            bought_temp="28°C",
            trade_window="13:30–15:30",
            bought_at="2026-07-05T12:00:00+00:00",
            sold_at=None,
            redeemed_at=None,
            shares=10,
            result="win",
            final_value_usd=5.0,
            winning_temp="28°C",
            win_temp_vs_bought="same",
            price_drop_below_threshold_at=None,
            sold_but_would_have_won=False,
            buy_price=0.5,
            sell_price=None,
            cost_basis_usd=5.0,
            realized_pnl_usd=5.0,
            roi_pct=100.0,
            sell_value_pct=None,
            held_hours=None,
            event_slug="slug",
            token_id="tok1",
            condition_id="0xabc",
            transaction_hash=None,
        )
        insights = compute_insights([rec])
        assert "London" in insights["win_rate_by_city"]
        assert insights["win_rate_by_city"]["London"]["win_rate_pct"] == 100.0


class TestNoLocalBotFiles:
    def test_analysis_modules_do_not_import_bot_audit_paths(self):
        import src.analysis.history_builder as hb
        import src.analysis.sync_runner as sr

        sources = json.dumps(
            {
                "history_builder": open(hb.__file__).read(),
                "sync_runner": open(sr.__file__).read(),
            }
        )
        assert "bought_events" not in sources
        assert "sold_events" not in sources
        assert "markets_yes_" not in sources
        assert "selections" not in sources


class TestResolutionCache:
    def test_cached_resolution_from_gamma_event(self):
        event = {
            "closed": True,
            "title": "Highest temperature in London on July 5?",
            "markets": [
                {
                    "closed": True,
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0", "1"]',
                    "groupItemTitle": "28°C",
                    "clobTokenIds": '["tok-loser"]',
                },
                {
                    "closed": True,
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["1", "0"]',
                    "groupItemTitle": "29°C",
                    "clobTokenIds": '["tok-winner"]',
                },
            ],
        }
        cached = CachedResolution.from_gamma_event(event)
        assert cached.closed is True
        assert cached.winning_temp == "29°C"
        assert cached.winning_token_id == "tok-winner"


class TestDataClientHelpers:
    def test_is_highest_temp_slug(self):
        from src.api.data_client import is_highest_temp_slug

        assert is_highest_temp_slug("highest-temperature-in-london-on-july-5-2026")
        assert not is_highest_temp_slug("other-market")
