"""Look up competitive score and open interest at buy time."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import DATA_DIR, SELECTIONS_DIR
from src.utils.market_parser import get_yes_token_id, parse_float

logger = logging.getLogger(__name__)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _file_snapshot_time(path: Path) -> datetime:
    if path.stem.startswith("events_"):
        try:
            return datetime.strptime(path.stem.replace("events_", ""), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    if path.stem.startswith("markets_yes_"):
        try:
            stamp = path.stem.replace("markets_yes_", "")
            return datetime.strptime(stamp, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _iter_snapshot_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("selections", "skipped"):
        for row in payload.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_competitive_snapshot_by_token(
    selections_dir: Optional[Path] = None,
) -> dict[str, list[tuple[datetime, float]]]:
    """Map yes_token_id -> [(run_at, competitive), ...] newest first."""
    root = selections_dir or SELECTIONS_DIR
    by_token: dict[str, list[tuple[datetime, float]]] = {}
    if not root.exists():
        return by_token

    for path in sorted(root.glob("markets_yes_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        run_at = _parse_iso(payload.get("run_at")) or _file_snapshot_time(path)
        for row in _iter_snapshot_rows(payload):
            competitive = parse_float(row.get("competitive"))
            if competitive is None:
                continue
            token_id = str(row.get("yes_token_id") or row.get("token_id") or "")
            if not token_id:
                continue
            by_token.setdefault(token_id, []).append((run_at, competitive))

    for entries in by_token.values():
        entries.sort(key=lambda item: item[0], reverse=True)
    return by_token


def load_open_interest_snapshot_by_slug(
    selections_dir: Optional[Path] = None,
) -> dict[str, list[tuple[datetime, float]]]:
    """Map event_slug -> [(run_at, open_interest), ...] newest first."""
    root = selections_dir or SELECTIONS_DIR
    by_slug: dict[str, list[tuple[datetime, float]]] = {}
    if not root.exists():
        return by_slug

    for path in sorted(root.glob("markets_yes_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        run_at = _parse_iso(payload.get("run_at")) or _file_snapshot_time(path)
        payload_oi = parse_float(payload.get("open_interest"))
        for row in _iter_snapshot_rows(payload):
            open_interest = parse_float(row.get("open_interest"))
            if open_interest is None:
                open_interest = payload_oi
            if open_interest is None:
                continue
            slug = str(row.get("event_slug") or "")
            if not slug:
                continue
            by_slug.setdefault(slug, []).append((run_at, open_interest))

    for entries in by_slug.values():
        entries.sort(key=lambda item: item[0], reverse=True)
    return by_slug


def _closest_value(
    entries: list[tuple[datetime, float]],
    bought_at: str,
) -> Optional[float]:
    if not entries:
        return None
    bought_dt = _parse_iso(bought_at)
    if bought_dt is None:
        return entries[0][1]
    best: Optional[tuple[float, float]] = None
    for run_at, value in entries:
        delta = abs((run_at - bought_dt).total_seconds())
        if best is None or delta < best[0]:
            best = (delta, value)
    return best[1] if best else None


def lookup_competitive_from_snapshots(
    token_id: str,
    bought_at: str,
    *,
    index: Optional[dict[str, list[tuple[datetime, float]]]] = None,
) -> Optional[float]:
    if not token_id:
        return None
    idx = index if index is not None else load_competitive_snapshot_by_token()
    return _closest_value(idx.get(str(token_id)) or [], bought_at)


def lookup_open_interest_from_snapshots(
    event_slug: str,
    bought_at: str,
    *,
    index: Optional[dict[str, list[tuple[datetime, float]]]] = None,
) -> Optional[float]:
    if not event_slug:
        return None
    idx = index if index is not None else load_open_interest_snapshot_by_slug()
    return _closest_value(idx.get(str(event_slug)) or [], bought_at)


def load_event_metrics_index(
    data_dir: Optional[Path] = None,
) -> dict[str, list[tuple[datetime, dict[str, Any]]]]:
    """Map event_slug -> [(snapshot_time, event_dict), ...] sorted newest first."""
    root = data_dir or DATA_DIR
    by_slug: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}

    paths = sorted(root.glob("events*.json"))
    for path in paths:
        try:
            events = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(events, list):
            continue
        file_time = _file_snapshot_time(path)
        for event in events:
            if not isinstance(event, dict):
                continue
            slug = str(event.get("slug") or "")
            if not slug:
                continue
            updated = _parse_iso(event.get("updatedAt")) or file_time
            by_slug.setdefault(slug, []).append((updated, event))

    for entries in by_slug.values():
        entries.sort(key=lambda item: item[0], reverse=True)
    return by_slug


def lookup_metrics_from_events(
    *,
    event_slug: str,
    token_id: str,
    bought_at: str,
    index: Optional[dict[str, list[tuple[datetime, dict[str, Any]]]]] = None,
) -> tuple[Optional[float], Optional[float]]:
    """Return (competitive for bucket, open_interest for event) from cached event files."""
    if not event_slug:
        return None, None
    idx = index if index is not None else load_event_metrics_index()
    entries = idx.get(event_slug) or []
    if not entries:
        return None, None

    bought_dt = _parse_iso(bought_at)
    if bought_dt is None:
        event = entries[0][1]
    else:
        best = min(entries, key=lambda item: abs((item[0] - bought_dt).total_seconds()))
        event = best[1]

    open_interest = parse_float(event.get("openInterest"))
    competitive: Optional[float] = None
    for market in event.get("markets") or []:
        if not isinstance(market, dict):
            continue
        yes_token = get_yes_token_id(market)
        if yes_token and str(yes_token) == str(token_id):
            competitive = parse_float(market.get("competitive"))
            break
    return competitive, open_interest


def lookup_competitive_for_buy(
    token_id: str,
    bought_at: str,
    *,
    event_slug: str = "",
    snapshot_index: Optional[dict[str, list[tuple[datetime, float]]]] = None,
    event_index: Optional[dict[str, list[tuple[datetime, dict[str, Any]]]]] = None,
) -> Optional[float]:
    competitive = lookup_competitive_from_snapshots(
        token_id, bought_at, index=snapshot_index
    )
    if competitive is not None:
        return competitive
    competitive, _ = lookup_metrics_from_events(
        event_slug=event_slug,
        token_id=token_id,
        bought_at=bought_at,
        index=event_index,
    )
    return competitive


def lookup_open_interest_for_buy(
    event_slug: str,
    bought_at: str,
    *,
    token_id: str = "",
    snapshot_index: Optional[dict[str, list[tuple[datetime, float]]]] = None,
    event_index: Optional[dict[str, list[tuple[datetime, dict[str, Any]]]]] = None,
) -> Optional[float]:
    open_interest = lookup_open_interest_from_snapshots(
        event_slug, bought_at, index=snapshot_index
    )
    if open_interest is not None:
        return open_interest
    _, open_interest = lookup_metrics_from_events(
        event_slug=event_slug,
        token_id=token_id,
        bought_at=bought_at,
        index=event_index,
    )
    return open_interest
