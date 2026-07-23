"""Skip cities with the worst historical win-summary % before ordering."""

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
        logger.warning("Could not read trade history for city skip: %s", history_path)
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


def city_win_summary_stats(
    records: list[TradeRecord],
) -> dict[str, dict[str, float | int]]:
    """Win summary numerator/denominator/% keyed by city."""
    grouped: dict[str, dict[str, float | int]] = {}
    for rec in records:
        city = rec.city or "Unknown"
        stats = grouped.setdefault(city, {"win_summary": 0, "win_summary_denom": 0})
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


def lowest_win_summary_cities(
    records: list[TradeRecord],
    *,
    bottom_n: Optional[int] = None,
) -> list[str]:
    """Return up to N cities with the lowest win summary % (denom > 0)."""
    n = settings.city_skip_bottom_n if bottom_n is None else bottom_n
    if n <= 0:
        return []
    stats = city_win_summary_stats(records)
    ranked = [
        (city, float(row["win_plus_sold_win_pct"]), int(row["win_summary_denom"]))
        for city, row in stats.items()
        if int(row["win_summary_denom"]) > 0
    ]
    ranked.sort(key=lambda item: (item[1], item[2], item[0]))
    return [city for city, _pct, _denom in ranked[:n]]


def resolve_skip_cities(
    *,
    history_path: Optional[Path] = None,
    bottom_n: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> list[str]:
    """Load trade history and return cities to skip for ordering."""
    if enabled is None:
        enabled = settings.city_skip_enabled
    if not enabled:
        return []
    records = load_trade_records(history_path)
    if not records:
        logger.info("City skip: no trade history; not skipping any cities")
        return []
    cities = lowest_win_summary_cities(records, bottom_n=bottom_n)
    if cities:
        stats = city_win_summary_stats(records)
        detail = ", ".join(
            f"{c}={stats[c]['win_plus_sold_win_pct']}% ({stats[c]['win_summary_denom']})"
            for c in cities
        )
        logger.info(
            "City skip: bottom %d by win summary%% → %s",
            len(cities),
            detail,
        )
    return cities


def filter_events_by_skip_cities(
    events: list[dict],
    skip_cities: list[str] | set[str],
) -> tuple[list[dict], list[dict]]:
    """Drop events whose city is in the skip set."""
    skip = {c.lower() for c in skip_cities}
    if not skip:
        return list(events), []
    kept: list[dict] = []
    skipped: list[dict] = []
    for event in events:
        city = str(event.get("city") or "")
        if city.lower() in skip:
            logger.info(
                "event=%s city=%s in bottom win-summary cities; skip",
                event.get("id"),
                city,
            )
            step_log = event.get("_step_logger")
            if step_log:
                step_log.log_step(
                    "filter_city_win_summary",
                    skipped=True,
                    city=city,
                    reason="low_win_summary_city",
                )
            skipped.append(
                {
                    "event_id": event.get("id"),
                    "city": city,
                    "reason": "low_win_summary_city",
                }
            )
            continue
        kept.append(event)
    return kept, skipped


def filter_selections_by_skip_cities(
    selections: list[MarketSelection],
    skip_cities: list[str] | set[str],
) -> tuple[list[MarketSelection], list[dict]]:
    """Drop selections whose city is in the skip set."""
    skip = {c.lower() for c in skip_cities}
    if not skip:
        return list(selections), []
    kept: list[MarketSelection] = []
    skipped: list[dict] = []
    for sel in selections:
        city = sel.city or ""
        if city.lower() in skip:
            logger.info(
                "event=%s city=%s in bottom win-summary cities; skip",
                sel.event_id,
                city,
            )
            step_log = sel.event.get("_step_logger") if sel.event else None
            if step_log:
                step_log.log_step(
                    "filter_city_win_summary",
                    skipped=True,
                    city=city,
                    reason="low_win_summary_city",
                )
            skipped.append(
                {
                    "event_id": sel.event_id,
                    "city": city,
                    "market_id": sel.market_id,
                    "reason": "low_win_summary_city",
                }
            )
            continue
        kept.append(sel)
    return kept, skipped
