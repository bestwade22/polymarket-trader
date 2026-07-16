"""Look up CLOB bid/ask spread at buy time from local selection snapshots."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import SELECTIONS_DIR
from src.utils.market_parser import parse_float

logger = logging.getLogger(__name__)


def compute_spread(best_bid: Any, best_ask: Any) -> Optional[float]:
    bid = parse_float(best_bid)
    ask = parse_float(best_ask)
    if bid is None or ask is None:
        return None
    if ask < bid:
        return None
    return round(ask - bid, 4)


def _parse_run_at(value: Any) -> Optional[datetime]:
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


def load_selection_spreads_by_token(
    selections_dir: Optional[Path] = None,
) -> dict[str, list[tuple[datetime, float, Optional[float], Optional[float]]]]:
    """Map yes_token_id -> [(run_at, spread, best_bid, best_ask), ...] newest first."""
    root = selections_dir or SELECTIONS_DIR
    by_token: dict[str, list[tuple[datetime, float, Optional[float], Optional[float]]]] = {}
    if not root.exists():
        return by_token

    for path in sorted(root.glob("markets_yes_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        run_at = _parse_run_at(payload.get("run_at"))
        if run_at is None:
            # Fall back to filename timestamp markets_yes_YYYY-MM-DD_HHMM.json
            try:
                stamp = path.stem.replace("markets_yes_", "")
                run_at = datetime.strptime(stamp, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
            except ValueError:
                run_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

        for row in _iter_selection_rows(payload):
            token_id = str(row.get("yes_token_id") or row.get("token_id") or "")
            if not token_id:
                continue
            bid = parse_float(row.get("best_bid"))
            ask = parse_float(row.get("best_ask"))
            spread = compute_spread(bid, ask)
            if spread is None:
                continue
            by_token.setdefault(token_id, []).append((run_at, spread, bid, ask))

    for token_id, entries in by_token.items():
        entries.sort(key=lambda item: item[0], reverse=True)
    return by_token


def lookup_spread_for_buy(
    token_id: str,
    bought_at: str,
    *,
    index: Optional[dict[str, list[tuple[datetime, float, Optional[float], Optional[float]]]]] = None,
) -> Optional[float]:
    """Pick spread from the selection snapshot closest to bought_at for this token."""
    if not token_id:
        return None
    idx = index if index is not None else load_selection_spreads_by_token()
    entries = idx.get(str(token_id)) or []
    if not entries:
        return None
    bought_dt = _parse_run_at(bought_at)
    if bought_dt is None:
        return entries[0][1]

    best: Optional[tuple[float, float]] = None  # (abs_delta_seconds, spread)
    for run_at, spread, _bid, _ask in entries:
        delta = abs((run_at - bought_dt).total_seconds())
        if best is None or delta < best[0]:
            best = (delta, spread)
    return best[1] if best else None
