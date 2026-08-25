"""Tests for dual-buy (top + runner-up) counterfactual analysis."""

from src.analysis.dual_buy_analysis import compute_dual_buy_analysis, dual_buy_row
from src.analysis.models import TradeRecord
from src.analysis.runner_up import runner_up_details


def _rec(**kwargs) -> TradeRecord:
    base = dict(
        date="2026-08-10",
        city="London",
        bought_temp="24°C",
        trade_window="14:00–16:00",
        bought_at="2026-08-10T06:15:00+00:00",
        sold_at=None,
        redeemed_at=None,
        shares=10.0,
        result="loss",
        final_value_usd=-5.0,
        winning_temp="25°C",
        win_temp_vs_bought="higher",
        price_drop_below_threshold_at=None,
        sold_but_would_have_won=False,
        buy_price=0.50,
        sell_price=None,
        cost_basis_usd=5.0,
        realized_pnl_usd=-5.0,
        roi_pct=-100.0,
        sell_value_pct=None,
        held_hours=None,
        event_slug="highest-temperature-in-london-on-august-10-2026",
        token_id="tok1",
        condition_id="cond1",
        transaction_hash=None,
        runner_up_yes=0.30,
        runner_up_temp="25°C",
        yes_gap=0.20,
    )
    base.update(kwargs)
    return TradeRecord(**base)


def test_runner_up_details_returns_temp():
    markets = [
        {"id": "1", "groupItemTitle": "24°C", "outcomePrices": '["0.55","0.45"]'},
        {"id": "2", "groupItemTitle": "25°C", "outcomePrices": '["0.30","0.70"]'},
        {"id": "3", "groupItemTitle": "23°C", "outcomePrices": '["0.10","0.90"]'},
    ]
    # Patch gamma via midpoint-like fields if outcomePrices path differs — use bestBid/Ask mid
    for m, yes in zip(markets, (0.55, 0.30, 0.10)):
        m["bestBid"] = yes - 0.01
        m["bestAsk"] = yes + 0.01
        m["midpoint"] = yes
        m.pop("outcomePrices", None)

    details = runner_up_details(markets)
    assert details["top_yes"] == 0.55
    assert details["runner_up_yes"] == 0.3
    assert details["runner_up_temp"] == "25°C"
    assert details["yes_gap"] == 0.25


def test_dual_buy_runner_covers_winner():
    row = dual_buy_row(_rec())
    assert row is not None
    assert row["cover"] == "runner_up"
    # cost = 10*(0.5+0.3)=8; payout=10 → pnl=+2
    assert row["pnl_dual"] == 2.0
    assert row["pnl_bought_hold"] == -5.0
    assert row["pnl_runner_hold"] == 7.0
    assert row["pnl_delta_vs_bought_hold"] == 7.0


def test_dual_buy_neither_loses_both():
    row = dual_buy_row(_rec(winning_temp="26°C", runner_up_temp="25°C"))
    assert row is not None
    assert row["cover"] == "neither"
    assert row["pnl_dual"] == -8.0


def test_dual_buy_bought_wins_without_runner_temp():
    row = dual_buy_row(
        _rec(
            result="win",
            winning_temp="24°C",
            win_temp_vs_bought="same",
            runner_up_temp=None,
            realized_pnl_usd=5.0,
            final_value_usd=5.0,
        )
    )
    assert row is not None
    assert row["cover"] == "bought"
    # cost 8, payout 10 → +2 (same as when runner would have lost)
    assert row["pnl_dual"] == 2.0


def test_dual_buy_skips_missing_runner_on_loss():
    assert dual_buy_row(_rec(runner_up_temp=None)) is None


def test_compute_dual_buy_analysis_aggregates():
    records = [
        _rec(date="2026-08-10"),  # runner covers → +2
        _rec(
            date="2026-08-11",
            city="Paris",
            token_id="tok2",
            winning_temp="26°C",
            runner_up_temp="25°C",
        ),  # neither → -8
        _rec(
            date="2026-08-12",
            city="Berlin",
            token_id="tok3",
            result="win",
            winning_temp="24°C",
            win_temp_vs_bought="same",
            realized_pnl_usd=5.0,
        ),  # bought → +2
    ]
    out = compute_dual_buy_analysis(records)
    assert out["n"] == 3
    assert out["covered_n"] == 2
    assert out["cover_counts"]["runner_up"] == 1
    assert out["cover_counts"]["neither"] == 1
    assert out["cover_counts"]["bought"] == 1
    assert out["dual_pnl"] == -4.0  # 2 - 8 + 2
    # bought holds: -5, -5, +5 = -5; dual = -4; delta = +1
    assert out["bought_hold_pnl"] == -5.0
    assert out["pnl_delta_vs_bought_hold"] == 1.0
