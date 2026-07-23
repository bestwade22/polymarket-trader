"""Tests for skipping lowest win-summary cities before orders."""

from __future__ import annotations

from src.analysis.models import TradeRecord
from src.trade.city_skip import (
    filter_events_by_skip_cities,
    lowest_win_summary_cities,
)


def _rec(city: str, *, result: str = "win", shares: float = 10, **extra) -> TradeRecord:
    base = dict(
        date="2026-07-05",
        city=city,
        bought_temp="28°C",
        bought_at_hk="2026-07-05 20:00:00 HKT",
        bought_at_local="13:00",
        trade_window="14:00–16:00",
        bought_at="2026-07-05T12:00:00+00:00",
        sold_at=None,
        redeemed_at=None,
        shares=shares,
        result=result,
        final_value_usd=5.0 if result == "win" else -5.0,
        winning_temp="28°C" if result == "win" else "30°C",
        win_temp_vs_bought="same" if result == "win" else "higher",
        price_drop_below_threshold_at=None,
        sold_but_would_have_won=False,
        buy_price=0.5,
        sell_price=None,
        cost_basis_usd=5.0,
        realized_pnl_usd=5.0 if result == "win" else -5.0,
        roi_pct=100.0 if result == "win" else -100.0,
        sell_value_pct=None,
        held_hours=None,
        event_slug=f"highest-temperature-in-{city.lower().replace(' ', '-')}-on-july-5-2026",
        token_id=f"tok-{city}",
        condition_id="0xabc",
        transaction_hash="0xtx",
    )
    base.update(extra)
    return TradeRecord(**base)


def test_lowest_win_summary_cities_picks_worst():
    records = [
        _rec("Alpha", result="win"),
        _rec("Alpha", result="win", token_id="tok-a2"),
        _rec("Beta", result="loss"),
        _rec("Beta", result="loss", token_id="tok-b2"),
        _rec("Gamma", result="win"),
        _rec("Gamma", result="loss", token_id="tok-g2"),
        _rec("Delta", result="loss"),
        _rec("Dust", result="win", shares=0.2),  # ignored in win summary
    ]
    bottom = lowest_win_summary_cities(records, bottom_n=2)
    # Both at 0%; lower denom sorts first (Delta=1, Beta=2).
    assert bottom == ["Delta", "Beta"]


def test_filter_events_by_skip_cities():
    events = [
        {"id": "1", "city": "London"},
        {"id": "2", "city": "Paris"},
        {"id": "3", "city": "Berlin"},
    ]
    kept, skipped = filter_events_by_skip_cities(events, ["Paris", "Berlin"])
    assert [e["city"] for e in kept] == ["London"]
    assert {s["city"] for s in skipped} == {"Paris", "Berlin"}
    assert all(s["reason"] == "low_win_summary_city" for s in skipped)
