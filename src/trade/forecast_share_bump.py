"""Bump buy shares when OM and WU forecasts agree vs the bought temp bucket.

Celsius: OM∩WU Δ vs bought rounds to +1°C → add FORECAST_AGREE_EXTRA_SHARES.
Fahrenheit: OM∩WU Δ vs bought rounds to +1 or +2°F → same bump.

Δ is computed in the market's native unit, preferring the matching forecast
field (°F for F markets, °C for C). Do not prefer rounded °C on F markets —
Weather.com often stores both 60°F and 61°F as 16°C, which falsely agrees.
"""

from __future__ import annotations

import logging
from typing import Optional

from config.settings import settings
from src.trade.strategies.base import MarketSelection
from src.utils.market_parser import parse_temperature_bucket, temp_bucket_sort_value

logger = logging.getLogger(__name__)


def _market_unit(bought_temp: str) -> Optional[str]:
    bucket = parse_temperature_bucket(bought_temp or "")
    if not bucket:
        return None
    return bucket[2]


def forecast_vs_bought_delta_native(
    bought_temp: str,
    *,
    forecast_temp_c: Optional[float] = None,
    forecast_temp_f: Optional[float] = None,
) -> Optional[float]:
    """Forecast − bought low-bound in the market’s native unit (°C or °F).

    Prefers the native forecast field so F markets are not collapsed through
    rounded °C (61°F and 60°F both map to 16°C).
    """
    bucket = parse_temperature_bucket(bought_temp or "")
    bought_val = temp_bucket_sort_value(bucket)
    if bucket is None or bought_val is None:
        return None
    _low, _high, unit = bucket

    if unit == "F":
        if forecast_temp_f is not None:
            forecast_f = float(forecast_temp_f)
        elif forecast_temp_c is not None:
            forecast_f = float(forecast_temp_c) * 9.0 / 5.0 + 32.0
        else:
            return None
        return round(forecast_f - float(bought_val), 2)

    # Celsius market
    if forecast_temp_c is not None:
        forecast_c = float(forecast_temp_c)
    elif forecast_temp_f is not None:
        forecast_c = (float(forecast_temp_f) - 32.0) * 5.0 / 9.0
    else:
        return None
    return round(forecast_c - float(bought_val), 2)


def om_wu_agree_delta_vs_bought(sel: MarketSelection) -> Optional[tuple[str, int]]:
    """Return (unit, rounded_delta) when OM and WU agree on Δ vs bought; else None."""
    bought = sel.group_item_title or ""
    unit = _market_unit(bought)
    if unit is None:
        return None

    om = forecast_vs_bought_delta_native(
        bought,
        forecast_temp_c=sel.forecast_temp_c,
        forecast_temp_f=sel.forecast_temp_f,
    )
    wu = forecast_vs_bought_delta_native(
        bought,
        forecast_temp_c=sel.forecast_wu_temp_c,
        forecast_temp_f=sel.forecast_wu_temp_f,
    )
    if om is None or wu is None:
        return None

    om_r = int(round(om))
    wu_r = int(round(wu))
    if om_r != wu_r:
        return None
    return unit, om_r


def should_add_forecast_agree_shares(sel: MarketSelection) -> bool:
    """True when OM∩WU Δ vs bought is +1°C, or +1/+2°F on Fahrenheit markets."""
    agreed = om_wu_agree_delta_vs_bought(sel)
    if agreed is None:
        return False
    unit, delta = agreed
    if unit == "F":
        return delta in (1, 2)
    return delta == 1


def apply_forecast_agree_extra_shares(
    selections: list[MarketSelection],
    *,
    extra_shares: Optional[int] = None,
) -> list[MarketSelection]:
    """Increase selection.share_count when OM∩WU forecast-agree bump applies.

    Extra defaults to FORECAST_AGREE_EXTRA_SHARES (0 disables). Idempotent: skips
    selections that already received this bump.
    """
    extra = (
        settings.forecast_agree_extra_shares
        if extra_shares is None
        else int(extra_shares)
    )
    if extra <= 0:
        return selections

    for sel in selections:
        prior = getattr(sel, "_forecast_agree_bump", None)
        if isinstance(prior, dict) and prior.get("applied"):
            continue
        if not should_add_forecast_agree_shares(sel):
            continue
        agreed = om_wu_agree_delta_vs_bought(sel)
        before = int(sel.share_count)
        sel.share_count = before + extra
        unit, delta = agreed if agreed else ("?", 0)
        logger.info(
            "event=%s city=%s temp=%s OM∩WU Δ=+%s°%s; share bump %d → %d (+%d)",
            sel.event_id,
            sel.city,
            sel.group_item_title,
            delta,
            unit,
            before,
            sel.share_count,
            extra,
        )
        step_log = sel.event.get("_step_logger") if sel.event else None
        if step_log:
            step_log.log_step(
                "forecast_agree_share_bump",
                unit=unit,
                delta=delta,
                shares_before=before,
                shares_after=sel.share_count,
                extra_shares=extra,
            )
        sel._forecast_agree_bump = {  # type: ignore[attr-defined]
            "applied": True,
            "unit": unit,
            "delta": delta,
            "extra_shares": extra,
            "shares_before": before,
            "shares_after": sel.share_count,
        }
    return selections
