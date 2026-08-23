"""Tests for trade history sync merge / incremental rebuild."""

from src.analysis.models import TradeRecord
from src.analysis.sync_runner import _merge_records


def _rec(**kwargs) -> TradeRecord:
    base = dict(
        date="2026-08-09",
        city="Taipei",
        bought_temp="27°C",
        trade_window="14:00–16:00",
        bought_at="2026-08-09T06:21:01+00:00",
        sold_at=None,
        redeemed_at=None,
        shares=15.0,
        result="loss",
        final_value_usd=-7.05,
        winning_temp="29°C",
        win_temp_vs_bought="higher",
        price_drop_below_threshold_at=None,
        sold_but_would_have_won=False,
        buy_price=0.47,
        sell_price=None,
        cost_basis_usd=7.05,
        realized_pnl_usd=None,
        roi_pct=-100.0,
        sell_value_pct=None,
        held_hours=None,
        event_slug="highest-temperature-in-taipei-on-august-9-2026",
        token_id="tok-taipei",
        condition_id="0xabc",
        transaction_hash="0xtx1",
        bought_at_hk="",
        bought_at_local="14:21",
        sold_at_hk="",
        price_drop_below_threshold_at_hk="",
    )
    base.update(kwargs)
    return TradeRecord(**base)


def test_merge_does_not_shrink_shares_on_partial_incremental():
    """Incremental slice with only the 0.6 fill must not overwrite 15-share row."""
    existing = {"tok-taipei": _rec(shares=15.0, cost_basis_usd=7.05, final_value_usd=-7.05)}
    fresh = [
        _rec(
            shares=0.6,
            cost_basis_usd=0.282,
            final_value_usd=-0.282,
            bought_at="2026-08-09T06:21:01+00:00",
        )
    ]
    merged = _merge_records(existing, fresh)
    assert len(merged) == 1
    assert merged[0].shares == 15.0
    assert merged[0].cost_basis_usd == 7.05


def test_merge_takes_fresh_when_shares_grow():
    existing = {"tok-taipei": _rec(shares=0.6, cost_basis_usd=0.282)}
    fresh = [_rec(shares=15.0, cost_basis_usd=7.05)]
    merged = _merge_records(existing, fresh)
    assert merged[0].shares == 15.0
    assert merged[0].cost_basis_usd == 7.05
