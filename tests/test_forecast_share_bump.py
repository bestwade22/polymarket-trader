"""Tests for OM∩WU forecast-agree share bump."""

from src.trade.forecast_share_bump import (
    apply_forecast_agree_extra_shares,
    om_wu_agree_delta_vs_bought,
    should_add_forecast_agree_shares,
)
from src.trade.strategies.base import MarketSelection


def _sel(
    *,
    temp: str = "26°C",
    shares: int = 15,
    om_c=None,
    om_f=None,
    wu_c=None,
    wu_f=None,
) -> MarketSelection:
    return MarketSelection(
        event_id="e1",
        city="Milan",
        market_id="m1",
        group_item_title=temp,
        yes_price=0.50,
        yes_token_id="tok",
        buy_price=0.50,
        share_count=shares,
        neg_risk=True,
        tick_size="0.01",
        order_min_size=5,
        strategy="highest_yes",
        forecast_temp_c=om_c,
        forecast_temp_f=om_f,
        forecast_wu_temp_c=wu_c,
        forecast_wu_temp_f=wu_f,
        event={"slug": "highest-temperature-in-milan"},
    )


def test_milan_plus_one_c_bumps_shares():
    """Bought 26°C, OM∩WU 27°C → +5 → 20 when base is 15."""
    sel = _sel(temp="26°C", shares=15, om_c=27, wu_c=27)
    assert should_add_forecast_agree_shares(sel) is True
    assert om_wu_agree_delta_vs_bought(sel) == ("C", 1)
    apply_forecast_agree_extra_shares([sel], extra_shares=5)
    assert sel.share_count == 20


def test_celsius_zero_delta_no_bump():
    sel = _sel(temp="26°C", shares=15, om_c=26, wu_c=26)
    assert should_add_forecast_agree_shares(sel) is False
    apply_forecast_agree_extra_shares([sel], extra_shares=5)
    assert sel.share_count == 15


def test_celsius_disagree_no_bump():
    sel = _sel(temp="26°C", shares=15, om_c=27, wu_c=28)
    assert should_add_forecast_agree_shares(sel) is False


def test_fahrenheit_plus_one_or_two_bumps():
    sel1 = _sel(temp="82°F", shares=15, om_f=83, wu_f=83)
    assert should_add_forecast_agree_shares(sel1) is True
    assert om_wu_agree_delta_vs_bought(sel1) == ("F", 1)

    sel2 = _sel(temp="82°F", shares=15, om_f=84, wu_f=84)
    assert should_add_forecast_agree_shares(sel2) is True
    assert om_wu_agree_delta_vs_bought(sel2) == ("F", 2)

    sel3 = _sel(temp="82°F", shares=15, om_f=85, wu_f=85)
    assert should_add_forecast_agree_shares(sel3) is False


def test_bump_idempotent():
    sel = _sel(temp="26°C", shares=15, om_c=27, wu_c=27)
    apply_forecast_agree_extra_shares([sel], extra_shares=5)
    apply_forecast_agree_extra_shares([sel], extra_shares=5)
    assert sel.share_count == 20


def test_extra_zero_disables():
    sel = _sel(temp="26°C", shares=15, om_c=27, wu_c=27)
    apply_forecast_agree_extra_shares([sel], extra_shares=0)
    assert sel.share_count == 15
