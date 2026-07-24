"""Skip markets whose city timezone is among the worst win-summary groups."""

from __future__ import annotations

import json
import logging
from dataclasses import fields
from pathlib import Path
from typing import Any, Optional

from config.settings import TRADE_HISTORY_FILE, settings
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


def resolve_skip_timezones(
    *,
    history_path: Optional[Path] = None,
    bottom_n: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> list[str]:
    """Load trade history and return city-timezone groups to skip for ordering."""
    if enabled is None:
        enabled = settings.city_skip_enabled
    if not enabled:
        return []
    records = load_trade_records(history_path)
    if not records:
        logger.info("Timezone skip: no trade history; not skipping any timezones")
        return []
    timezones = lowest_win_summary_timezones(records, bottom_n=bottom_n)
    if timezones:
        stats = timezone_win_summary_stats(records)
        detail = ", ".join(
            f"{tz}={stats[tz]['win_plus_sold_win_pct']}% ({stats[tz]['win_summary_denom']})"
            for tz in timezones
        )
        logger.info(
            "Timezone skip: bottom %d by city-timezone win summary%% → %s",
            len(timezones),
            detail,
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

