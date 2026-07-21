"""Resolve simulated positions to win/loss/sold/open and PnL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.analysis.models import TradeRecord, recompute_sold_but_would_have_won
from src.analysis.resolution import fetch_resolved_event, resolve_winning_temp
from src.simulation.buy_pass import SimulatedBuy
from src.simulation.sell_pass import SimulatedSell
from src.utils.hk_time import format_hk
from src.utils.market_parser import compare_temp_buckets, parse_float
from src.utils.time_window import trading_window_label


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
        winning_token = resolution.winning_token_id if resolution else None
        if winning_token:
            result = "win" if winning_token == selection.yes_token_id else "loss"
        elif resolution and resolution.closed and winning_temp:
            result = (
                "win"
                if compare_temp_buckets(bought_temp, winning_temp) == "same"
                else "loss"
            )
        elif resolution and not resolution.closed:
            result = "open"
        else:
            # Fallback: compare temps if we have winning_temp only
            if winning_temp:
                result = (
                    "win"
                    if compare_temp_buckets(bought_temp, winning_temp) == "same"
                    else "loss"
                )
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
