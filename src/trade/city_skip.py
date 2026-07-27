"""Skip markets whose city timezone is among the worst win-summary groups.

The denylist is NOT a hard-coded city list. Each trade-hourly / enrich run
recomputes bottom-N timezone groups from current trade_history.json, ranking
only "surviving" trades that match the live filter stack when fields exist.
A daily denylist JSON is written so the skip set refreshes at least once/day.
"""

from __future__ import annotations

import json
import logging
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import TRADE_HISTORY_FILE, TIMEZONE_SKIP_DENYLIST_FILE, settings
from src.analysis.models import (
    TradeRecord,
    _counts_toward_win_summary,
    _counts_toward_win_summary_denom,
)
from src.analysis.strategy_insights import timezone_group
from src.trade.strategies.base import MarketSelection

logger = logging.getLogger(__name__)


def _dict_to_trade_record(row: dict[str, Any]) -> Optional[TradeRecord]:
    allowed = {f.name for f in fields(TradeRecord)}
    payload = {k: v for k, v in row.items() if k in allowed}
    required = ("date", "city", "bought_temp", "trade_window", "bought_at", "shares", "result")
    if any(k not in payload for k in required):
        return None
    try:
        return TradeRecord(**payload)
    except TypeError:
        return None


def load_trade_records(path: Optional[Path] = None) -> list[TradeRecord]:
    history_path = path or TRADE_HISTORY_FILE
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read trade history for timezone skip: %s", history_path)
        return []
    rows = data.get("records") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    records: list[TradeRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rec = _dict_to_trade_record(row)
        if rec is not None:
            records.append(rec)
    return records


def surviving_records_for_skip(records: list[TradeRecord]) -> list[TradeRecord]:
    """Trades that would pass the live shipped stack when filter fields exist.

    Uses current YES_PRICE_MIN / YES_PRICE_MAX / SPREAD_MAX. Missing spread is
    allowed (same as live). Missing buy_price drops the row from ranking.
    """
    yes_min = float(getattr(settings, "yes_price_min", 0.0) or 0.0)
    yes_max = float(getattr(settings, "yes_price_max", 0.60) or 0.60)
    spread_max = float(getattr(settings, "spread_max", 0.15) or 0.15)
    kept: list[TradeRecord] = []
    for rec in records:
        buy = rec.buy_price
        if buy is None:
            continue
        if buy >= yes_max:
            continue
        if yes_min > 0 and buy < yes_min:
            continue
        if rec.spread is not None and float(rec.spread) >= spread_max:
            continue
        kept.append(rec)
    return kept


def timezone_win_summary_stats(
    records: list[TradeRecord],
) -> dict[str, dict[str, float | int]]:
    """Win summary numerator/denominator/% keyed by city timezone group."""
    grouped: dict[str, dict[str, float | int]] = {}
    for rec in records:
        key = timezone_group(rec.city or "")
        stats = grouped.setdefault(key, {"win_summary": 0, "win_summary_denom": 0})
        if _counts_toward_win_summary_denom(rec):
            stats["win_summary_denom"] = int(stats["win_summary_denom"]) + 1
        if _counts_toward_win_summary(rec):
            stats["win_summary"] = int(stats["win_summary"]) + 1

    for stats in grouped.values():
        denom = int(stats["win_summary_denom"])
        wins = int(stats["win_summary"])
        stats["win_plus_sold_win_pct"] = (
            round((wins / denom) * 100, 1) if denom else 0.0
        )
    return grouped


def lowest_win_summary_timezones(
    records: list[TradeRecord],
    *,
    bottom_n: Optional[int] = None,
) -> list[str]:
    """Return up to N timezone groups with the lowest win summary % (denom > 0)."""
    n = settings.city_skip_bottom_n if bottom_n is None else bottom_n
    if n <= 0:
        return []
    stats = timezone_win_summary_stats(records)
    ranked = [
        (tz, float(row["win_plus_sold_win_pct"]), int(row["win_summary_denom"]))
        for tz, row in stats.items()
        if int(row["win_summary_denom"]) > 0
    ]
    ranked.sort(key=lambda item: (item[1], item[2], item[0]))
    return [tz for tz, _pct, _denom in ranked[:n]]


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_timezone_skip_denylist(
    path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    denylist_path = path or TIMEZONE_SKIP_DENYLIST_FILE
    if not denylist_path.exists():
        return None
    try:
        data = json.loads(denylist_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def refresh_timezone_skip_denylist(
    *,
    history_path: Optional[Path] = None,
    denylist_path: Optional[Path] = None,
    bottom_n: Optional[int] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Recompute bottom-N from surviving trades and write daily denylist JSON."""
    out_path = denylist_path or TIMEZONE_SKIP_DENYLIST_FILE
    today = _today_utc()
    existing = load_timezone_skip_denylist(out_path)
    if (
        not force
        and existing
        and existing.get("date") == today
        and isinstance(existing.get("timezones"), list)
    ):
        return existing

    records = load_trade_records(history_path)
    surviving = surviving_records_for_skip(records)
    # Fall back to all records if filters wipe the sample (cold start / missing fields).
    rank_pool = surviving if len(surviving) >= 20 else records
    timezones = lowest_win_summary_timezones(rank_pool, bottom_n=bottom_n)
    stats = timezone_win_summary_stats(rank_pool)
    detail = {
        tz: {
            "win_plus_sold_win_pct": stats.get(tz, {}).get("win_plus_sold_win_pct"),
            "win_summary_denom": stats.get(tz, {}).get("win_summary_denom"),
        }
        for tz in timezones
    }
    payload = {
        "date": today,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "bottom_n": settings.city_skip_bottom_n if bottom_n is None else bottom_n,
        "rank_pool": "surviving" if rank_pool is surviving else "all",
        "rank_pool_n": len(rank_pool),
        "surviving_n": len(surviving),
        "all_n": len(records),
        "yes_price_min": float(getattr(settings, "yes_price_min", 0) or 0),
        "yes_price_max": float(getattr(settings, "yes_price_max", 0.6) or 0.6),
        "spread_max": float(getattr(settings, "spread_max", 0.15) or 0.15),
        "timezones": timezones,
        "detail": detail,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    logger.info(
        "Timezone skip denylist refreshed (%s): bottom %d from %s n=%d → %s",
        today,
        len(timezones),
        payload["rank_pool"],
        payload["rank_pool_n"],
        timezones,
    )
    return payload


def resolve_skip_timezones(
    *,
    history_path: Optional[Path] = None,
    bottom_n: Optional[int] = None,
    enabled: Optional[bool] = None,
    force_refresh: bool = False,
) -> list[str]:
    """Return city-timezone groups to skip; refreshes daily denylist as needed."""
    if enabled is None:
        enabled = settings.city_skip_enabled
    if not enabled:
        return []
    payload = refresh_timezone_skip_denylist(
        history_path=history_path,
        bottom_n=bottom_n,
        force=force_refresh,
    )
    timezones = [str(tz) for tz in (payload.get("timezones") or [])]
    if timezones:
        detail = payload.get("detail") or {}
        logger.info(
            "Timezone skip: bottom %d by city-timezone win summary%% (%s pool) → %s",
            len(timezones),
            payload.get("rank_pool"),
            ", ".join(
                f"{tz}={detail.get(tz, {}).get('win_plus_sold_win_pct')}%"
                f" ({detail.get(tz, {}).get('win_summary_denom')})"
                for tz in timezones
            ),
        )
    return timezones


def filter_events_by_skip_timezones(
    events: list[dict],
    skip_timezones: list[str] | set[str],
) -> tuple[list[dict], list[dict]]:
    """Drop events whose city timezone group is in the skip set."""
    skip = set(skip_timezones)
    if not skip:
        return list(events), []
    kept: list[dict] = []
    skipped: list[dict] = []
    for event in events:
        city = str(event.get("city") or "")
        tz_group = timezone_group(city)
        if tz_group in skip:
            logger.info(
                "event=%s city=%s timezone=%s in bottom win-summary timezones; skip",
                event.get("id"),
                city,
                tz_group,
            )
            step_log = event.get("_step_logger")
            if step_log:
                step_log.log_step(
                    "filter_timezone_win_summary",
                    skipped=True,
                    city=city,
                    timezone=tz_group,
                    reason="low_win_summary_timezone",
                )
            skipped.append(
                {
                    "event_id": event.get("id"),
                    "city": city,
                    "timezone": tz_group,
                    "reason": "low_win_summary_timezone",
                }
            )
            continue
        kept.append(event)
    return kept, skipped


def filter_selections_by_skip_timezones(
    selections: list[MarketSelection],
    skip_timezones: list[str] | set[str],
) -> tuple[list[MarketSelection], list[dict]]:
    """Drop selections whose city timezone group is in the skip set."""
    skip = set(skip_timezones)
    if not skip:
        return list(selections), []
    kept: list[MarketSelection] = []
    skipped: list[dict] = []
    for sel in selections:
        city = sel.city or ""
        tz_group = timezone_group(city)
        if tz_group in skip:
            logger.info(
                "event=%s city=%s timezone=%s in bottom win-summary timezones; skip",
                sel.event_id,
                city,
                tz_group,
            )
            step_log = sel.event.get("_step_logger") if sel.event else None
            if step_log:
                step_log.log_step(
                    "filter_timezone_win_summary",
                    skipped=True,
                    city=city,
                    timezone=tz_group,
                    reason="low_win_summary_timezone",
                )
            skipped.append(
                {
                    "event_id": sel.event_id,
                    "city": city,
                    "timezone": tz_group,
                    "market_id": sel.market_id,
                    "reason": "low_win_summary_timezone",
                }
            )
            continue
        kept.append(sel)
    return kept, skipped
