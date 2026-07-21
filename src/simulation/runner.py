"""Orchestrate historical strategy simulation and write sim_trade_history.json."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import (
    SIM_TRADE_HISTORY_FILE,
    ensure_dirs,
    settings,
)
from dataclasses import fields

from src.analysis.models import TradeRecord, summarize_records
from src.analysis.strategy_insights import compute_insights
from src.api.clob_client import ClobPriceClient
from src.simulation.buy_pass import history_window_ts, try_buy_event
from src.simulation.event_loader import date_range, default_sim_date_range, load_events_for_date
from src.simulation.price_at_time import PriceHistoryStore
from src.simulation.resolve import build_sim_record
from src.simulation.sell_pass import try_sell_win
from src.simulation.snapshot_enrichment import load_enrichment_by_token
from src.utils.time_window import trading_window_label

logger = logging.getLogger(__name__)


def _dict_to_trade_record(row: dict[str, Any]) -> TradeRecord:
    """Build TradeRecord from sim dict (ignore unknown sim-only keys)."""
    allowed = {f.name for f in fields(TradeRecord)}
    payload = {k: v for k, v in row.items() if k in allowed}
    return TradeRecord(**payload)


def run_simulate_trades(
    *,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    strategy_name: Optional[str] = None,
    yes_price_max: Optional[float] = None,
    spread_max: Optional[float] = None,
    share_count: Optional[int] = None,
    fetch_if_missing: bool = True,
    output_path: Optional[Path] = None,
    clob: Optional[ClobPriceClient] = None,
) -> dict[str, Any]:
    """Replay strategy over weather events; write sim_trade_history.json."""
    ensure_dirs()
    start, end = default_sim_date_range()
    if from_date is not None:
        start = from_date
    if to_date is not None:
        end = to_date

    strategy = (strategy_name or settings.strategy).lower()
    shares = share_count if share_count is not None else settings.share_count
    ypm = yes_price_max if yes_price_max is not None else settings.yes_price_max
    sm = spread_max if spread_max is not None else settings.spread_max

    store = PriceHistoryStore(clob=clob or ClobPriceClient())
    enrichment_index = load_enrichment_by_token()
    records: list[dict[str, Any]] = []
    events_scanned = 0
    buys = 0

    logger.info(
        "simulate-trades: %s → %s strategy=%s yes_price_max=%.2f spread_max=%.2f shares=%d",
        start.isoformat(),
        end.isoformat(),
        strategy,
        ypm,
        sm,
        shares,
    )

    for day in date_range(start, end):
        events = load_events_for_date(day, fetch_if_missing=fetch_if_missing)
        for event in events:
            events_scanned += 1
            buy = try_buy_event(
                event,
                store,
                strategy_name=strategy,
                yes_price_max=ypm,
                spread_max=sm,
                share_count=shares,
                enrichment_index=enrichment_index,
            )
            if buy is None:
                continue
            buys += 1

            hist_start, hist_end = history_window_ts(event)
            sell = try_sell_win(
                event,
                buy.selection.yes_token_id,
                store,
                bought_at=buy.bought_at,
                history_start_ts=hist_start,
                history_end_ts=hist_end,
            )
            # Ensure bought token history is on disk (also after sell fetch)
            store.mark_bought(buy.selection.yes_token_id)

            records.append(build_sim_record(buy, sell, share_count=shares))

    trade_records = [_dict_to_trade_record(r) for r in records]
    summary = summarize_records(trade_records)
    insights = compute_insights(trade_records)
    now = datetime.now(timezone.utc)

    payload = {
        "synced_at": now.isoformat(),
        "sim": True,
        "params": {
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "strategy": strategy,
            "yes_price_max": ypm,
            "spread_max": sm,
            "share_count": shares,
            "trade_window": trading_window_label(),
            "sample_grid": ":05 / :35 city local",
            "fill_model": "100% at historical Yes %",
            "spread_rule": "SPREAD_MAX only when markets_yes_* spread exists",
        },
        "events_scanned": events_scanned,
        "buy_count": buys,
        "records": records,
        "summary": summary.to_dict(),
        "insights": insights,
    }

    out = output_path or SIM_TRADE_HISTORY_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    logger.info(
        "Wrote %d simulated trades to %s (scanned %d events)",
        len(records),
        out,
        events_scanned,
    )
    return {
        "record_count": len(records),
        "events_scanned": events_scanned,
        "output": str(out),
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
    }
