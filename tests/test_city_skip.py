"""Tests for skipping lowest win-summary city timezones before orders."""

from __future__ import annotations

import json

from src.analysis.models import TradeRecord
from src.analysis.strategy_insights import timezone_group
from src.trade.city_skip import (
    filter_events_by_skip_timezones,
    lowest_win_summary_timezones,
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


def test_lowest_win_summary_timezones_picks_worst(monkeypatch):
    # Force known timezone labels so the test does not depend on city_timezones.json.
    mapping = {
        "Alpha": "Good Zone",
        "Beta": "Bad Zone A",
        "Gamma": "Mid Zone",
        "Delta": "Bad Zone B",
        "Dust": "Good Zone",
    }
    monkeypatch.setattr(
        "src.trade.city_skip.timezone_group",
        lambda city: mapping.get(city, "Unknown"),
    )
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
    bottom = lowest_win_summary_timezones(records, bottom_n=2)
    # Both at 0%; lower denom sorts first (Bad Zone B=1, Bad Zone A=2).
    assert bottom == ["Bad Zone B", "Bad Zone A"]


def test_filter_events_by_skip_timezones(monkeypatch):
    mapping = {
        "London": "UK (UTC+0/+1)",
        "Paris": "Central EU (UTC+1/+2)",
        "Berlin": "Central EU (UTC+1/+2)",
    }
    monkeypatch.setattr(
        "src.trade.city_skip.timezone_group",
        lambda city: mapping.get(city, "Unknown"),
    )
    events = [
        {"id": "1", "city": "London"},
        {"id": "2", "city": "Paris"},
        {"id": "3", "city": "Berlin"},
    ]
    kept, skipped = filter_events_by_skip_timezones(
        events, ["Central EU (UTC+1/+2)"]
    )
    assert [e["city"] for e in kept] == ["London"]
    assert {s["city"] for s in skipped} == {"Paris", "Berlin"}
    assert all(s["reason"] == "low_win_summary_timezone" for s in skipped)
    assert all(s["timezone"] == "Central EU (UTC+1/+2)" for s in skipped)


def test_surviving_records_respect_live_stack(monkeypatch):
    from src.trade.city_skip import surviving_records_for_skip

    monkeypatch.setattr("src.trade.city_skip.settings.yes_price_min", 0.45)
    monkeypatch.setattr("src.trade.city_skip.settings.yes_price_max", 0.60)
    monkeypatch.setattr("src.trade.city_skip.settings.spread_max", 0.05)
    records = [
        _rec("Alpha", buy_price=0.50, spread=0.02, token_id="a"),
        _rec("Beta", buy_price=0.40, spread=0.02, token_id="b"),  # below min
        _rec("Gamma", buy_price=0.50, spread=0.12, token_id="c"),  # wide spread
        _rec("Delta", buy_price=0.52, spread=None, token_id="d"),  # missing spread ok
    ]
    kept = surviving_records_for_skip(records)
    assert {r.city for r in kept} == {"Alpha", "Delta"}


def test_insights_include_surviving_timezone_summary(monkeypatch):
    from src.analysis.strategy_insights import compute_insights

    monkeypatch.setattr("src.trade.city_skip.settings.yes_price_min", 0.45)
    monkeypatch.setattr("src.trade.city_skip.settings.yes_price_max", 0.60)
    monkeypatch.setattr("src.trade.city_skip.settings.spread_max", 0.05)
    monkeypatch.setattr(
        "src.analysis.strategy_insights.timezone_group",
        lambda city: {"Alpha": "Good", "Beta": "Bad"}.get(city, "Unknown"),
    )
    records = [
        _rec("Alpha", buy_price=0.50, spread=0.02, result="win", token_id="a1"),
        _rec("Alpha", buy_price=0.50, spread=0.02, result="win", token_id="a2"),
        _rec("Beta", buy_price=0.50, spread=0.02, result="loss", token_id="b1"),
        _rec("Beta", buy_price=0.40, spread=0.02, result="loss", token_id="b2"),  # filtered out
    ]
    insights = compute_insights(records)
    surviving = insights["summary_by_city_timezone_surviving"]
    assert "Good" in surviving
    assert "Bad" in surviving
    assert surviving["Good"]["count"] == 2
    assert surviving["Bad"]["count"] == 1
    assert surviving["Bad"]["win_plus_sold_win_pct"] == 0.0


def test_refresh_timezone_skip_denylist_writes_daily_file(tmp_path, monkeypatch):
    from src.trade import city_skip as cs

    monkeypatch.setattr(
        cs,
        "timezone_group",
        lambda city: {"Alpha": "Good", "Beta": "Bad"}.get(city, "Unknown"),
    )
    monkeypatch.setattr(cs.settings, "yes_price_min", 0.0)
    monkeypatch.setattr(cs.settings, "spread_max", 0.15)
    monkeypatch.setattr(cs.settings, "city_skip_bottom_n", 1)
    history = tmp_path / "trade_history.json"
    denylist = tmp_path / "denylist.json"
    history.write_text(
        json.dumps(
            {
                "records": [
                    _rec("Alpha", result="win", token_id="a1").to_dict(),
                    _rec("Alpha", result="win", token_id="a2").to_dict(),
                    _rec("Beta", result="loss", token_id="b1").to_dict(),
                    _rec("Beta", result="loss", token_id="b2").to_dict(),
                ]
            }
        )
    )
    payload = cs.refresh_timezone_skip_denylist(
        history_path=history, denylist_path=denylist, force=True
    )
    assert denylist.exists()
    assert payload["timezones"] == ["Bad"]
    assert payload["date"]
    # Second call without force reuses same-day file
    again = cs.refresh_timezone_skip_denylist(
        history_path=history, denylist_path=denylist, force=False
    )
    assert again["timezones"] == ["Bad"]


def test_timezone_group_uses_shared_labels():
    # Sanity: public helper exists and returns a string for any city.
    assert isinstance(timezone_group("London"), str)
