"""Resolve simulated positions to win/loss/sold/open and PnL."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.analysis.models import TradeRecord, recompute_sold_but_would_have_won
from src.analysis.resolution import (
    CachedResolution,
    fetch_resolved_event,
    load_cached_resolution,
    resolve_winning_temp,
)
from src.simulation.buy_pass import SimulatedBuy
from src.simulation.sell_pass import SimulatedSell
from src.utils.hk_time import format_hk
from src.utils.market_parser import compare_temp_buckets, parse_float
from src.utils.time_window import trading_window_label

logger = logging.getLogger(__name__)


def _format_local_hhmm(ts: int, tz_name: Optional[str]) -> str:
    if not tz_name:
        return ""
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%H:%M")
    except Exception:
        return ""


def _pnl(
    result: str,
    shares: float,
    buy_price: float,
    sell_price: Optional[float],
) -> Optional[float]:
    cost = shares * buy_price
    if result == "win":
        return round(shares * 1.0 - cost, 4)
    if result == "loss":
        return round(-cost, 4)
    if result == "sold" and sell_price is not None:
        return round(shares * sell_price - cost, 4)
    return None


def _resolution_is_conclusive(resolution: CachedResolution) -> bool:
    if resolution.winning_token_id:
        return True
    return bool(resolution.winning_temp)


def _fetch_resolution_for_conclude(event_slug: str) -> Optional[CachedResolution]:
    """Prefer conclusive cache; otherwise refresh from Gamma (stale open caches)."""
    if not event_slug:
        return None
    cached = load_cached_resolution(event_slug)
    if cached is not None and _resolution_is_conclusive(cached):
        return cached
    return fetch_resolved_event(event_slug, use_cache=False)


def _classify_hold_result(
    *,
    token_id: str,
    bought_temp: str,
    resolution: CachedResolution,
) -> Optional[str]:
    """Return win/loss when resolution is conclusive enough; else None (stay open)."""
    winning_temp = resolution.winning_temp
    winning_token = resolution.winning_token_id
    if winning_token:
        return "win" if winning_token == token_id else "loss"
    if winning_temp:
        return (
            "win"
            if compare_temp_buckets(bought_temp, winning_temp) == "same"
            else "loss"
        )
    return None


def _apply_hold_pnl(row: dict[str, Any], result: str) -> None:
    shares = float(row.get("shares") or 0)
    buy_price = float(row.get("buy_price") or 0)
    cost_basis = round(shares * buy_price, 4)
    row["cost_basis_usd"] = cost_basis
    final_value = _pnl(result, shares, buy_price, None)
    row["final_value_usd"] = final_value
    row["realized_pnl_usd"] = final_value
    row["roi_pct"] = (
        round((final_value / cost_basis) * 100, 2)
        if final_value is not None and cost_basis > 0
        else None
    )
    if result == "win":
        row["outcome_value_usd"] = round(shares * 1.0, 4)
    elif result == "loss":
        row["outcome_value_usd"] = 0.0
    else:
        row["outcome_value_usd"] = None


def _recompute_sold_flag(row: dict[str, Any]) -> None:
    pnl = row.get("realized_pnl_usd")
    if pnl is None:
        pnl = row.get("final_value_usd")
    row["sold_but_would_have_won"] = bool(
        row.get("result") == "sold"
        and pnl is not None
        and float(pnl) < 0
        and row.get("win_temp_vs_bought") == "same"
    )


def conclude_open_sim_records(records: list[dict[str, Any]]) -> dict[str, int]:
    """Resolve open (and unknown sold) sim rows from resolutions cache / Gamma.

    Called on every simulate-trades run so skipped completed dates still get
    outcomes once markets resolve.
    """
    open_candidates = [
        r for r in records if isinstance(r, dict) and r.get("result") == "open"
    ]
    sold_unknown = [
        r
        for r in records
        if isinstance(r, dict)
        and r.get("result") == "sold"
        and (r.get("win_temp_vs_bought") == "unknown" or not r.get("winning_temp"))
    ]
    if not open_candidates and not sold_unknown:
        return {"open_concluded": 0, "sold_enriched": 0, "still_open": 0}

    concluded = 0
    enriched = 0
    for row in open_candidates:
        slug = str(row.get("event_slug") or "")
        resolution = _fetch_resolution_for_conclude(slug)
        if resolution is None:
            continue
        result = _classify_hold_result(
            token_id=str(row.get("token_id") or ""),
            bought_temp=str(row.get("bought_temp") or ""),
            resolution=resolution,
        )
        if result is None:
            continue
        winning_temp = resolution.winning_temp
        row["result"] = result
        row["winning_temp"] = winning_temp
        row["win_temp_vs_bought"] = (
            compare_temp_buckets(str(row.get("bought_temp") or ""), winning_temp)
            if winning_temp
            else "unknown"
        )
        _apply_hold_pnl(row, result)
        concluded += 1

    for row in sold_unknown:
        slug = str(row.get("event_slug") or "")
        resolution = _fetch_resolution_for_conclude(slug)
        if resolution is None or not resolution.winning_temp:
            continue
        row["winning_temp"] = resolution.winning_temp
        row["win_temp_vs_bought"] = compare_temp_buckets(
            str(row.get("bought_temp") or ""), resolution.winning_temp
        )
        _recompute_sold_flag(row)
        enriched += 1

    still_open = sum(
        1 for r in records if isinstance(r, dict) and r.get("result") == "open"
    )
    if concluded or enriched:
        logger.info(
            "Concluded %d open sim records; enriched %d sold; %d still open",
            concluded,
            enriched,
            still_open,
        )
    elif still_open:
        logger.info("No new resolutions yet; %d sim records still open", still_open)
    return {
        "open_concluded": concluded,
        "sold_enriched": enriched,
        "still_open": still_open,
    }


def build_sim_record(
    buy: SimulatedBuy,
    sell: Optional[SimulatedSell],
    *,
    share_count: int,
) -> dict[str, Any]:
    """Build a TradeRecord-shaped dict plus sim metadata."""
    event = buy.event
    selection = buy.selection
    city = selection.city or str(event.get("city") or "")
    tz_name = str(event.get("timezone") or "")
    event_slug = str(event.get("slug") or "")
    bought_temp = selection.group_item_title or ""
    shares = float(share_count)
    buy_price = round(buy.buy_price, 4)
    cost_basis = round(shares * buy_price, 4)
    bought_ts = int(buy.bought_at.timestamp())
    bought_at_iso = buy.bought_at.astimezone(timezone.utc).isoformat()
    bought_at_hk = format_hk(buy.bought_at)
    bought_at_local = buy.sample_time_local or _format_local_hhmm(bought_ts, tz_name)

    sold_at_iso: Optional[str] = None
    sold_at_hk = ""
    sold_at_local = ""
    sell_price: Optional[float] = None
    held_hours: Optional[float] = None

    if sell is not None:
        result = "sold"
        sell_price = round(sell.sell_price, 4)
        sold_ts = int(sell.sold_at.timestamp())
        sold_at_iso = sell.sold_at.astimezone(timezone.utc).isoformat()
        sold_at_hk = format_hk(sell.sold_at)
        sold_at_local = _format_local_hhmm(sold_ts, tz_name)
        held_hours = round((sold_ts - bought_ts) / 3600.0, 2)
        winning_temp = resolve_winning_temp(event_slug)
    else:
        resolution = fetch_resolved_event(event_slug)
        winning_temp = resolution.winning_temp if resolution else None
        if resolution is not None:
            classified = _classify_hold_result(
                token_id=selection.yes_token_id,
                bought_temp=bought_temp,
                resolution=resolution,
            )
            result = classified if classified is not None else "open"
        else:
            result = "open"

    win_temp_vs_bought = (
        compare_temp_buckets(bought_temp, winning_temp) if winning_temp else "unknown"
    )
    final_value = _pnl(result, shares, buy_price, sell_price)
    roi_pct = (
        round((final_value / cost_basis) * 100, 2)
        if final_value is not None and cost_basis > 0
        else None
    )
    sell_value_pct = (
        round((sell_price / buy_price) * 100, 2)
        if sell_price is not None and buy_price > 0
        else None
    )

    # Outcome value: for resolved hold, shares if win else 0; for sold use sell proceeds
    if result == "sold" and sell_price is not None:
        outcome_value = round(shares * sell_price, 4)
    elif result == "win":
        outcome_value = round(shares * 1.0, 4)
    elif result == "loss":
        outcome_value = 0.0
    else:
        outcome_value = None

    record = TradeRecord(
        date=str(event.get("event_date") or buy.bought_at.date().isoformat()),
        city=city,
        bought_temp=bought_temp,
        trade_window=trading_window_label(),
        bought_at=bought_at_iso,
        sold_at=sold_at_iso,
        redeemed_at=None,
        shares=shares,
        result=result,
        final_value_usd=final_value,
        winning_temp=winning_temp,
        win_temp_vs_bought=win_temp_vs_bought,
        price_drop_below_threshold_at=None,
        sold_but_would_have_won=False,
        buy_price=buy_price,
        sell_price=sell_price,
        cost_basis_usd=cost_basis,
        realized_pnl_usd=final_value,
        roi_pct=roi_pct,
        sell_value_pct=sell_value_pct,
        held_hours=held_hours,
        event_slug=event_slug,
        token_id=selection.yes_token_id,
        condition_id=str((selection.market or {}).get("conditionId") or ""),
        transaction_hash=None,
        bought_at_hk=bought_at_hk,
        bought_at_local=bought_at_local,
        sold_at_hk=sold_at_hk,
        share_count_target=share_count,
        shares_over_target=False,
        outcome_value_usd=outcome_value,
        spread=buy.spread,
        on_edge=None,
        competitive=parse_float(event.get("competitive")),
        open_interest=parse_float(event.get("openInterest")),
    )
    record.sold_but_would_have_won = recompute_sold_but_would_have_won(record)

    data = record.to_dict()
    data["sim_strategy"] = buy.strategy_name
    data["sample_time_local"] = buy.sample_time_local
    data["gamma_proxy"] = buy.gamma_proxy
    if sell is not None:
        data["sim_sell_tier"] = sell.tier_name
        data["sold_at_local"] = sold_at_local
    return data
