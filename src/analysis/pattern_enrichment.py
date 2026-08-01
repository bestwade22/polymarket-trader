"""Pattern fields and loss-autopsy tags for trade history research."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from src.analysis.models import (
    TradeRecord,
    _counts_toward_win_summary,
    _counts_toward_win_summary_denom,
    _is_sold_would_win,
    _record_pnl_value,
)
from src.utils.market_parser import parse_temperature_bucket, temp_bucket_sort_value

logger = logging.getLogger(__name__)

PATTERN_FIELDS = (
    "yes_gap_at_select",
    "yes_gap_at_fill",
    "minutes_into_window",
    "forecast_temp_f",
    "forecast_temp_c",
    "forecast_delta_c",
    "forecast_source",
    "forecast_wu_temp_f",
    "forecast_wu_temp_c",
    "book_depth_near_touch",
    "price_change_30m",
    "price_change_90m",
    "city_streak",
    "loss_autopsy",
)


def _parse_hhmm(value: str) -> Optional[int]:
    text = (value or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def minutes_into_trading_window(
    bought_at_local: str,
    trade_window: str,
) -> Optional[float]:
    """Minutes after window start when the buy happened (local clock)."""
    buy_m = _parse_hhmm(bought_at_local)
    if buy_m is None:
        return None
    window = (trade_window or "").replace("–", "-").replace("—", "-")
    parts = re.split(r"\s*-\s*", window)
    if len(parts) < 2:
        return None
    start_m = _parse_hhmm(parts[0].strip())
    if start_m is None:
        return None
    return float(buy_m - start_m)


def forecast_delta_c(
    bought_temp: str,
    forecast_temp_f: Optional[float] = None,
    forecast_temp_c: Optional[float] = None,
) -> Optional[float]:
    """Forecast max − selected bucket midpoint, in °C when convertible."""
    bucket = parse_temperature_bucket(bought_temp or "")
    bought_val = temp_bucket_sort_value(bucket)
    if bought_val is None or not bucket:
        return None
    _low, _high, unit = bucket
    if forecast_temp_c is not None:
        forecast_c = float(forecast_temp_c)
    elif forecast_temp_f is not None:
        forecast_c = (float(forecast_temp_f) - 32.0) * 5.0 / 9.0
    else:
        return None
    if unit == "F":
        bought_c = (float(bought_val) - 32.0) * 5.0 / 9.0
    else:
        bought_c = float(bought_val)
    return round(forecast_c - bought_c, 2)


def classify_loss_autopsy(rec: TradeRecord) -> Optional[str]:
    """Tag settled losses for sell-timing vs selection-quality diagnosis."""
    if rec.result == "open":
        return None
    if not _counts_toward_win_summary_denom(rec):
        return None
    if _counts_toward_win_summary(rec):
        return None

    # Sold early but would have won → sell timing
    if _is_sold_would_win(rec) or (
        rec.result == "sold" and rec.sold_but_would_have_won
    ):
        return "sold_too_early"

    gap_select = rec.yes_gap_at_select
    gap_fill = rec.yes_gap_at_fill if rec.yes_gap_at_fill is not None else rec.yes_gap
    if (
        gap_select is not None
        and gap_fill is not None
        and gap_select >= 0.10
        and gap_fill < gap_select - 0.05
    ):
        return "gap_collapsed"

    if rec.win_temp_vs_bought in ("higher", "lower"):
        return "wrong_bucket"

    # Leader was never clearly ahead at buy
    if gap_fill is not None and gap_fill < 0.05:
        return "never_led"
    if gap_select is not None and gap_select < 0.05:
        return "never_led"

    if rec.result == "loss":
        return "wrong_bucket"
    if rec.result == "sold":
        pnl = _record_pnl_value(rec)
        if pnl is not None and pnl < 0:
            return "sold_too_early"
    return "wrong_bucket"


def _city_streak_label(prior: list[TradeRecord], *, lookback: int = 3) -> Optional[str]:
    """W/L string for last `lookback` settled same-city trades (oldest→newest)."""
    settled: list[str] = []
    for rec in prior:
        if not _counts_toward_win_summary_denom(rec):
            continue
        settled.append("W" if _counts_toward_win_summary(rec) else "L")
        if len(settled) >= lookback:
            break
    if not settled:
        return None
    # prior is newest-first; reverse for chronological streak text
    return "".join(reversed(settled))


def apply_pattern_enrichment(records: list[TradeRecord]) -> dict[str, int]:
    """Fill pattern fields on records in-place. Returns fill counts by field."""
    counts = {f: 0 for f in PATTERN_FIELDS}
    if not records:
        return counts

    # Newest first for streak lookback
    ordered = sorted(
        records,
        key=lambda r: (r.bought_at or "", r.token_id or ""),
        reverse=True,
    )
    by_city: dict[str, list[TradeRecord]] = {}
    for rec in ordered:
        by_city.setdefault(rec.city or "", []).append(rec)

    # Process chronological for streaks: oldest first
    chrono = list(reversed(ordered))
    city_history: dict[str, list[TradeRecord]] = {city: [] for city in by_city}

    for rec in chrono:
        # minutes into window
        if rec.minutes_into_window is None:
            mins = minutes_into_trading_window(rec.bought_at_local, rec.trade_window)
            if mins is not None:
                rec.minutes_into_window = mins
                counts["minutes_into_window"] += 1

        # gap at select / fill
        if rec.yes_gap_at_fill is None and rec.yes_gap is not None:
            rec.yes_gap_at_fill = rec.yes_gap
            counts["yes_gap_at_fill"] += 1
        if rec.yes_gap_at_select is None and rec.yes_gap is not None:
            # Without a separate select snapshot, use fill as best available.
            rec.yes_gap_at_select = rec.yes_gap
            counts["yes_gap_at_select"] += 1

        # city streak from prior settled trades only
        city = rec.city or ""
        prior = list(reversed(city_history.get(city, [])))
        if rec.city_streak is None:
            streak = _city_streak_label(prior, lookback=3)
            if streak is not None:
                rec.city_streak = streak
                counts["city_streak"] += 1

        # sell-derived short price path proxies
        if (
            rec.price_change_30m is None
            and rec.sell_price is not None
            and rec.buy_price
            and rec.held_hours is not None
            and rec.held_hours <= 0.5
        ):
            rec.price_change_30m = round(float(rec.sell_price) - float(rec.buy_price), 4)
            counts["price_change_30m"] += 1
        if (
            rec.price_change_90m is None
            and rec.sell_price is not None
            and rec.buy_price
            and rec.held_hours is not None
            and rec.held_hours <= 1.5
        ):
            rec.price_change_90m = round(float(rec.sell_price) - float(rec.buy_price), 4)
            counts["price_change_90m"] += 1

        # autopsy last so it can use gap fields
        if rec.loss_autopsy is None:
            tag = classify_loss_autopsy(rec)
            if tag is not None:
                rec.loss_autopsy = tag
                counts["loss_autopsy"] += 1

        city_history.setdefault(city, []).append(rec)

    filled = sum(counts.values())
    if filled:
        logger.info("Pattern enrichment fills: %s", {k: v for k, v in counts.items() if v})
    return counts


def apply_selection_pattern_fields(
    rec: TradeRecord,
    enrichment: dict[str, Any],
) -> list[str]:
    """Copy select-time pattern fields from a selection/enrichment row."""
    filled: list[str] = []
    if rec.yes_gap_at_select is None and enrichment.get("yes_gap") is not None:
        rec.yes_gap_at_select = float(enrichment["yes_gap"])
        filled.append("yes_gap_at_select")
    if rec.book_depth_near_touch is None:
        depth = enrichment.get("book_depth_near_touch")
        if depth is None:
            depth = enrichment.get("liquidity") or enrichment.get("liquidityNum")
        if depth is not None:
            try:
                rec.book_depth_near_touch = float(depth)
                filled.append("book_depth_near_touch")
            except (TypeError, ValueError):
                pass
    if rec.forecast_temp_c is None and enrichment.get("forecast_temp_c") is not None:
        try:
            rec.forecast_temp_c = float(enrichment["forecast_temp_c"])
            filled.append("forecast_temp_c")
        except (TypeError, ValueError):
            pass
    if rec.forecast_temp_f is None and enrichment.get("forecast_temp_f") is not None:
        try:
            rec.forecast_temp_f = float(enrichment["forecast_temp_f"])
            filled.append("forecast_temp_f")
        except (TypeError, ValueError):
            pass
    if rec.forecast_source is None and enrichment.get("forecast_source"):
        rec.forecast_source = str(enrichment["forecast_source"])
        filled.append("forecast_source")
    if rec.forecast_wu_temp_c is None and enrichment.get("forecast_wu_temp_c") is not None:
        try:
            rec.forecast_wu_temp_c = float(enrichment["forecast_wu_temp_c"])
            filled.append("forecast_wu_temp_c")
        except (TypeError, ValueError):
            pass
    if rec.forecast_wu_temp_f is None and enrichment.get("forecast_wu_temp_f") is not None:
        try:
            rec.forecast_wu_temp_f = float(enrichment["forecast_wu_temp_f"])
            filled.append("forecast_wu_temp_f")
        except (TypeError, ValueError):
            pass
    if rec.forecast_delta_c is None:
        delta = forecast_delta_c(
            rec.bought_temp,
            forecast_temp_f=enrichment.get("forecast_temp_f")
            if enrichment.get("forecast_temp_f") is not None
            else rec.forecast_temp_f,
            forecast_temp_c=enrichment.get("forecast_temp_c")
            if enrichment.get("forecast_temp_c") is not None
            else rec.forecast_temp_c,
        )
        if delta is not None:
            rec.forecast_delta_c = delta
            filled.append("forecast_delta_c")
    return filled
