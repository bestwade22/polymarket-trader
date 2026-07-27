"""Analyze skipped / not-bought selection rows and would-have-won rates."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config.settings import DATA_DIR, RESOLUTIONS_CACHE_FILE, SELECTIONS_DIR, TRADE_HISTORY_FILE
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


def _load_trade_history_resolutions() -> dict[str, str]:
    """event_slug -> winning_temp from settled trade_history rows."""
    if not TRADE_HISTORY_FILE.exists():
        return {}
    try:
        data = json.loads(TRADE_HISTORY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, str] = {}
    for row in data.get("records") or []:
        if not isinstance(row, dict):
            continue
        slug = row.get("event_slug")
        winning = row.get("winning_temp")
        if slug and winning:
            out[str(slug)] = str(winning)
    return out


def _load_event_id_slug_index(data_dir: Optional[Path] = None) -> dict[str, str]:
    """event_id / market_id -> event slug from dated events_*.json."""
    root = data_dir or DATA_DIR
    index: dict[str, str] = {}
    for path in sorted(root.glob("events_*.json")):
        try:
            events = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            slug = event.get("slug") or event.get("ticker")
            if not slug:
                continue
            slug_s = str(slug)
            eid = event.get("id")
            if eid is not None:
                index[str(eid)] = slug_s
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                mid = market.get("id")
                if mid is not None:
                    index[f"market:{mid}"] = slug_s
    return index


def _slugify_city(city: str) -> str:
    text = (city or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _date_from_selection_meta(row: dict[str, Any]) -> Optional[str]:
    source = str(row.get("_source_file") or "")
    m = re.search(r"markets_yes_(\d{4}-\d{2}-\d{2})_", source)
    if m:
        return m.group(1)
    run_at = row.get("_run_at") or ""
    try:
        return datetime.fromisoformat(str(run_at).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _reconstruct_slug(city: str, event_date: str) -> Optional[str]:
    if not city or not event_date:
        return None
    try:
        dt = datetime.strptime(event_date, "%Y-%m-%d")
    except ValueError:
        return None
    city_slug = _slugify_city(city)
    if not city_slug:
        return None
    # Polymarket weather slugs: highest-temperature-in-{city}-on-{month}-{day}-{year}
    month = dt.strftime("%B").lower()
    return f"highest-temperature-in-{city_slug}-on-{month}-{dt.day}-{dt.year}"


def _bought_title(row: dict[str, Any]) -> Optional[str]:
    return (
        row.get("group_item_title")
        or row.get("groupItemTitle")
        or row.get("held_group_item_title")
        or row.get("gamma_title")
        or None
    )


def _event_slug_from_row(
    row: dict[str, Any],
    *,
    id_index: Optional[dict[str, str]] = None,
) -> Optional[str]:
    slug = row.get("event_slug")
    if slug:
        return str(slug)
    index = id_index or {}
    eid = row.get("event_id")
    if eid is not None and str(eid) in index:
        return index[str(eid)]
    mid = row.get("market_id")
    if mid is not None and f"market:{mid}" in index:
        return index[f"market:{mid}"]
    city = str(row.get("city") or "")
    event_date = _date_from_selection_meta(row)
    if city and event_date:
        return _reconstruct_slug(city, event_date)
    return None


def _would_have_won(
    row: dict[str, Any],
    resolutions: dict[str, str],
    *,
    id_index: Optional[dict[str, str]] = None,
) -> Optional[bool]:
    title = _bought_title(row)
    if not title:
        return None
    slug = _event_slug_from_row(row, id_index=id_index)
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
    fetch_missing_resolutions: bool = True,
    max_fetches: int = 200,
) -> dict[str, Any]:
    """Aggregate skip reasons and would-have-won when a temp bucket was known."""
    rows = load_skipped_rows(selections_dir)
    res_map = dict(resolutions or _load_resolution_map())
    # Trade history often has resolutions before the slim cache is complete.
    for slug, winning in _load_trade_history_resolutions().items():
        res_map.setdefault(slug, winning)
    id_index = _load_event_id_slug_index()

    # Attach resolved slugs onto rows for samples / fetch loop
    pending_slugs: list[str] = []
    seen_pending: set[str] = set()
    for row in rows:
        slug = _event_slug_from_row(row, id_index=id_index)
        if slug:
            row["_resolved_event_slug"] = slug
        title = _bought_title(row)
        if slug and title and slug not in res_map and slug not in seen_pending:
            seen_pending.add(slug)
            pending_slugs.append(slug)

    fetched = 0
    if fetch_missing_resolutions:
        for slug in pending_slugs[: max(0, max_fetches)]:
            resolution = fetch_resolved_event(slug)
            if resolution and resolution.winning_temp:
                res_map[slug] = resolution.winning_temp
                fetched += 1
        if fetched:
            logger.info(
                "Skipped analysis fetched %d/%d missing resolutions",
                fetched,
                min(len(pending_slugs), max_fetches),
            )

    by_reason: dict[str, dict[str, Any]] = {}
    samples: list[dict[str, Any]] = []
    slug_hits = 0

    for row in rows:
        reason = str(row.get("reason") or "unknown")
        stats = by_reason.setdefault(
            reason,
            {
                "reason": reason,
                "count": 0,
                "with_temp": 0,
                "with_slug": 0,
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
        slug = row.get("_resolved_event_slug") or _event_slug_from_row(row, id_index=id_index)
        if slug:
            stats["with_slug"] += 1
            slug_hits += 1
        whw = _would_have_won(row, res_map, id_index=id_index)
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
                    "event_slug": slug,
                    "would_have_won": whw,
                    "timezone": row.get("timezone"),
                }
            )

    reason_rows = []
    for reason, stats in by_reason.items():
        resolved = int(stats["resolved"])
        won = int(stats["would_have_won"])
        whw_pct = round(100.0 * won / resolved, 1) if resolved else None
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
        "with_slug": slug_hits,
        "resolved_skips": resolved_total,
        "would_have_won_total": won_total,
        "would_have_won_pct": round(100.0 * won_total / resolved_total, 1)
        if resolved_total
        else None,
        "resolutions_fetched": fetched,
        "by_reason": reason_rows,
        "samples": samples,
    }
