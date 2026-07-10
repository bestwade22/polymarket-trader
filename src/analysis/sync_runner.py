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
from src.analysis.models import TradeRecord, compute_outcome_value, summarize_records
from src.analysis.strategy_insights import compute_insights
from src.api.data_client import fetch_all_closed_positions, fetch_all_user_activity

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


def _merge_records(
    existing: dict[str, TradeRecord],
    fresh: list[TradeRecord],
) -> list[TradeRecord]:
    merged = dict(existing)
    for rec in fresh:
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

    all_records = _backfill_outcome_values(_merge_records(existing, fresh_records))
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
