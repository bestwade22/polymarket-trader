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

# Bump when buy/sell/resolve/sample logic changes so prior sims are invalidated.
SIM_PROCESS_VERSION = "1"


def _dict_to_trade_record(row: dict[str, Any]) -> TradeRecord:
    """Build TradeRecord from sim dict (ignore unknown sim-only keys)."""
    allowed = {f.name for f in fields(TradeRecord)}
    payload = {k: v for k, v in row.items() if k in allowed}
    return TradeRecord(**payload)


def _event_key(event: dict[str, Any]) -> str:
    slug = str(event.get("slug") or "").strip()
    if slug:
        return slug
    return str(event.get("id") or "")


def _strategy_fingerprint(
    *,
    strategy: str,
    yes_price_max: float,
    spread_max: float,
    share_count: int,
) -> dict[str, Any]:
    """Params that define whether a prior sim can be reused (excludes date range)."""
    return {
        "process_version": SIM_PROCESS_VERSION,
        "strategy": strategy,
        "yes_price_max": yes_price_max,
        "spread_max": spread_max,
        "share_count": share_count,
        "trade_window": trading_window_label(),
        "sample_grid": ":05 / :35 city local",
        "fill_model": "100% at historical Yes %",
        "spread_rule": "SPREAD_MAX only when markets_yes_* spread exists",
    }


def _prior_fingerprint(data: dict[str, Any]) -> dict[str, Any]:
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    # Legacy sim files omit process_version; treat as "1".
    raw_version = data.get("process_version")
    process_version = str(raw_version) if raw_version is not None else "1"
    return {
        "process_version": process_version,
        "strategy": params.get("strategy"),
        "yes_price_max": params.get("yes_price_max"),
        "spread_max": params.get("spread_max"),
        "share_count": params.get("share_count"),
        "trade_window": params.get("trade_window"),
        "sample_grid": params.get("sample_grid"),
        "fill_model": params.get("fill_model"),
        "spread_rule": params.get("spread_rule"),
    }


def _parse_iso_date(value: Any) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _backfill_completed_dates(
    data: dict[str, Any],
    completed_dates: dict[str, Any],
) -> None:
    """Treat prior from→to as completed when fingerprint matched but dates unset."""
    if completed_dates:
        return
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    start = _parse_iso_date(params.get("from_date"))
    end = _parse_iso_date(params.get("to_date"))
    if start is None or end is None or start > end:
        return
    for day in date_range(start, end):
        completed_dates[day.isoformat()] = {
            "status": "backfilled",
            "from_prior_range": True,
        }


def _backfill_simulated_events_from_records(
    records: list[dict[str, Any]],
    simulated_events: dict[str, Any],
) -> None:
    for row in records:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("event_slug") or "").strip()
        if not slug or slug in simulated_events:
            continue
        simulated_events[slug] = {
            "status": "bought",
            "date": row.get("date"),
            "city": row.get("city"),
            "backfilled": True,
        }


def _load_reusable_prior(
    path: Path,
    fingerprint: dict[str, Any],
    *,
    force: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Return (records, simulated_events, completed_dates) when strategy/process match.

    force does not clear prior here; the runner invalidates only the requested dates.
    """
    if not path.exists():
        return [], {}, {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read prior sim file %s; re-simulating", path)
        return [], {}, {}
    if not isinstance(data, dict):
        return [], {}, {}

    prior_fp = _prior_fingerprint(data)
    if prior_fp != fingerprint:
        logger.info(
            "Sim strategy/process changed (prior process_version=%s strategy=%s); re-simulating",
            prior_fp.get("process_version"),
            prior_fp.get("strategy"),
        )
        return [], {}, {}

    records = data.get("records") if isinstance(data.get("records"), list) else []
    records = [r for r in records if isinstance(r, dict)]
    simulated_events = (
        dict(data.get("simulated_events"))
        if isinstance(data.get("simulated_events"), dict)
        else {}
    )
    completed_dates = (
        dict(data.get("completed_dates"))
        if isinstance(data.get("completed_dates"), dict)
        else {}
    )
    _backfill_completed_dates(data, completed_dates)
    _backfill_simulated_events_from_records(records, simulated_events)
    logger.info(
        "Reusing prior sim: %d records, %d events, %d completed dates%s",
        len(records),
        len(simulated_events),
        len(completed_dates),
        " (force will redo requested dates)" if force else "",
    )
    return records, simulated_events, completed_dates


def _drop_dates_from_prior(
    *,
    records: list[dict[str, Any]],
    simulated_events: dict[str, Any],
    completed_dates: dict[str, Any],
    days: list[date],
) -> list[dict[str, Any]]:
    """Remove prior state for dates being force-resimulated."""
    day_keys = {d.isoformat() for d in days}
    kept_records = [r for r in records if str(r.get("date") or "") not in day_keys]
    for key, meta in list(simulated_events.items()):
        if isinstance(meta, dict) and str(meta.get("date") or "") in day_keys:
            del simulated_events[key]
    for day_key in day_keys:
        completed_dates.pop(day_key, None)
    return kept_records


def run_simulate_trades(
    *,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    strategy_name: Optional[str] = None,
    yes_price_max: Optional[float] = None,
    spread_max: Optional[float] = None,
    share_count: Optional[int] = None,
    fetch_if_missing: bool = True,
    force: bool = False,
    output_path: Optional[Path] = None,
    clob: Optional[ClobPriceClient] = None,
) -> dict[str, Any]:
    """Replay strategy over weather events; write sim_trade_history.json.

    Same strategy + process_version reuses prior completed dates/events and only
    simulates new markets. Pass force=True (or bump SIM_PROCESS_VERSION) to redo.
    """
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

    fingerprint = _strategy_fingerprint(
        strategy=strategy,
        yes_price_max=ypm,
        spread_max=sm,
        share_count=shares,
    )
    out = output_path or SIM_TRADE_HISTORY_FILE
    records, simulated_events, completed_dates = _load_reusable_prior(
        out, fingerprint, force=force
    )
    days = list(date_range(start, end))
    if force:
        records = _drop_dates_from_prior(
            records=records,
            simulated_events=simulated_events,
            completed_dates=completed_dates,
            days=days,
        )

    store = PriceHistoryStore(clob=clob or ClobPriceClient())
    enrichment_index = load_enrichment_by_token()
    events_scanned = 0
    events_skipped_cached = 0
    dates_skipped = 0
    buys_new = 0
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    logger.info(
        "simulate-trades: %s → %s strategy=%s yes_price_max=%.2f spread_max=%.2f "
        "shares=%d process=%s force=%s",
        start.isoformat(),
        end.isoformat(),
        strategy,
        ypm,
        sm,
        shares,
        SIM_PROCESS_VERSION,
        force,
    )

    for day in days:
        day_key = day.isoformat()
        if day_key in completed_dates and not force:
            logger.info("date=%s already simulated; skip", day_key)
            dates_skipped += 1
            continue

        events = load_events_for_date(day, fetch_if_missing=fetch_if_missing)
        day_buys = 0
        day_new = 0
        for event in events:
            events_scanned += 1
            key = _event_key(event)
            if key and key in simulated_events and not force:
                events_skipped_cached += 1
                continue

            day_new += 1
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
                if key:
                    simulated_events[key] = {
                        "status": "no_buy",
                        "event_id": event.get("id"),
                        "date": day_key,
                        "city": event.get("city"),
                        "simulated_at": now_iso,
                    }
                continue

            buys_new += 1
            day_buys += 1

            hist_start, hist_end = history_window_ts(event)
            sell = try_sell_win(
                event,
                buy.selection.yes_token_id,
                store,
                bought_at=buy.bought_at,
                history_start_ts=hist_start,
                history_end_ts=hist_end,
            )
            store.mark_bought(buy.selection.yes_token_id)

            rec = build_sim_record(buy, sell, share_count=shares)
            records.append(rec)
            if key:
                simulated_events[key] = {
                    "status": "bought",
                    "event_id": event.get("id"),
                    "date": day_key,
                    "city": event.get("city"),
                    "simulated_at": now_iso,
                    "token_id": buy.selection.yes_token_id,
                }

        completed_dates[day_key] = {
            "status": "complete",
            "events": len(events),
            "buys": day_buys,
            "newly_simulated": day_new,
            "simulated_at": now_iso,
        }

    trade_records = [_dict_to_trade_record(r) for r in records]
    summary = summarize_records(trade_records)
    insights = compute_insights(trade_records)

    covered = sorted(completed_dates.keys())
    stored_from = start.isoformat()
    stored_to = end.isoformat()
    if covered:
        stored_from = min(stored_from, covered[0])
        stored_to = max(stored_to, covered[-1])

    payload = {
        "synced_at": now_iso,
        "sim": True,
        "process_version": SIM_PROCESS_VERSION,
        "params": {
            "from_date": stored_from,
            "to_date": stored_to,
            **{k: v for k, v in fingerprint.items() if k != "process_version"},
        },
        "events_scanned": events_scanned,
        "events_skipped_cached": events_skipped_cached,
        "dates_skipped": dates_skipped,
        "buy_count": sum(1 for e in simulated_events.values() if e.get("status") == "bought"),
        "buy_count_new": buys_new,
        "completed_dates": completed_dates,
        "simulated_events": simulated_events,
        "records": records,
        "summary": summary.to_dict(),
        "insights": insights,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    logger.info(
        "Wrote %d simulated trades to %s (scanned %d new events, skipped %d cached, "
        "%d dates cached, %d new buys)",
        len(records),
        out,
        events_scanned,
        events_skipped_cached,
        dates_skipped,
        buys_new,
    )
    return {
        "record_count": len(records),
        "events_scanned": events_scanned,
        "events_skipped_cached": events_skipped_cached,
        "dates_skipped": dates_skipped,
        "buy_count_new": buys_new,
        "output": str(out),
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "process_version": SIM_PROCESS_VERSION,
        "force": force,
    }
