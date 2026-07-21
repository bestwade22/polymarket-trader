"""Nearest markets_yes_* enrichment (spread / Gamma) for simulation samples."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import SELECTIONS_DIR
from src.analysis.spread_lookup import compute_spread
from src.utils.market_parser import parse_float

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotEnrichment:
    run_at: datetime
    spread: Optional[float]
    best_bid: Optional[float]
    best_ask: Optional[float]
    gamma_yes_price: Optional[float]
    outcome_prices: Optional[Any]


def _parse_run_at(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iter_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("selections", "skipped", "skipped_bought"):
        for row in payload.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_enrichment_by_token(
    selections_dir: Optional[Path] = None,
) -> dict[str, list[SnapshotEnrichment]]:
    """Map yes_token_id -> enrichment snapshots (newest first)."""
    root = selections_dir or SELECTIONS_DIR
    by_token: dict[str, list[SnapshotEnrichment]] = {}
    if not root.exists():
        return by_token

    for path in sorted(root.glob("markets_yes_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        run_at = _parse_run_at(payload.get("run_at"))
        if run_at is None:
            try:
                stamp = path.stem.replace("markets_yes_", "")
                run_at = datetime.strptime(stamp, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
            except ValueError:
                run_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

        for row in _iter_rows(payload):
            token_id = str(row.get("yes_token_id") or row.get("token_id") or "")
            if not token_id:
                continue
            bid = parse_float(row.get("best_bid"))
            ask = parse_float(row.get("best_ask"))
            spread = compute_spread(bid, ask)
            if spread is None:
                spread = parse_float(row.get("spread"))
            gamma = parse_float(row.get("gamma_yes_price"))
            outcome_prices = row.get("outcomePrices")
            by_token.setdefault(token_id, []).append(
                SnapshotEnrichment(
                    run_at=run_at,
                    spread=spread,
                    best_bid=bid,
                    best_ask=ask,
                    gamma_yes_price=gamma,
                    outcome_prices=outcome_prices,
                )
            )

    for entries in by_token.values():
        entries.sort(key=lambda e: e.run_at, reverse=True)
    return by_token


def lookup_enrichment_near(
    token_id: str,
    at: datetime,
    *,
    index: Optional[dict[str, list[SnapshotEnrichment]]] = None,
    max_delta_seconds: int = 45 * 60,
) -> Optional[SnapshotEnrichment]:
    """Nearest markets_yes_* row for token within max_delta of `at`."""
    if not token_id:
        return None
    idx = index if index is not None else load_enrichment_by_token()
    entries = idx.get(str(token_id)) or []
    if not entries:
        return None
    best: Optional[tuple[float, SnapshotEnrichment]] = None
    for entry in entries:
        delta = abs((entry.run_at - at).total_seconds())
        if delta > max_delta_seconds:
            continue
        if best is None or delta < best[0]:
            best = (delta, entry)
    return best[1] if best else None
