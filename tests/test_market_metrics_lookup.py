"""Tests for competitive / open interest lookup."""

from __future__ import annotations

import json
from pathlib import Path

from src.analysis.market_metrics_lookup import (
    load_competitive_snapshot_by_token,
    load_event_metrics_index,
    load_open_interest_snapshot_by_slug,
    lookup_competitive_for_buy,
    lookup_metrics_from_events,
    lookup_open_interest_for_buy,
)
from src.analysis.strategy_insights import compute_insights
from tests.test_trade_history import _sample_record


def test_competitive_band_labels():
    from src.analysis import strategy_insights as si

    assert si._competitive_band(0.99) == "0.98–1.00"
    assert si._competitive_band(0.975) == "0.96–0.98"
    assert si._competitive_band(0.805) == "0.80–0.82"
    assert si._competitive_band(0.79) == "<0.80"
    assert si._competitive_band(None) == "unknown"


def test_open_interest_band_labels():
    from src.analysis import strategy_insights as si

    assert si._open_interest_band(11247.68) == "10000–12000"
    assert si._open_interest_band(4500) == "4000–6000"
    assert si._open_interest_band(32000) == "≥30000"
    assert si._open_interest_band(None) == "unknown"


def test_lookup_competitive_from_snapshots(tmp_path: Path):
    sel_dir = tmp_path / "snapshots"
    sel_dir.mkdir()
    (sel_dir / "markets_yes_2026-07-20_1200.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T12:00:00+00:00",
                "selections": [
                    {
                        "yes_token_id": "tokA",
                        "event_slug": "event-a",
                        "competitive": 0.965,
                        "open_interest": 11000,
                    }
                ],
            }
        )
    )
    index = load_competitive_snapshot_by_token(sel_dir)
    assert lookup_competitive_for_buy(
        "tokA",
        "2026-07-20T12:05:00+00:00",
        snapshot_index=index,
    ) == 0.965


def test_lookup_metrics_from_events(tmp_path: Path):
    events = [
        {
            "slug": "highest-temperature-in-london-on-july-20-2026",
            "updatedAt": "2026-07-20T12:00:00Z",
            "openInterest": 11247.68,
            "markets": [
                {
                    "competitive": 0.894,
                    "clobTokenIds": '["tokA", "tokB"]',
                }
            ],
        }
    ]
    (tmp_path / "events_2026-07-20.json").write_text(json.dumps(events))
    index = load_event_metrics_index(tmp_path)
    competitive, open_interest = lookup_metrics_from_events(
        event_slug="highest-temperature-in-london-on-july-20-2026",
        token_id="tokA",
        bought_at="2026-07-20T12:30:00+00:00",
        index=index,
    )
    assert competitive == 0.894
    assert open_interest == 11247.68


def test_lookup_open_interest_from_snapshots(tmp_path: Path):
    sel_dir = tmp_path / "snapshots"
    sel_dir.mkdir()
    (sel_dir / "markets_yes_2026-07-20_1200.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T12:00:00+00:00",
                "selections": [
                    {
                        "yes_token_id": "tokA",
                        "event_slug": "event-a",
                        "open_interest": 7553.14,
                    }
                ],
            }
        )
    )
    index = load_open_interest_snapshot_by_slug(sel_dir)
    assert lookup_open_interest_for_buy(
        "event-a",
        "2026-07-20T12:05:00+00:00",
        snapshot_index=index,
    ) == 7553.14


def test_insights_include_competitive_and_open_interest_bands():
    rec = _sample_record(competitive=0.975, open_interest=11247.68)
    insights = compute_insights([rec])
    assert "0.96–0.98" in insights["summary_by_competitive_band"]
    assert "10000–12000" in insights["summary_by_open_interest_band"]
