"""Analyze skipped / not-bought selection rows and would-have-won rates."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from config.settings import RESOLUTIONS_CACHE_FILE, SELECTIONS_DIR
from src.analysis.resolution import fetch_resolved_event
from src.utils.market_parser import compare_temp_buckets, extract_temp_label

logger = logging.getLogger(__name__)


def _load_resolution_map(cache_path: Optional[Path] = None) -> dict[str, str]:
    """event_slug -> winning_temp from cache (no network)."""
    path = cache_path or RESOLUTIONS_CACHE_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    events = payload.get("events") or {}
    out: dict[str, str] = {}
    for slug, row in events.items():
        if isinstance(row, dict) and row.get("winning_temp"):
            out[str(slug)] = str(row["winning_temp"])
    return out


def _bought_title(row: dict[str, Any]) -> Optional[str]:
    return (
        row.get("group_item_title")
        or row.get("groupItemTitle")
        or row.get("held_group_item_title")
        or None
    )


def _event_slug_from_row(row: dict[str, Any]) -> Optional[str]:
    slug = row.get("event_slug")
    if slug:
        return str(slug)
    # Reconstruct is hard; leave empty
    return None


def _would_have_won(
    row: dict[str, Any],
    resolutions: dict[str, str],
) -> Optional[bool]:
    title = _bought_title(row)
    if not title:
        return None
    slug = _event_slug_from_row(row)
    winning = resolutions.get(slug or "") if slug else None
    if not winning:
        return None
    vs = compare_temp_buckets(title, winning)
    if vs == "same":
        return True
    if vs in ("higher", "lower"):
        return False
    return None


def load_skipped_rows(
    selections_dir: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Flatten all skipped_bought entries from markets_yes_*.json."""
    root = selections_dir or SELECTIONS_DIR
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(root.glob("markets_yes_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        run_at = payload.get("run_at")
        strategy = payload.get("strategy")
        for row in payload.get("skipped_bought") or []:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item["_run_at"] = run_at
            item["_strategy"] = strategy
            item["_source_file"] = path.name
            rows.append(item)
    return rows


def compute_skipped_analysis(
    *,
    selections_dir: Optional[Path] = None,
    resolutions: Optional[dict[str, str]] = None,
    fetch_missing_resolutions: bool = False,
) -> dict[str, Any]:
    """Aggregate skip reasons and would-have-won when a temp bucket was known."""
    rows = load_skipped_rows(selections_dir)
    res_map = dict(resolutions or _load_resolution_map())

    if fetch_missing_resolutions:
        for row in rows:
            slug = _event_slug_from_row(row)
            if not slug or slug in res_map:
                continue
            resolution = fetch_resolved_event(slug)
            if resolution and resolution.winning_temp:
                res_map[slug] = resolution.winning_temp

    by_reason: dict[str, dict[str, Any]] = {}
    samples: list[dict[str, Any]] = []

    for row in rows:
        reason = str(row.get("reason") or "unknown")
        stats = by_reason.setdefault(
            reason,
            {
                "reason": reason,
                "count": 0,
                "with_temp": 0,
                "resolved": 0,
                "would_have_won": 0,
                "would_have_lost": 0,
                "unknown_outcome": 0,
            },
        )
        stats["count"] += 1
        title = _bought_title(row)
        if title:
            stats["with_temp"] += 1
        whw = _would_have_won(row, res_map)
        if whw is True:
            stats["resolved"] += 1
            stats["would_have_won"] += 1
        elif whw is False:
            stats["resolved"] += 1
            stats["would_have_lost"] += 1
        elif title:
            stats["unknown_outcome"] += 1

        if len(samples) < 40 and title:
            samples.append(
                {
                    "run_at": row.get("_run_at"),
                    "city": row.get("city"),
                    "reason": reason,
                    "temp": extract_temp_label(title),
                    "event_slug": _event_slug_from_row(row),
                    "would_have_won": whw,
                    "timezone": row.get("timezone"),
                }
            )

    reason_rows = []
    for reason, stats in by_reason.items():
        resolved = int(stats["resolved"])
        won = int(stats["would_have_won"])
        whw_pct = round(100.0 * won / resolved, 1) if resolved else None
        # Costly filter: high would-have-won among resolved skips
        reason_rows.append(
            {
                **stats,
                "would_have_won_pct": whw_pct,
                "filter_costly": bool(whw_pct is not None and whw_pct >= 50 and resolved >= 5),
                "filter_helpful": bool(
                    whw_pct is not None and whw_pct < 40 and resolved >= 5
                ),
            }
        )
    reason_rows.sort(key=lambda r: (-int(r["count"]), r["reason"]))

    total = len(rows)
    resolved_total = sum(int(r["resolved"]) for r in reason_rows)
    won_total = sum(int(r["would_have_won"]) for r in reason_rows)
    return {
        "total_skips": total,
        "resolved_skips": resolved_total,
        "would_have_won_total": won_total,
        "would_have_won_pct": round(100.0 * won_total / resolved_total, 1)
        if resolved_total
        else None,
        "by_reason": reason_rows,
        "samples": samples,
    }
