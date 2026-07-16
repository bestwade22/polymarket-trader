"""Backfill / look up whether a buy was on the cool edge at order time."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import DATA_DIR, SELECTIONS_DIR
from src.analysis.edge import EDGE_PRICE_THRESHOLD, cooler_markets, is_on_edge, market_yes_prob
from src.api.clob_client import ClobPriceClient
from src.utils.market_parser import get_yes_token_id, parse_float

logger = logging.getLogger(__name__)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iter_selection_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("selections", "skipped"):
        for row in payload.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_on_edge_snapshot_by_token(
    selections_dir: Optional[Path] = None,
) -> dict[str, list[tuple[datetime, bool]]]:
    """Map yes_token_id -> [(run_at, on_edge), ...] when snapshots stored the flag."""
    root = selections_dir or SELECTIONS_DIR
    by_token: dict[str, list[tuple[datetime, bool]]] = {}
    if not root.exists():
        return by_token

    for path in sorted(root.glob("markets_yes_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        run_at = _parse_iso(payload.get("run_at"))
        if run_at is None:
            try:
                stamp = path.stem.replace("markets_yes_", "")
                run_at = datetime.strptime(stamp, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
            except ValueError:
                run_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

        for row in _iter_selection_rows(payload):
            if "on_edge" not in row or row.get("on_edge") is None:
                continue
            token_id = str(row.get("yes_token_id") or row.get("token_id") or "")
            if not token_id:
                continue
            by_token.setdefault(token_id, []).append((run_at, bool(row["on_edge"])))

    for entries in by_token.values():
        entries.sort(key=lambda item: item[0], reverse=True)
    return by_token


def lookup_on_edge_from_snapshots(
    token_id: str,
    bought_at: str,
    *,
    index: Optional[dict[str, list[tuple[datetime, bool]]]] = None,
) -> Optional[bool]:
    if not token_id:
        return None
    idx = index if index is not None else load_on_edge_snapshot_by_token()
    entries = idx.get(str(token_id)) or []
    if not entries:
        return None
    bought_dt = _parse_iso(bought_at)
    if bought_dt is None:
        return entries[0][1]
    best: Optional[tuple[float, bool]] = None
    for run_at, on_edge in entries:
        delta = abs((run_at - bought_dt).total_seconds())
        if best is None or delta < best[0]:
            best = (delta, on_edge)
    return best[1] if best else None


def load_event_markets_by_slug(data_dir: Optional[Path] = None) -> dict[str, list[dict[str, Any]]]:
    root = data_dir or DATA_DIR
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for path in root.glob("events_*.json"):
        try:
            events = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            slug = str(event.get("slug") or "")
            markets = event.get("markets") or []
            if slug and isinstance(markets, list):
                by_slug[slug] = markets
    return by_slug


def price_near_timestamp(
    clob: ClobPriceClient,
    token_id: str,
    bought_ts: int,
    *,
    window_seconds: int = 3600,
) -> Optional[float]:
    history = clob.get_prices_history(
        token_id,
        start_ts=bought_ts - window_seconds,
        end_ts=bought_ts + window_seconds,
        fidelity=1,
    )
    if not history:
        return None
    best_price: Optional[float] = None
    best_delta: Optional[float] = None
    for point in history:
        ts = int(point.get("t") or 0)
        price = parse_float(point.get("p"))
        if price is None:
            continue
        delta = abs(ts - bought_ts)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_price = price
    return best_price


def compute_on_edge_from_history(
    *,
    event_slug: str,
    bought_temp: str,
    bought_at: str,
    markets_by_slug: dict[str, list[dict[str, Any]]],
    clob: ClobPriceClient,
    threshold: float = EDGE_PRICE_THRESHOLD,
    price_cache: Optional[dict[tuple[str, int], Optional[float]]] = None,
) -> Optional[bool]:
    """Infer on_edge using event market list + CLOB prices near bought_at."""
    markets = markets_by_slug.get(event_slug)
    if not markets:
        return None
    cooler = cooler_markets(markets, bought_temp)
    if cooler is None:
        return None
    if not cooler:
        return True

    bought_dt = _parse_iso(bought_at)
    if bought_dt is None:
        return None
    # Bucket timestamps to 15 minutes to reuse CLOB lookups across similar buys.
    bought_ts = int(bought_dt.timestamp())
    cache_ts = bought_ts - (bought_ts % 900)
    cache = price_cache if price_cache is not None else {}

    for market in cooler:
        token_id = get_yes_token_id(market)
        price: Optional[float] = None
        if token_id:
            key = (str(token_id), cache_ts)
            if key not in cache:
                cache[key] = price_near_timestamp(clob, str(token_id), bought_ts)
            price = cache[key]
        if price is None:
            # Fallback to event-file Gamma/mid when history is missing (often dead books).
            price = market_yes_prob(market)
        if price is None:
            return None
        if price >= threshold:
            return False
    return True


def compute_on_edge_from_event_markets(
    markets: list[dict[str, Any]],
    selected_title: str,
) -> Optional[bool]:
    """Use in-memory refreshed event markets (Gamma + CLOB) at selection time."""
    return is_on_edge(markets, selected_title)
