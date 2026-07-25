"""Tests for selection enrichment, filter sweep, skipped analysis, runner-up."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.filter_sweep import compute_filter_sweep
from src.analysis.models import TradeRecord
from src.analysis.runner_up import runner_up_yes_and_gap
from src.analysis.selection_enrichment import (
    backfill_records_from_selections,
    load_selection_enrichment_by_token,
    lookup_enrichment_for_buy,
)
from src.analysis.skipped_analysis import compute_skipped_analysis


def _rec(**kwargs) -> TradeRecord:
    base = dict(
        date="2026-07-01",
        city="London",
        bought_temp="28°C",
        trade_window="14:00–16:00",
        bought_at="2026-07-01T13:15:00+00:00",
        sold_at=None,
        redeemed_at=None,
        shares=10.0,
        result="win",
        final_value_usd=4.0,
        winning_temp="28°C",
        win_temp_vs_bought="same",
        price_drop_below_threshold_at=None,
        sold_but_would_have_won=False,
        buy_price=0.50,
        sell_price=None,
        cost_basis_usd=5.0,
        realized_pnl_usd=4.0,
        roi_pct=80.0,
        sell_value_pct=None,
        held_hours=None,
        event_slug="highest-temperature-in-london-on-july-1-2026",
        token_id="tok1",
        condition_id="cond1",
        transaction_hash=None,
    )
    base.update(kwargs)
    return TradeRecord(**base)


def test_runner_up_yes_and_gap():
    markets = [
        {"id": "1", "outcomePrices": ["0.55", "0.45"], "outcomes": ["Yes", "No"]},
        {"id": "2", "outcomePrices": ["0.30", "0.70"], "outcomes": ["Yes", "No"]},
        {"id": "3", "outcomePrices": ["0.10", "0.90"], "outcomes": ["Yes", "No"]},
    ]
    top, runner, gap = runner_up_yes_and_gap(markets)
    assert top == 0.55
    assert runner == 0.30
    assert gap == 0.25


def test_lookup_enrichment_from_selections(tmp_path: Path):
    payload = {
        "run_at": "2026-07-16T13:00:00+00:00",
        "selections": [
            {
                "yes_token_id": "tokA",
                "best_bid": 0.48,
                "best_ask": 0.52,
                "spread": 0.04,
                "midpoint": 0.50,
                "gamma_yes_price": 0.51,
                "clob_buy_price": 0.52,
                "on_edge": False,
                "competitive": 0.99,
                "open_interest": 12000,
                "runner_up_yes": 0.22,
                "yes_gap": 0.28,
            }
        ],
    }
    (tmp_path / "markets_yes_2026-07-16_1300.json").write_text(json.dumps(payload))
    index = load_selection_enrichment_by_token(tmp_path)
    row = lookup_enrichment_for_buy("tokA", "2026-07-16T13:05:00+00:00", index=index)
    assert row is not None
    assert row["spread"] == 0.04
    assert row["midpoint"] == 0.50
    assert row["yes_gap"] == 0.28


def test_backfill_records_from_selections(tmp_path: Path):
    payload = {
        "run_at": "2026-07-16T13:00:00+00:00",
        "selections": [
            {
                "yes_token_id": "tok1",
                "best_bid": 0.40,
                "best_ask": 0.45,
                "midpoint": 0.425,
                "gamma_yes_price": 0.42,
                "clob_buy_price": 0.45,
                "on_edge": False,
                "competitive": 0.98,
                "open_interest": 15000,
                "runner_up_yes": 0.20,
                "yes_gap": 0.22,
            }
        ],
    }
    (tmp_path / "markets_yes_2026-07-16_1300.json").write_text(json.dumps(payload))
    rec = _rec(spread=None, on_edge=None, competitive=None, open_interest=None)
    counts = backfill_records_from_selections(
        [rec], selections_dir=tmp_path, data_dir=tmp_path, use_event_fallback=False
    )
    assert rec.spread == 0.05
    assert rec.on_edge is False
    assert rec.yes_gap == 0.22
    assert counts["spread"] == 1


def test_filter_sweep_recommended_shape():
    records = []
    for i in range(20):
        records.append(
            _rec(
                date=f"2026-07-{(i % 10) + 1:02d}",
                token_id=f"t{i}",
                buy_price=0.50,
                spread=0.04,
                on_edge=False,
                result="win" if i % 2 == 0 else "loss",
                win_temp_vs_bought="same" if i % 2 == 0 else "higher",
                realized_pnl_usd=2.0 if i % 2 == 0 else -3.0,
                final_value_usd=2.0 if i % 2 == 0 else -3.0,
            )
        )
    sweep = compute_filter_sweep(records, min_oos_denom=1)
    assert "stacks" in sweep
    assert isinstance(sweep["stacks"], list)
    assert sweep["stacks"]


def test_skipped_analysis_by_reason(tmp_path: Path):
    (tmp_path / "markets_yes_2026-07-20_1400.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T14:00:00+00:00",
                "strategy": "highest_yes",
                "selections": [],
                "skipped_bought": [
                    {
                        "city": "Cape Town",
                        "reason": "low_win_summary_timezone",
                        "timezone": "South Africa (UTC+2)",
                    },
                    {
                        "city": "London",
                        "reason": "spread_max",
                        "group_item_title": "28°C",
                        "event_slug": "highest-temperature-in-london-on-july-20-2026",
                    },
                ],
            }
        )
    )
    # Resolution: London 28°C won → would have won
    analysis = compute_skipped_analysis(
        selections_dir=tmp_path,
        resolutions={"highest-temperature-in-london-on-july-20-2026": "28°C"},
    )
    assert analysis["total_skips"] == 2
    by = {r["reason"]: r for r in analysis["by_reason"]}
    assert by["spread_max"]["would_have_won"] == 1
    assert by["low_win_summary_timezone"]["count"] == 1
