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
        bought_at_local="15:00",
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
        "Sparse": "Sparse Zone",
        "Dust": "Good Zone",
    }
    monkeypatch.setattr(
        "src.trade.city_skip.timezone_group",
        lambda city: mapping.get(city, "Unknown"),
    )
    monkeypatch.setattr("src.trade.city_skip.settings.city_skip_min_count", 3)

    def losses(city: str, n: int) -> list[TradeRecord]:
        return [
            _rec(city, result="loss", token_id=f"tok-{city}-{i}") for i in range(n)
        ]

    def wins(city: str, n: int) -> list[TradeRecord]:
        return [
            _rec(city, result="win", token_id=f"tok-{city}-{i}") for i in range(n)
        ]

    records = [
        *wins("Alpha", 4),  # Good Zone denom 4, 100% — ineligible? denom 4 > min_count 3
        *losses("Beta", 4),  # Bad Zone A denom 4, 0%
        *wins("Gamma", 2),
        *losses("Gamma", 2),  # Mid Zone denom 4, 50%
        *losses("Delta", 5),  # Bad Zone B denom 5, 0%
        *losses("Sparse", 2),  # Sparse Zone denom 2 ≤ min_count — excluded
        _rec("Dust", result="win", shares=0.2),  # ignored in win summary
    ]
    # min_count = min(denoms >= 3) = 4 → only denom > 4 eligible → Bad Zone B only for bottom 1
    bottom = lowest_win_summary_timezones(records, bottom_n=2)
    assert "Sparse Zone" not in bottom
    # Both Bad Zone A (4) and Mid/Good have denom 4 == min_count → excluded; only B (5) eligible
    assert bottom == ["Bad Zone B"]


def test_lowest_win_summary_excludes_at_or_below_min_count(monkeypatch):
    from src.trade.city_skip import resolve_city_skip_min_count

    mapping = {
        "MY": "Malaysia",
        "PH": "Philippines",
        "JP": "Japan",
        "CN": "China",
        "AR": "Argentina",
        "ZA": "South Africa",
        "UK": "UK",
    }
    monkeypatch.setattr(
        "src.trade.city_skip.timezone_group",
        lambda city: mapping.get(city, "Unknown"),
    )
    monkeypatch.setattr("src.trade.city_skip.settings.city_skip_min_count", 3)

    def many(city: str, n: int, *, result: str) -> list[TradeRecord]:
        return [
            _rec(city, result=result, token_id=f"tok-{city}-{i}") for i in range(n)
        ]

    records = [
        *many("MY", 3, result="win"),  # denom 3 = min_count → excluded
        *many("PH", 2, result="loss"),  # 2 → excluded
        *many("JP", 1, result="loss"),  # 1 → excluded
        *many("CN", 4, result="loss"),  # 4 > 3, 0%
        *many("AR", 4, result="loss"),  # 4 > 3, 0%
        *many("ZA", 4, result="loss"),  # 4 > 3, 0%
        *many("UK", 4, result="win"),  # 4 > 3, 100% — not bottom
    ]
    assert resolve_city_skip_min_count([3, 2, 1, 4, 4, 4, 4], floor=3) == 3
    bottom = lowest_win_summary_timezones(records, bottom_n=3)
    assert bottom == ["Argentina", "China", "South Africa"]
    assert "Malaysia" not in bottom
    assert "Philippines" not in bottom
    assert "Japan" not in bottom
    assert "UK" not in bottom


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
        _rec("Alpha", buy_price=0.50, spread=0.02, token_id="a", bought_at_local="15:00"),
        _rec("Beta", buy_price=0.40, spread=0.02, token_id="b"),  # below min
        _rec("Gamma", buy_price=0.50, spread=0.12, token_id="c"),  # wide spread
        _rec("Delta", buy_price=0.52, spread=None, token_id="d"),  # missing spread ok
        _rec(
            "Early",
            buy_price=0.47,
            spread=0.02,
            token_id="e",
            bought_at_local="14:15",
        ),  # low band too early
        _rec(
            "HighNarrow",
            buy_price=0.55,
            spread=0.02,
            token_id="f",
            yes_gap=0.10,
        ),  # outside high band with patched max 0.60
    ]
    kept = surviving_records_for_skip(records)
    assert {r.city for r in kept} == {"Alpha", "Delta", "HighNarrow"}


def test_surviving_records_buy_band_high_gap(monkeypatch):
    from src.trade.city_skip import surviving_records_for_skip

    monkeypatch.setattr("src.trade.city_skip.settings.yes_price_min", 0.45)
    monkeypatch.setattr("src.trade.city_skip.settings.yes_price_max", 0.70)
    monkeypatch.setattr("src.trade.city_skip.settings.spread_max", 0.08)
    monkeypatch.setattr("src.trade.city_skip.settings.yes_gap_min", 0.05)
    monkeypatch.setattr("src.trade.city_skip.settings.buy_band_high_yes_gap_min", 0.25)
    records = [
        _rec("Wide", buy_price=0.65, spread=0.02, yes_gap=0.30, token_id="w"),
        _rec("Narrow", buy_price=0.65, spread=0.02, yes_gap=0.20, token_id="n"),
    ]
    kept = surviving_records_for_skip(records)
    assert {r.city for r in kept} == {"Wide"}


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
    monkeypatch.setattr(cs.settings, "city_skip_min_count", 3)
    history = tmp_path / "trade_history.json"
    denylist = tmp_path / "denylist.json"
    history.write_text(
        json.dumps(
            {
                "records": [
                    *[_rec("Alpha", result="win", token_id=f"a{i}").to_dict() for i in range(4)],
                    *[_rec("Beta", result="loss", token_id=f"b{i}").to_dict() for i in range(4)],
                ]
            }
        )
    )
    payload = cs.refresh_timezone_skip_denylist(
        history_path=history, denylist_path=denylist, force=True
    )
    assert denylist.exists()
    # min_count = 4 (both zones denom 4); denom > 4 required → empty eligible
    # Wait: both have denom 4, min_count=4, eligible need >4 → empty
    # Need Bad with more trades than Good's min tier.
    assert payload["min_count"] == 4
    assert payload["min_count_floor"] == 3
    assert payload["timezones"] == []
    assert "yes_gap_min" in payload
    assert "buy_band_high_yes_gap_min" in payload
    assert "buy_band_low_min_local_hour" in payload
    # Rebuild with Bad having denom 5 so it clears min_count
    history.write_text(
        json.dumps(
            {
                "records": [
                    *[_rec("Alpha", result="win", token_id=f"a{i}").to_dict() for i in range(4)],
                    *[_rec("Beta", result="loss", token_id=f"b{i}").to_dict() for i in range(5)],
                ]
            }
        )
    )
    payload = cs.refresh_timezone_skip_denylist(
        history_path=history, denylist_path=denylist, force=True
    )
    assert payload["min_count"] == 4
    assert payload["timezones"] == ["Bad"]
    # Second call without force reuses same-day file
    again = cs.refresh_timezone_skip_denylist(
        history_path=history, denylist_path=denylist, force=False
    )
    assert again["timezones"] == ["Bad"]


def test_timezone_group_uses_shared_labels():
    # Sanity: public helper exists and returns a string for any city.
    assert isinstance(timezone_group("London"), str)
