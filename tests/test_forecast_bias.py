"""Forecast − result bias helpers and city bias insights."""

from __future__ import annotations

from src.analysis.models import TradeRecord
from src.analysis.strategy_insights import (
    FORECAST_BIAS_SINCE,
    compute_city_forecast_bias,
    compute_insights,
)
from src.utils.market_parser import (
    forecast_vs_result_delta_c,
    winning_temp_midpoint_c,
)


def _rec(**kwargs) -> TradeRecord:
    defaults = dict(
        date="2026-08-10",
        city="Tokyo",
        bought_temp="30°C",
        trade_window="14:00–16:00",
        bought_at="2026-08-10T06:00:00+00:00",
        sold_at=None,
        redeemed_at=None,
        shares=10.0,
        result="win",
        final_value_usd=5.0,
        winning_temp="30°C",
        win_temp_vs_bought="same",
        price_drop_below_threshold_at=None,
        sold_but_would_have_won=False,
        buy_price=0.50,
        sell_price=None,
        cost_basis_usd=5.0,
        realized_pnl_usd=5.0,
        roi_pct=100.0,
        sell_value_pct=None,
        held_hours=None,
        event_slug="highest-temperature-in-tokyo-on-august-10-2026",
        token_id="t1",
        condition_id="c1",
        transaction_hash=None,
    )
    defaults.update(kwargs)
    return TradeRecord(**defaults)


def test_winning_temp_midpoint_c_single_and_range():
    assert winning_temp_midpoint_c("30°C") == 30.0
    assert winning_temp_midpoint_c("28-29°C") == 28.5


def test_forecast_vs_result_delta_hot_and_cold():
    # Forecast 32 vs win 30 → +2 (hot)
    assert forecast_vs_result_delta_c("30°C", forecast_temp_c=32) == 2.0
    # Forecast 28 vs win 30 → -2 (cold)
    assert forecast_vs_result_delta_c("30°C", forecast_temp_c=28) == -2.0


def test_city_bias_uses_since_cutoff_and_corr():
    records = [
        # Before cutoff — ignored
        _rec(
            date="2026-08-01",
            city="Paris",
            token_id="old",
            forecast_temp_c=40,
            winning_temp="30°C",
            result="loss",
            win_temp_vs_bought="higher",
            realized_pnl_usd=-5.0,
            final_value_usd=-5.0,
        ),
        # After cutoff: OM +2, WU +1
        _rec(
            date="2026-08-10",
            city="Paris",
            token_id="p1",
            forecast_temp_c=32,
            forecast_wu_temp_c=31,
            winning_temp="30°C",
        ),
        _rec(
            date="2026-08-11",
            city="Paris",
            token_id="p2",
            forecast_temp_c=34,
            forecast_wu_temp_c=33,
            winning_temp="30°C",
            result="loss",
            win_temp_vs_bought="higher",
            realized_pnl_usd=-5.0,
            final_value_usd=-5.0,
        ),
    ]
    bias = compute_city_forecast_bias(records)
    assert FORECAST_BIAS_SINCE.isoformat() == "2026-08-04"
    assert "Paris" in bias
    assert bias["Paris"]["om_bias_n"] == 2
    assert bias["Paris"]["om_bias_mean"] == 3.0  # (2+4)/2
    assert bias["Paris"]["om_corr"] == -3.0
    assert bias["Paris"]["wu_bias_n"] == 2
    assert bias["Paris"]["wu_bias_mean"] == 2.0  # (1+3)/2
    assert bias["Paris"]["wu_corr"] == -2.0


def test_insights_merge_city_bias_and_vs_result_bands():
    records = [
        _rec(
            date="2026-08-10",
            city="London",
            token_id="l1",
            forecast_temp_c=28,
            forecast_wu_temp_c=27,
            bought_temp="27°C",
            winning_temp="27°C",
        ),
        _rec(
            date="2026-08-12",
            city="London",
            token_id="l2",
            forecast_temp_c=27,
            forecast_wu_temp_c=27,
            bought_temp="27°C",
            winning_temp="27°C",
        ),
    ]
    insights = compute_insights(records)
    city = insights["summary_by_city"]["London"]
    assert city["om_bias_n"] == 2
    assert city["om_corr"] is not None
    assert insights["forecast_bias_since"] == "2026-08-04"
    assert "summary_by_om_vs_result_band" in insights
    assert "summary_by_wu_vs_result_band" in insights
    # OM deltas: +1 and 0
    om_bands = insights["summary_by_om_vs_result_band"]
    assert om_bands.get("+1", {}).get("count", 0) + om_bands.get("0", {}).get("count", 0) == 2
