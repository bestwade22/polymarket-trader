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
    names = {row["stack"] for row in sweep["stacks"]}
    assert "skip_bottom7_tz_surviving" in names
    assert "skip_bottom7_tz_surviving + spread_live + buy_live" in names


def test_filter_sweep_surviving_skip_ranks_from_surviving_pool(monkeypatch):
    """Surviving-ranked bottom-7 can differ from all-train ranking."""
    from src.analysis.filter_sweep import (
        _bottom_tz_set,
        _bottom_tz_set_surviving,
        _pred_from_name_parts,
    )
    from src.trade.city_skip import lowest_win_summary_timezones, surviving_records_for_skip

    monkeypatch.setattr("src.trade.city_skip.settings.yes_price_min", 0.45)
    monkeypatch.setattr("src.trade.city_skip.settings.yes_price_max", 0.60)
    monkeypatch.setattr("src.trade.city_skip.settings.spread_max", 0.05)
    monkeypatch.setattr("src.analysis.filter_sweep.settings.yes_price_min", 0.45)
    monkeypatch.setattr("src.analysis.filter_sweep.settings.yes_price_max", 0.60)
    monkeypatch.setattr("src.analysis.filter_sweep.settings.spread_max", 0.05)
    mapping = {
        "GoodCity": "Good TZ",
        "BadSurv": "Bad Surv TZ",
        "BadAll": "Bad All TZ",
    }
    monkeypatch.setattr(
        "src.analysis.filter_sweep.timezone_group",
        lambda city: mapping.get(city, "Unknown"),
    )
    monkeypatch.setattr(
        "src.trade.city_skip.timezone_group",
        lambda city: mapping.get(city, "Unknown"),
    )

    records = []
    # Surviving winners for Good TZ
    for i in range(12):
        records.append(
            _rec(
                date="2026-07-01",
                city="GoodCity",
                token_id=f"g{i}",
                buy_price=0.50,
                spread=0.02,
                result="win",
                win_temp_vs_bought="same",
                realized_pnl_usd=2.0,
                final_value_usd=2.0,
            )
        )
    # Surviving-only losers for Bad Surv (0% in surviving pool)
    for i in range(5):
        records.append(
            _rec(
                date="2026-07-02",
                city="BadSurv",
                token_id=f"bs{i}",
                buy_price=0.50,
                spread=0.02,
                result="loss",
                win_temp_vs_bought="higher",
                realized_pnl_usd=-3.0,
                final_value_usd=-3.0,
            )
        )
    # Cheap winners for Bad Surv so all-records win% looks decent
    for i in range(10):
        records.append(
            _rec(
                date="2026-07-02",
                city="BadSurv",
                token_id=f"bsw{i}",
                buy_price=0.30,
                spread=0.02,
                result="win",
                win_temp_vs_bought="same",
                realized_pnl_usd=2.0,
                final_value_usd=2.0,
            )
        )
    # Many cheap losers for Bad All → worst on all-records ranking
    for i in range(20):
        records.append(
            _rec(
                date="2026-07-01",
                city="BadAll",
                token_id=f"ba{i}",
                buy_price=0.30,
                spread=0.02,
                result="loss",
                win_temp_vs_bought="higher",
                realized_pnl_usd=-3.0,
                final_value_usd=-3.0,
            )
        )
    # Surviving winners for Bad All → fine under surviving ranking
    for i in range(8):
        records.append(
            _rec(
                date="2026-07-02",
                city="BadAll",
                token_id=f"baw{i}",
                buy_price=0.50,
                spread=0.02,
                result="win",
                win_temp_vs_bought="same",
                realized_pnl_usd=2.0,
                final_value_usd=2.0,
            )
        )

    worst_all = lowest_win_summary_timezones(records, bottom_n=1)
    surviving = surviving_records_for_skip(records)
    worst_surv = lowest_win_summary_timezones(surviving, bottom_n=1)
    assert worst_all == ["Bad All TZ"]
    assert worst_surv == ["Bad Surv TZ"]
    assert _bottom_tz_set(records, 1) == {"Bad All TZ"}
    assert _bottom_tz_set_surviving(records, 1) == {"Bad Surv TZ"}

    # Live stack allows missing spread; strict spread<0.05 does not
    missing = _rec(
        date="2026-07-05",
        city="GoodCity",
        token_id="miss",
        buy_price=0.50,
        spread=None,
        result="win",
    )
    assert _pred_from_name_parts(
        "skip_bottom7_tz_surviving + spread_live + buy_live", missing, set()
    )
    assert not _pred_from_name_parts(
        "skip_bottom7_tz_surviving + spread<0.05 + buy>=0.45", missing, set()
    )
    # YES_PRICE_MAX: buy at 0.60 fails buy_live
    high = _rec(
        date="2026-07-05",
        city="GoodCity",
        token_id="hi",
        buy_price=0.60,
        spread=0.02,
        result="win",
    )
    assert not _pred_from_name_parts(
        "skip_bottom7_tz_surviving + spread_live + buy_live", high, set()
    )


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
    assert "recent_skips" in analysis
    assert len(analysis["recent_skips"]) == 4
    assert "forecast_compare" in analysis
    assert "overall" in analysis["forecast_compare"]
    assert "by_local_slot" in analysis["forecast_compare"]
    assert "by_reason" in analysis["forecast_compare"]
    assert "yes_price_max_by_buy_band_first_skip" in analysis


def test_yes_price_max_first_skip_dedupes_market(tmp_path: Path):
    """Repeated yes_price_max skips of same market count once in first-skip table."""
    (tmp_path / "markets_yes_2026-07-20_1400.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T14:00:00+00:00",
                "skipped_bought": [
                    {
                        "city": "Paris",
                        "market_id": "m-paris",
                        "reason": "yes_price_max",
                        "group_item_title": "31°C",
                        "event_slug": "highest-temperature-in-paris-on-july-20-2026",
                        "selection_price": 0.72,
                    }
                ],
            }
        )
    )
    (tmp_path / "markets_yes_2026-07-20_1430.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T14:30:00+00:00",
                "skipped_bought": [
                    {
                        "city": "Paris",
                        "market_id": "m-paris",
                        "reason": "yes_price_max",
                        "group_item_title": "31°C",
                        "event_slug": "highest-temperature-in-paris-on-july-20-2026",
                        "selection_price": 0.78,
                    },
                    {
                        "city": "Berlin",
                        "market_id": "m-berlin",
                        "reason": "yes_price_max",
                        "group_item_title": "30°C",
                        "event_slug": "highest-temperature-in-berlin-on-july-20-2026",
                        "selection_price": 0.65,
                    },
                ],
            }
        )
    )
    analysis = compute_skipped_analysis(
        selections_dir=tmp_path,
        resolutions={
            "highest-temperature-in-paris-on-july-20-2026": "30°C",
            "highest-temperature-in-berlin-on-july-20-2026": "30°C",
        },
        fetch_missing_resolutions=False,
    )
    all_bands = {r["reason"]: r for r in analysis["yes_price_max_by_buy_band"]}
    first_bands = {r["reason"]: r for r in analysis["yes_price_max_by_buy_band_first_skip"]}
    # All skips: Paris twice (0.70–0.75 and 0.75–0.80) + Berlin once
    assert sum(r["count"] for r in all_bands.values()) == 3
    # First skip only: Paris once at 0.72 + Berlin once at 0.65
    assert sum(r["count"] for r in first_bands.values()) == 2
    assert first_bands["0.70–0.75"]["count"] == 1
    assert first_bands["0.65–0.70"]["count"] == 1
    assert "0.75–0.80" not in first_bands


def test_yes_price_max_first_skip_ignores_earlier_other_reason(tmp_path: Path):
    """Earlier non-yes_price_max skip must not block the first yes_price_max of that market."""
    (tmp_path / "markets_yes_2026-08-02_1315.json").write_text(
        json.dumps(
            {
                "run_at": "2026-08-02T13:15:00+00:00",
                "skipped_bought": [
                    {
                        "city": "London",
                        "market_id": "m-lon-27",
                        "reason": "spread_max",
                        "group_item_title": "27°C",
                        "event_slug": "highest-temperature-in-london-on-august-2-2026",
                        "selection_price": 0.55,
                        "spread": 0.08,
                    }
                ],
            }
        )
    )
    (tmp_path / "markets_yes_2026-08-02_1415.json").write_text(
        json.dumps(
            {
                "run_at": "2026-08-02T14:15:00+00:00",
                "skipped_bought": [
                    {
                        "city": "London",
                        "market_id": "m-lon-27",
                        "reason": "yes_price_max",
                        "group_item_title": "27°C",
                        "event_slug": "highest-temperature-in-london-on-august-2-2026",
                        "selection_price": 0.63,
                    }
                ],
            }
        )
    )
    (tmp_path / "markets_yes_2026-08-02_1445.json").write_text(
        json.dumps(
            {
                "run_at": "2026-08-02T14:45:00+00:00",
                "skipped_bought": [
                    {
                        "city": "London",
                        "market_id": "m-lon-27",
                        "reason": "yes_price_max",
                        "group_item_title": "27°C",
                        "event_slug": "highest-temperature-in-london-on-august-2-2026",
                        "selection_price": 0.71,
                    }
                ],
            }
        )
    )
    analysis = compute_skipped_analysis(
        selections_dir=tmp_path,
        resolutions={"highest-temperature-in-london-on-august-2-2026": "27°C"},
        fetch_missing_resolutions=False,
    )
    all_n = sum(r["count"] for r in analysis["yes_price_max_by_buy_band"])
    first_n = sum(r["count"] for r in analysis["yes_price_max_by_buy_band_first_skip"])
    assert all_n == 2
    assert first_n == 1
    first_bands = {r["reason"]: r for r in analysis["yes_price_max_by_buy_band_first_skip"]}
    # First yes_price_max was 0.63 at 14:15, not the 0.71 re-skip
    assert first_bands["0.60–0.65"]["count"] == 1
    assert first_bands["0.60–0.65"]["would_have_won"] == 1


def test_forecast_match_uses_all_skips_vs_event_result(tmp_path: Path):
    """Forecast-compare tables only include skips that have a forecast value."""
    (tmp_path / "markets_yes_2026-07-20_1400.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T14:00:00+00:00",
                "skipped_bought": [
                    {
                        "city": "London",
                        "reason": "spread_max",
                        "group_item_title": "28°C",
                        "event_slug": "highest-temperature-in-london-on-july-20-2026",
                        "selection_price": 0.55,
                        "spread": 0.08,
                        "forecast_temp_c": 28,
                        "forecast_temp_f": 82,
                    },
                    {
                        "city": "Paris",
                        "reason": "yes_price_max",
                        "group_item_title": "31°C",
                        "event_slug": "highest-temperature-in-paris-on-july-20-2026",
                        "selection_price": 0.72,
                        # no forecast — excluded from forecast_compare tables
                    },
                    {
                        "city": "London",
                        "reason": "yes_price_max",
                        "group_item_title": "28°C",
                        "event_slug": "highest-temperature-in-london-on-july-20-2026",
                        "selection_price": 0.66,
                        "forecast_temp_c": 30,
                        "forecast_temp_f": 86,
                    },
                ],
            }
        )
    )
    analysis = compute_skipped_analysis(
        selections_dir=tmp_path,
        resolutions={
            "highest-temperature-in-london-on-july-20-2026": "28°C",
            "highest-temperature-in-paris-on-july-20-2026": "30°C",
        },
        fetch_missing_resolutions=False,
    )
    overall = analysis["forecast_compare"]["overall"]
    assert overall["count"] == 2  # Paris without forecast excluded
    assert overall["resolved"] == 2
    assert overall["would_have_won"] == 2  # both London skips would win
    assert overall["om_match_resolved"] == 2
    assert overall["om_match"] == 1  # 28 matches; 30 misses
    by_reason = {r["group"]: r for r in analysis["forecast_compare"]["by_reason"]}
    assert by_reason["spread_max"]["count"] == 1
    assert by_reason["yes_price_max"]["count"] == 1
    assert "Paris" not in str(by_reason) or "yes_price_max" in by_reason


def test_forecast_match_overall_vs_winning(tmp_path: Path):
    (tmp_path / "markets_yes_2026-07-20_1400.json").write_text(
        json.dumps(
            {
                "run_at": "2026-07-20T14:00:00+00:00",
                "skipped_bought": [
                    {
                        "city": "London",
                        "reason": "spread_max",
                        "group_item_title": "28°C",
                        "event_slug": "highest-temperature-in-london-on-july-20-2026",
                        "selection_price": 0.55,
                        "spread": 0.08,
                        "forecast_temp_c": 28,
                        "forecast_temp_f": 82,
                        "forecast_wu_temp_c": 30,
                        "forecast_wu_temp_f": 86,
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
    overall = analysis["forecast_compare"]["overall"]
    assert overall["om_match_pct"] == 100.0
    assert overall["wu_match_pct"] == 0.0
    assert overall["would_have_won_pct"] == 100.0


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
