"""Sync wallet trade history to analysis JSON ledger."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import (
    SYNC_STATE_FILE,
    TRADE_HISTORY_FILE,
    ensure_dirs,
    settings,
)
from src.analysis.history_builder import build_records_from_activity
from src.analysis.models import (
    TradeRecord,
    compute_outcome_value,
    recompute_sold_but_would_have_won,
    summarize_records,
)
from src.analysis.spread_lookup import load_selection_spreads_by_token, lookup_spread_for_buy
from src.analysis.market_metrics_lookup import (
    load_competitive_snapshot_by_token,
    load_event_metrics_index,
    load_open_interest_snapshot_by_slug,
    lookup_competitive_for_buy,
    lookup_open_interest_for_buy,
)
from src.analysis.edge_lookup import (
    compute_on_edge_from_history,
    load_event_markets_by_slug,
    load_on_edge_snapshot_by_token,
    lookup_on_edge_from_snapshots,
)
from src.analysis.resolution import fetch_resolved_event
from src.analysis.strategy_insights import compute_insights
from src.api.clob_client import ClobPriceClient
from src.api.data_client import fetch_all_closed_positions, fetch_all_user_activity
from src.utils.market_parser import compare_temp_buckets

logger = logging.getLogger(__name__)


def load_sync_state() -> dict[str, Any]:
    if not SYNC_STATE_FILE.exists():
        return {}
    try:
        return json.loads(SYNC_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_sync_state(state: dict[str, Any]) -> None:
    ensure_dirs()
    SYNC_STATE_FILE.write_text(json.dumps(state, indent=2))


def _record_from_dict(row: dict) -> TradeRecord:
    fields = TradeRecord.__dataclass_fields__
    data = {k: row.get(k) for k in fields}
    # Legacy field rename
    if data.get("outcome_value_usd") is None and row.get("would_win_value_usd") is not None:
        data["outcome_value_usd"] = row.get("would_win_value_usd")
    rec = TradeRecord(**data)
    if rec.outcome_value_usd is None:
        rec.outcome_value_usd = compute_outcome_value(rec)
    return rec


def load_existing_records() -> dict[str, TradeRecord]:
    if not TRADE_HISTORY_FILE.exists():
        return {}
    try:
        data = json.loads(TRADE_HISTORY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    records = data.get("records", [])
    result: dict[str, TradeRecord] = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        token_id = str(row.get("token_id") or "")
        if not token_id:
            continue
        result[token_id] = _record_from_dict(row)
    return result


def _backfill_outcome_values(records: list[TradeRecord]) -> list[TradeRecord]:
    for rec in records:
        if rec.outcome_value_usd is None:
            rec.outcome_value_usd = compute_outcome_value(rec)
    return records


def _backfill_resolutions(records: list[TradeRecord]) -> list[TradeRecord]:
    """Fill missing winning_temp / win_temp_vs_bought from resolution cache or Gamma."""
    missing = [
        rec
        for rec in records
        if rec.win_temp_vs_bought == "unknown" or not rec.winning_temp
    ]
    if not missing:
        return records

    filled = 0
    for rec in missing:
        resolution = fetch_resolved_event(rec.event_slug)
        if not resolution or not resolution.winning_temp:
            continue
        rec.winning_temp = resolution.winning_temp
        rec.win_temp_vs_bought = compare_temp_buckets(
            rec.bought_temp, rec.winning_temp
        )
        if rec.result == "sold":
            rec.sold_but_would_have_won = recompute_sold_but_would_have_won(rec)
        filled += 1

    if filled:
        logger.info(
            "Backfilled resolution on %d/%d trade records",
            filled,
            len(missing),
        )
    return records


def _backfill_sold_outcomes(records: list[TradeRecord]) -> list[TradeRecord]:
    """Align sold_but_would_have_won with P&L + win_temp_vs_bought rules."""
    fixed = 0
    for rec in records:
        if rec.result != "sold":
            continue
        want = recompute_sold_but_would_have_won(rec)
        if rec.sold_but_would_have_won != want:
            rec.sold_but_would_have_won = want
            fixed += 1
    if fixed:
        logger.info("Corrected sold_but_would_have_won on %d sold records", fixed)
    return records


def _backfill_spreads(records: list[TradeRecord]) -> list[TradeRecord]:
    """Fill missing spread from local selection snapshots (ask − bid at order time)."""
    missing = [rec for rec in records if rec.spread is None]
    if not missing:
        return records
    index = load_selection_spreads_by_token()
    filled = 0
    for rec in missing:
        spread = lookup_spread_for_buy(rec.token_id, rec.bought_at, index=index)
        if spread is not None:
            rec.spread = spread
            filled += 1
    if filled:
        logger.info("Backfilled spread on %d/%d trade records", filled, len(missing))
    return records


def _backfill_market_metrics(records: list[TradeRecord]) -> list[TradeRecord]:
    """Fill missing competitive / open_interest from snapshots and event cache files."""
    missing_competitive = [rec for rec in records if rec.competitive is None]
    missing_oi = [rec for rec in records if rec.open_interest is None]
    if not missing_competitive and not missing_oi:
        return records

    comp_index = load_competitive_snapshot_by_token()
    oi_index = load_open_interest_snapshot_by_slug()
    event_index = load_event_metrics_index()
    filled_comp = 0
    filled_oi = 0

    for rec in records:
        if rec.competitive is None:
            competitive = lookup_competitive_for_buy(
                rec.token_id,
                rec.bought_at,
                event_slug=rec.event_slug,
                snapshot_index=comp_index,
                event_index=event_index,
            )
            if competitive is not None:
                rec.competitive = competitive
                filled_comp += 1
        if rec.open_interest is None:
            open_interest = lookup_open_interest_for_buy(
                rec.event_slug,
                rec.bought_at,
                token_id=rec.token_id,
                snapshot_index=oi_index,
                event_index=event_index,
            )
            if open_interest is not None:
                rec.open_interest = open_interest
                filled_oi += 1

    if filled_comp:
        logger.info(
            "Backfilled competitive on %d/%d trade records",
            filled_comp,
            len(missing_competitive),
        )
    if filled_oi:
        logger.info(
            "Backfilled open_interest on %d/%d trade records",
            filled_oi,
            len(missing_oi),
        )
    return records


def _backfill_on_edge(
    records: list[TradeRecord],
    *,
    use_clob: bool = True,
) -> list[TradeRecord]:
    """Fill missing on_edge from selection snapshots, else CLOB prices near buy time."""
    missing = [rec for rec in records if rec.on_edge is None]
    if not missing:
        return records

    sel_index = load_on_edge_snapshot_by_token()
    filled = 0
    still_missing: list[TradeRecord] = []
    for rec in missing:
        on_edge = lookup_on_edge_from_snapshots(
            rec.token_id, rec.bought_at, index=sel_index
        )
        if on_edge is not None:
            rec.on_edge = on_edge
            filled += 1
        else:
            still_missing.append(rec)

    if still_missing and use_clob:
        markets_by_slug = load_event_markets_by_slug()
        clob = ClobPriceClient()
        price_cache: dict[tuple[str, int], Optional[float]] = {}
        for i, rec in enumerate(still_missing, start=1):
            on_edge = compute_on_edge_from_history(
                event_slug=rec.event_slug,
                bought_temp=rec.bought_temp,
                bought_at=rec.bought_at,
                markets_by_slug=markets_by_slug,
                clob=clob,
                price_cache=price_cache,
            )
            if on_edge is not None:
                rec.on_edge = on_edge
                filled += 1
            if i % 25 == 0:
                logger.info("on_edge CLOB backfill progress %d/%d", i, len(still_missing))

    if filled:
        logger.info("Backfilled on_edge on %d/%d trade records", filled, len(missing))
    return records


def _merge_records(
    existing: dict[str, TradeRecord],
    fresh: list[TradeRecord],
) -> list[TradeRecord]:
    merged = dict(existing)
    for rec in fresh:
        prior = merged.get(rec.token_id)
        if prior is not None:
            if rec.spread is None and prior.spread is not None:
                rec.spread = prior.spread
            if rec.on_edge is None and prior.on_edge is not None:
                rec.on_edge = prior.on_edge
            if rec.competitive is None and prior.competitive is not None:
                rec.competitive = prior.competitive
            if rec.open_interest is None and prior.open_interest is not None:
                rec.open_interest = prior.open_interest
        merged[rec.token_id] = rec
    return sorted(merged.values(), key=lambda r: r.bought_at, reverse=True)


def _max_activity_ts(activity: list[dict[str, Any]]) -> Optional[int]:
    if not activity:
        return None
    return max(int(row.get("timestamp") or 0) for row in activity)


def run_sync_trade_history(
    *,
    init_days: Optional[int] = None,
    wallet_address: Optional[str] = None,
    fetch_price_drop: bool = True,
) -> dict[str, Any]:
    """Fetch wallet activity and write trade_history.json."""
    wallet = wallet_address or settings.deposit_wallet_address
    if not wallet:
        return {"status": "error", "reason": "missing_wallet"}

    ensure_dirs()
    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    state = load_sync_state()

    if init_days is not None:
        start_ts = int((now - timedelta(days=init_days)).timestamp())
    elif state.get("last_activity_ts"):
        start_ts = int(state["last_activity_ts"])
    else:
        start_ts = int((now - timedelta(days=7)).timestamp())

    logger.info(
        "Syncing trade history for %s from ts=%s (init_days=%s)",
        wallet[:10],
        start_ts,
        init_days,
    )

    activity = fetch_all_user_activity(
        wallet,
        types=["TRADE", "REDEEM"],
        start=start_ts,
        end=now_ts,
        sort_direction="ASC",
    )
    closed_positions = fetch_all_closed_positions(wallet)

    fresh_records = build_records_from_activity(
        activity,
        closed_positions,
        wallet=wallet,
        fetch_price_drop=fetch_price_drop,
    )

    existing = load_existing_records() if init_days is None else {}
    # Re-evaluate open positions from prior sync
    if init_days is None:
        open_tokens = {tid for tid, rec in existing.items() if rec.result == "open"}
        if open_tokens:
            # Widen fetch to cover all open rows
            extra_start = start_ts
            for tid in open_tokens:
                rec = existing[tid]
                if rec.bought_at:
                    try:
                        bought_ts = int(
                            datetime.fromisoformat(rec.bought_at).timestamp()
                        )
                        extra_start = min(extra_start, bought_ts)
                    except ValueError:
                        pass
            if extra_start < start_ts:
                activity = fetch_all_user_activity(
                    wallet,
                    types=["TRADE", "REDEEM"],
                    start=extra_start,
                    end=now_ts,
                    sort_direction="ASC",
                )
                fresh_records = build_records_from_activity(
                    activity,
                    closed_positions,
                    wallet=wallet,
                    fetch_price_drop=fetch_price_drop,
                )

    all_records = _backfill_market_metrics(
        _backfill_on_edge(
            _backfill_spreads(
                _backfill_sold_outcomes(
                    _backfill_resolutions(
                        _backfill_outcome_values(_merge_records(existing, fresh_records))
                    )
                )
            )
        )
    )
    summary = summarize_records(all_records)
    insights = compute_insights(all_records)

    payload = {
        "synced_at": now.isoformat(),
        "wallet": wallet,
        "records": [r.to_dict() for r in all_records],
        "summary": summary.to_dict(),
        "insights": insights,
    }
    TRADE_HISTORY_FILE.write_text(json.dumps(payload, indent=2))

    last_ts = _max_activity_ts(activity) or state.get("last_activity_ts") or now_ts
    save_sync_state(
        {
            "last_activity_ts": last_ts,
            "last_sync_at": now.isoformat(),
            "record_count": len(all_records),
        }
    )

    logger.info(
        "Wrote %d trade records to %s",
        len(all_records),
        TRADE_HISTORY_FILE,
    )
    return {
        "status": "ok",
        "record_count": len(all_records),
        "activity_rows": len(activity),
        "path": str(TRADE_HISTORY_FILE),
        "summary": summary.to_dict(),
    }
