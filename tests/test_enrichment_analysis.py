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
                        "selection_price": 0.55,
                    },
                    {
                        "city": "Paris",
                        "reason": "yes_price_max",
                        "group_item_title": "31°C",
                        "event_slug": "highest-temperature-in-paris-on-july-20-2026",
                        "selection_price": 0.72,
                        "yes_price_max": 0.6,
                    },
                    {
                        "city": "Berlin",
                        "reason": "yes_price_max",
                        "group_item_title": "30°C",
                        "event_slug": "highest-temperature-in-berlin-on-july-20-2026",
                        "selection_price": 0.65,
                        "yes_price_max": 0.6,
                    },
                ],
            }
        )
    )
    # London 28°C won → would have won; Paris 31°C lost; Berlin unresolved
    analysis = compute_skipped_analysis(
        selections_dir=tmp_path,
        resolutions={
            "highest-temperature-in-london-on-july-20-2026": "28°C",
            "highest-temperature-in-paris-on-july-20-2026": "30°C",
        },
        fetch_missing_resolutions=False,
    )
    assert analysis["total_skips"] == 4
    by = {r["reason"]: r for r in analysis["by_reason"]}
    assert by["spread_max"]["would_have_won"] == 1
    assert by["spread_max"]["avg_price"] == 0.55
    assert by["spread_max"]["total_pnl_if_bought"] == 4.5  # 10 * (1 - 0.55)
    assert by["yes_price_max"]["avg_price"] == 0.685
    assert by["yes_price_max"]["total_pnl_if_bought"] == -7.2  # 10 * -0.72
    assert by["low_win_summary_timezone"]["count"] == 1
    assert analysis["with_slug"] >= 1
    bands = {r["reason"]: r for r in analysis["yes_price_max_by_buy_band"]}
    assert "0.70–0.75" in bands
    assert "0.65–0.70" in bands
    assert bands["0.70–0.75"]["count"] == 1
    assert bands["0.65–0.70"]["count"] == 1
    assert analysis["total_pnl_if_bought"] == round(4.5 - 7.2, 2)

def test_skipped_price_backfill_and_005_bands(tmp_path: Path, monkeypatch):
    from src.analysis import skipped_analysis as sa

    selections = tmp_path / "selections"
    selections.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (selections / "markets_yes_2026-07-20_1400.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T14:00:00+00:00",
                "skipped_bought": [
                    {
                        "event_id": "100",
                        "city": "London",
                        "market_id": "m1",
                        "reason": "spread_max",
                        "group_item_title": "28°C",
                        "event_slug": "highest-temperature-in-london-on-july-20-2026",
                        "spread": 0.08,
                    },
                    {
                        "event_id": "101",
                        "city": "Paris",
                        "market_id": "m2",
                        "reason": "yes_price_max",
                        "group_item_title": "31°C",
                        "event_slug": "highest-temperature-in-paris-on-july-20-2026",
                        "selection_price": 0.63,
                    },
                ],
            }
        )
    )
    (data_dir / "events_2026-07-20.json").write_text(
        json.dumps(
            [
                {
                    "id": "100",
                    "slug": "highest-temperature-in-london-on-july-20-2026",
                    "markets": [
                        {
                            "id": "m1",
                            "groupItemTitle": "28°C",
                            "outcomePrices": '["0.52","0.48"]',
                        }
                    ],
                }
            ]
        )
    )
    monkeypatch.setattr(sa, "SELECTIONS_DIR", selections)
    monkeypatch.setattr(sa, "DATA_DIR", data_dir)

    analysis = sa.compute_skipped_analysis(
        selections_dir=selections,
        resolutions={
            "highest-temperature-in-london-on-july-20-2026": "28°C",
            "highest-temperature-in-paris-on-july-20-2026": "30°C",
        },
        fetch_missing_resolutions=False,
    )
    by = {r["reason"]: r for r in analysis["by_reason"]}
    assert by["spread_max"]["avg_price"] == 0.52
    assert by["spread_max"]["total_pnl_if_bought"] == 4.8  # 10*(1-0.52)
    assert by["yes_price_max"]["avg_price"] == 0.63
    bands = {r["reason"]: r for r in analysis["yes_price_max_by_buy_band"]}
    assert "0.60–0.65" in bands
    assert bands["0.60–0.65"]["count"] == 1


def test_skipped_slug_reconstruct_from_city_date(tmp_path: Path):
    (tmp_path / "markets_yes_2026-07-20_1400.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T14:00:00+00:00",
                "skipped_bought": [
                    {
                        "city": "London",
                        "reason": "spread_max",
                        "group_item_title": "28°C",
                        "market_id": "1",
                    }
                ],
            }
        )
    )
    analysis = compute_skipped_analysis(
        selections_dir=tmp_path,
        resolutions={"highest-temperature-in-london-on-july-20-2026": "28°C"},
        fetch_missing_resolutions=False,
    )
    assert analysis["resolved_skips"] == 1
    assert analysis["would_have_won_total"] == 1


def test_pattern_enrichment_minutes_and_autopsy():
    from src.analysis.pattern_enrichment import apply_pattern_enrichment

    win = _rec(
        result="win",
        bought_at_local="14:20",
        trade_window="14:00–16:00",
        yes_gap=0.18,
        token_id="w1",
    )
    loss = _rec(
        result="loss",
        bought_at_local="14:05",
        trade_window="14:00–16:00",
        yes_gap=0.02,
        win_temp_vs_bought="higher",
        winning_temp="30°C",
        realized_pnl_usd=-5.0,
        final_value_usd=-5.0,
        token_id="l1",
        city="London",
        date="2026-07-02",
        bought_at="2026-07-02T13:05:00+00:00",
    )
    apply_pattern_enrichment([win, loss])
    assert win.minutes_into_window == 20.0
    assert loss.minutes_into_window == 5.0
    assert loss.loss_autopsy in ("wrong_bucket", "never_led")
    assert win.yes_gap_at_fill == 0.18
    assert loss.city_streak is None or isinstance(loss.city_streak, str)
