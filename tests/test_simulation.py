"""Unit tests for trade strategy simulator (no network)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from src.simulation.buy_pass import SimulatedBuy, try_buy_event
from src.simulation.event_loader import date_range, default_sim_date_range
from src.simulation.price_at_time import PriceHistoryStore
from src.simulation.resolve import _pnl, build_sim_record
from src.simulation.sample_times import sample_local_minutes_in_window, sample_times_utc_for_event
from src.simulation.sell_pass import SimulatedSell, try_sell_win
from src.simulation.snapshot_enrichment import SnapshotEnrichment, lookup_enrichment_near
from src.trade.strategies.base import MarketSelection


def test_sample_local_minutes_default_window():
    pairs = sample_local_minutes_in_window(
        start_hour=14, start_minute=0, end_hour=16, end_minute=0
    )
    assert pairs == [(14, 5), (14, 35), (15, 5), (15, 35)]


def test_sample_times_utc_for_event_new_york():
    event = {
        "event_date": "2026-07-15",
        "timezone": "America/New_York",
    }
    samples = sample_times_utc_for_event(event)
    assert len(samples) == 4
    locals_ = [
        s.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") for s in samples
    ]
    assert locals_ == ["14:05", "14:35", "15:05", "15:35"]


def test_default_sim_date_range():
    start, end = default_sim_date_range(today=date(2026, 7, 21))
    assert end == date(2026, 7, 20)
    assert start == date(2026, 7, 14)
    assert date_range(start, end)[0] == start
    assert date_range(start, end)[-1] == end


def _market(mid: float, market_id: str, title: str, token: str, *, bid=None, ask=None, gamma=None):
    m = {
        "id": market_id,
        "groupItemTitle": title,
        "clobTokenIds": json.dumps([token, "no"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "midpoint": mid,
        "clobBuyPrice": mid,
        "orderMinSize": 5,
        "negRisk": False,
        "orderPriceMinTickSize": 0.01,
    }
    if bid is not None:
        m["bestBid"] = bid
    if ask is not None:
        m["bestAsk"] = ask
    yes = gamma if gamma is not None else mid
    m["outcomePrices"] = json.dumps([str(yes), str(round(1 - yes, 4))])
    return m


def test_buy_pass_selects_highest_yes(monkeypatch):
    event = {
        "id": "e1",
        "city": "Munich",
        "slug": "highest-temperature-in-munich-on-july-15-2026",
        "title": "Highest temperature in Munich on July 15?",
        "event_date": "2026-07-15",
        "timezone": "Europe/Berlin",
        "markets": [
            _market(0.30, "m1", "22°C", "t1"),
            _market(0.45, "m2", "24°C", "t2"),
            _market(0.20, "m3", "26°C", "t3"),
        ],
    }

    class FakeStore:
        def mark_bought(self, token_id):
            self.bought = token_id

    from src.simulation import buy_pass as bp

    monkeypatch.setattr(bp, "build_event_at_time", lambda e, at, store, **k: (e, True))
    monkeypatch.setattr(
        bp,
        "sample_times_utc_for_event",
        lambda e: [datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc)],
    )

    store = FakeStore()
    buy = try_buy_event(event, store, strategy_name="highest_yes", yes_price_max=0.60)
    assert buy is not None
    assert buy.selection.group_item_title == "24°C"
    assert buy.buy_price == pytest.approx(0.45)
    assert store.bought == "t2"


def test_buy_pass_skips_yes_price_max(monkeypatch):
    event = {
        "id": "e1",
        "city": "Munich",
        "slug": "highest-temperature-in-munich-on-july-15-2026",
        "event_date": "2026-07-15",
        "timezone": "Europe/Berlin",
        "markets": [
            _market(0.70, "m2", "24°C", "t2"),
        ],
    }
    from src.simulation import buy_pass as bp

    monkeypatch.setattr(bp, "build_event_at_time", lambda e, at, store, **k: (e, True))
    monkeypatch.setattr(
        bp,
        "sample_times_utc_for_event",
        lambda e: [datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc)],
    )
    buy = try_buy_event(event, MagicMock(), strategy_name="highest_yes", yes_price_max=0.60)
    assert buy is None


def test_buy_pass_skips_wide_spread_from_snapshot(monkeypatch):
    event = {
        "id": "e1",
        "city": "Munich",
        "slug": "highest-temperature-in-munich-on-july-15-2026",
        "event_date": "2026-07-15",
        "timezone": "Europe/Berlin",
        "markets": [
            _market(0.40, "m2", "24°C", "t2", bid=0.20, ask=0.40),  # spread 0.20
        ],
    }
    from src.simulation import buy_pass as bp

    monkeypatch.setattr(bp, "build_event_at_time", lambda e, at, store, **k: (e, True))
    monkeypatch.setattr(
        bp,
        "sample_times_utc_for_event",
        lambda e: [datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc)],
    )
    buy = try_buy_event(
        event, MagicMock(), strategy_name="highest_yes", yes_price_max=0.60, spread_max=0.15
    )
    assert buy is None


def test_buy_pass_allows_missing_spread(monkeypatch):
    event = {
        "id": "e1",
        "city": "Munich",
        "slug": "highest-temperature-in-munich-on-july-15-2026",
        "event_date": "2026-07-15",
        "timezone": "Europe/Berlin",
        "markets": [
            _market(0.40, "m2", "24°C", "t2"),  # no bid/ask
        ],
    }
    from src.simulation import buy_pass as bp

    monkeypatch.setattr(bp, "build_event_at_time", lambda e, at, store, **k: (e, True))
    monkeypatch.setattr(
        bp,
        "sample_times_utc_for_event",
        lambda e: [datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc)],
    )
    store = MagicMock()
    buy = try_buy_event(
        event, store, strategy_name="highest_yes", yes_price_max=0.60, spread_max=0.15
    )
    assert buy is not None
    assert buy.spread is None


def test_sell_win_fires_when_price_hits_floor():
    event = {
        "city": "Munich",
        "slug": "highest-temperature-in-munich-on-july-15-2026",
        "title": "Highest temperature in Munich on July 15?",
        "event_date": "2026-07-15",
        "timezone": "Europe/Berlin",
    }
    sold_local = datetime(2026, 7, 15, 15, 10, tzinfo=ZoneInfo("Europe/Berlin"))
    sold_ts = int(sold_local.astimezone(timezone.utc).timestamp())
    bought_at = datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc)

    class Store:
        def get_history(self, token_id, **kwargs):
            return [{"t": sold_ts, "p": 0.92}]

    sell = try_sell_win(
        event,
        "tok",
        Store(),
        bought_at=bought_at,
        history_start_ts=sold_ts - 3600,
        history_end_ts=sold_ts + 3600,
    )
    assert sell is not None
    assert sell.tier_name == "tier1"
    assert sell.sell_price == pytest.approx(0.92)


def test_sell_win_holds_when_price_never_hits():
    event = {
        "city": "Munich",
        "slug": "highest-temperature-in-munich-on-july-15-2026",
        "title": "Highest temperature in Munich on July 15?",
        "event_date": "2026-07-15",
        "timezone": "Europe/Berlin",
    }
    sold_local = datetime(2026, 7, 15, 15, 10, tzinfo=ZoneInfo("Europe/Berlin"))
    sold_ts = int(sold_local.astimezone(timezone.utc).timestamp())
    bought_at = datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc)

    class Store:
        def get_history(self, token_id, **kwargs):
            return [{"t": sold_ts, "p": 0.50}]

    sell = try_sell_win(
        event,
        "tok",
        Store(),
        bought_at=bought_at,
        history_start_ts=sold_ts - 3600,
        history_end_ts=sold_ts + 3600,
    )
    assert sell is None


def test_pnl_formulas():
    assert _pnl("win", 10, 0.40, None) == pytest.approx(6.0)
    assert _pnl("loss", 10, 0.40, None) == pytest.approx(-4.0)
    assert _pnl("sold", 10, 0.40, 0.91) == pytest.approx(5.1)


def test_build_sim_record_sold(monkeypatch):
    monkeypatch.setattr(
        "src.simulation.resolve.resolve_winning_temp",
        lambda slug: "24°C",
    )
    event = {
        "event_date": "2026-07-15",
        "city": "Munich",
        "timezone": "Europe/Berlin",
        "slug": "highest-temperature-in-munich-on-july-15-2026",
    }
    sel = MarketSelection(
        event_id="e1",
        city="Munich",
        market_id="m2",
        group_item_title="24°C",
        yes_price=0.45,
        yes_token_id="t2",
        buy_price=0.45,
        share_count=10,
        neg_risk=False,
        tick_size="0.01",
        order_min_size=5,
        strategy="highest_yes",
        event=event,
        market={"conditionId": "c1"},
    )
    buy = SimulatedBuy(
        event=event,
        selection=sel,
        bought_at=datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc),
        sample_time_local="14:05",
        buy_price=0.45,
        gamma_proxy=True,
        spread=None,
        strategy_name="highest_yes",
    )
    sell = SimulatedSell(
        sold_at=datetime(2026, 7, 15, 13, 10, tzinfo=timezone.utc),
        sell_price=0.91,
        tier_name="tier1",
    )
    row = build_sim_record(buy, sell, share_count=10)
    assert row["result"] == "sold"
    assert row["gamma_proxy"] is True
    assert row["sim_strategy"] == "highest_yes"
    assert row["realized_pnl_usd"] == pytest.approx(4.6)


def test_price_store_persists_only_bought(tmp_path):
    clob = MagicMock()
    clob.get_prices_history.return_value = [{"t": 1, "p": 0.4}]
    store = PriceHistoryStore(clob=clob, cache_dir=tmp_path)
    hist = store.get_history("tokA", start_ts=0, end_ts=100)
    assert hist == [{"t": 1, "p": 0.4}]
    assert not list(tmp_path.glob("*.json"))
    store.mark_bought("tokA")
    assert (tmp_path / "tokA.json").exists()
    store.get_history("tokB", start_ts=0, end_ts=100)
    assert not (tmp_path / "tokB.json").exists()


def test_lookup_enrichment_near():
    at = datetime(2026, 7, 15, 12, 5, tzinfo=timezone.utc)
    near = SnapshotEnrichment(
        run_at=at,
        spread=0.05,
        best_bid=0.38,
        best_ask=0.43,
        gamma_yes_price=0.41,
        outcome_prices=None,
    )
    far = SnapshotEnrichment(
        run_at=at.replace(hour=20),
        spread=0.20,
        best_bid=0.1,
        best_ask=0.3,
        gamma_yes_price=0.2,
        outcome_prices=None,
    )
    idx = {"tok": [far, near]}
    hit = lookup_enrichment_near("tok", at, index=idx)
    assert hit is not None
    assert hit.spread == pytest.approx(0.05)
