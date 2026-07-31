"""Enrich trade records from selection snapshots and event cache files."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import DATA_DIR, SELECTIONS_DIR
from src.analysis.pattern_enrichment import apply_pattern_enrichment, apply_selection_pattern_fields
from src.analysis.runner_up import runner_up_yes_and_gap
from src.analysis.spread_lookup import compute_spread
from src.utils.market_parser import get_yes_token_id, parse_float

logger = logging.getLogger(__name__)

# Selection fields copied onto TradeRecord when missing.
ENRICH_FIELDS = (
    "spread",
    "best_bid",
    "best_ask",
    "midpoint",
    "gamma_yes_price",
    "clob_buy_price",
    "on_edge",
    "competitive",
    "open_interest",
    "runner_up_yes",
    "yes_gap",
    "forecast_temp_f",
    "forecast_temp_c",
)


def _parse_dt(value: Any) -> Optional[datetime]:
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


def _row_enrichment(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize selection-row fields for trade-history enrichment."""
    bid = parse_float(row.get("best_bid"))
    ask = parse_float(row.get("best_ask"))
    spread = parse_float(row.get("spread"))
    if spread is None:
        spread = compute_spread(bid, ask)
    return {
        "spread": spread,
        "best_bid": bid,
        "best_ask": ask,
        "midpoint": parse_float(row.get("midpoint")),
        "gamma_yes_price": parse_float(row.get("gamma_yes_price")),
        "clob_buy_price": parse_float(row.get("clob_buy_price")),
        "on_edge": row.get("on_edge") if isinstance(row.get("on_edge"), bool) else None,
        "competitive": parse_float(row.get("competitive")),
        "open_interest": parse_float(row.get("open_interest")),
        "runner_up_yes": parse_float(row.get("runner_up_yes")),
        "yes_gap": parse_float(row.get("yes_gap")),
        "forecast_temp_f": parse_float(row.get("forecast_temp_f")),
        "forecast_temp_c": parse_float(row.get("forecast_temp_c")),
        "liquidity": parse_float(row.get("liquidity") or row.get("liquidityNum")),
        "book_depth_near_touch": parse_float(
            row.get("book_depth_near_touch") or row.get("liquidity") or row.get("liquidityNum")
        ),
        "event_slug": row.get("event_slug"),
        "groupItemTitle": row.get("groupItemTitle") or row.get("group_item_title"),
    }


def load_selection_enrichment_by_token(
    selections_dir: Optional[Path] = None,
) -> dict[str, list[tuple[datetime, dict[str, Any]]]]:
    """Map yes_token_id -> [(run_at, enrichment), ...] newest first."""
    root = selections_dir or SELECTIONS_DIR
    by_token: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    if not root.exists():
        return by_token

    for path in sorted(root.glob("markets_yes_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        run_at = _parse_dt(payload.get("run_at"))
        if run_at is None:
            try:
                stamp = path.stem.replace("markets_yes_", "")
                run_at = datetime.strptime(stamp, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
            except ValueError:
                run_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

        for row in _iter_selection_rows(payload):
            token_id = str(row.get("yes_token_id") or row.get("token_id") or "")
            if not token_id:
                continue
            by_token.setdefault(token_id, []).append((run_at, _row_enrichment(row)))

    for entries in by_token.values():
        entries.sort(key=lambda item: item[0], reverse=True)
    return by_token


def lookup_enrichment_for_buy(
    token_id: str,
    bought_at: str,
    *,
    index: Optional[dict[str, list[tuple[datetime, dict[str, Any]]]]] = None,
) -> Optional[dict[str, Any]]:
    """Nearest selection snapshot enrichment for this token around bought_at."""
    if not token_id:
        return None
    idx = index if index is not None else load_selection_enrichment_by_token()
    entries = idx.get(str(token_id)) or []
    if not entries:
        return None
    bought_dt = _parse_dt(bought_at)
    if bought_dt is None:
        return dict(entries[0][1])

    best: Optional[tuple[float, dict[str, Any]]] = None
    for run_at, enrichment in entries:
        delta = abs((run_at - bought_dt).total_seconds())
        if best is None or delta < best[0]:
            best = (delta, enrichment)
    return dict(best[1]) if best else None


def _load_events_for_date(event_date: str, data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / f"events_{event_date}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        for key in ("events", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [e for e in rows if isinstance(e, dict)]
    return []


def load_event_market_index(
    data_dir: Optional[Path] = None,
) -> dict[str, dict[str, Any]]:
    """Map yes_token_id -> {spread, bid, ask, competitive, open_interest, runner_up, ...}.

    Built from dated events_*.json (fetch-time books). Used only when selection
    snapshots do not cover a trade token (early history).
    """
    root = data_dir or DATA_DIR
    by_token: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("events_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        events: list[dict[str, Any]]
        if isinstance(payload, list):
            events = [e for e in payload if isinstance(e, dict)]
        elif isinstance(payload, dict):
            events = []
            for key in ("events", "data"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    events = [e for e in rows if isinstance(e, dict)]
                    break
        else:
            continue

        for event in events:
            oi = parse_float(event.get("openInterest"))
            markets = event.get("markets") or []
            top_yes, runner_up, gap = runner_up_yes_and_gap(markets)
            for market in markets:
                if not isinstance(market, dict):
                    continue
                token_id = get_yes_token_id(market)
                if not token_id:
                    continue
                bid = parse_float(market.get("bestBid"))
                ask = parse_float(market.get("bestAsk"))
                spread = compute_spread(bid, ask)
                mid = parse_float(market.get("midpoint"))
                if mid is None and bid is not None and ask is not None:
                    mid = round((bid + ask) / 2, 4)
                from src.utils.market_parser import get_gamma_yes_price

                gamma = get_gamma_yes_price(market)
                by_token[str(token_id)] = {
                    "spread": spread,
                    "best_bid": bid,
                    "best_ask": ask,
                    "midpoint": mid,
                    "gamma_yes_price": gamma,
                    "clob_buy_price": parse_float(market.get("clobBuyPrice")) or ask,
                    "competitive": parse_float(market.get("competitive")),
                    "open_interest": oi,
                    "runner_up_yes": runner_up,
                    "yes_gap": gap,
                    "event_slug": event.get("slug"),
                    "top_yes": top_yes,
                }
    return by_token


def apply_enrichment_to_record(rec: Any, enrichment: dict[str, Any]) -> list[str]:
    """Fill missing TradeRecord fields from enrichment. Returns filled field names."""
    filled: list[str] = []
    for field in ENRICH_FIELDS:
        if enrichment.get(field) is None:
            continue
        current = getattr(rec, field, None)
        if current is None:
            setattr(rec, field, enrichment[field])
            filled.append(field)
    filled.extend(apply_selection_pattern_fields(rec, enrichment))
    return filled


def backfill_records_from_selections(
    records: list[Any],
    *,
    selections_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    use_event_fallback: bool = True,
) -> dict[str, int]:
    """Fill missing enrichment fields on trade records. Returns fill counts by field."""
    counts = {f: 0 for f in ENRICH_FIELDS}
    if not records:
        return counts

    needs_any = [
        rec
        for rec in records
        if any(getattr(rec, f, None) is None for f in ENRICH_FIELDS)
        or any(
            getattr(rec, f, None) is None
            for f in (
                "yes_gap_at_select",
                "book_depth_near_touch",
                "forecast_delta_c",
                "minutes_into_window",
                "loss_autopsy",
                "city_streak",
            )
        )
    ]

    sel_index = load_selection_enrichment_by_token(selections_dir) if needs_any else {}
    event_index = load_event_market_index(data_dir) if use_event_fallback and needs_any else {}

    for rec in needs_any:
        token_id = str(getattr(rec, "token_id", "") or "")
        enrichment = lookup_enrichment_for_buy(
            token_id, getattr(rec, "bought_at", "") or "", index=sel_index
        )
        if enrichment:
            for field in apply_enrichment_to_record(rec, enrichment):
                counts[field] = counts.get(field, 0) + 1

        still_missing = any(getattr(rec, f, None) is None for f in ENRICH_FIELDS)
        if still_missing and token_id and token_id in event_index:
            for field in apply_enrichment_to_record(rec, event_index[token_id]):
                counts[field] = counts.get(field, 0) + 1

        # Runner-up from event markets when still missing (even if other fields filled)
        if getattr(rec, "yes_gap", None) is None or getattr(rec, "runner_up_yes", None) is None:
            event_date = getattr(rec, "date", "") or ""
            slug = getattr(rec, "event_slug", "") or ""
            if event_date and slug:
                for event in _load_events_for_date(event_date, data_dir or DATA_DIR):
                    if event.get("slug") != slug:
                        continue
                    _top, runner, gap = runner_up_yes_and_gap(
                        event.get("markets") or [],
                        selected_market_id=None,
                    )
                    patch = {"runner_up_yes": runner, "yes_gap": gap}
                    for field in apply_enrichment_to_record(rec, patch):
                        counts[field] = counts.get(field, 0) + 1
                    break

    pattern_counts = apply_pattern_enrichment(records)
    for field, n in pattern_counts.items():
        counts[field] = counts.get(field, 0) + n

    filled_total = sum(counts.values())
    if filled_total:
        logger.info(
            "Selection/event enrichment fills: %s",
            {k: v for k, v in counts.items() if v},
        )
    return counts
