"""Analyze skipped / not-bought selection rows and would-have-won rates."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config.settings import DATA_DIR, RESOLUTIONS_CACHE_FILE, SELECTIONS_DIR, TRADE_HISTORY_FILE, settings
from src.analysis.pattern_enrichment import forecast_delta_c
from src.analysis.resolution import fetch_resolved_event
from src.utils.market_parser import (
    compare_temp_buckets,
    extract_temp_label,
    forecast_matches_winning_temp,
)

logger = logging.getLogger(__name__)


def _row_selection_price(row: dict[str, Any]) -> Optional[float]:
    for key in ("selection_price", "buy_price", "yes_price", "clob_mid_price", "gamma_price", "midpoint"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        if 0.0 < price <= 1.0:
            return price
    return None


def _parse_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list) and parsed:
                return _parse_price(parsed[0])
        try:
            price = float(text)
        except ValueError:
            return None
    else:
        try:
            price = float(raw)
        except (TypeError, ValueError):
            return None
    if 0.0 < price <= 1.0:
        return price
    return None


def _buy_price_band(price: float) -> str:
    """0.05-wide buy-$ bands, e.g. 0.60–0.65."""
    if price < 0:
        return "<0.00"
    if price >= 1.0:
        return "1.00+"
    lo = int(price * 20) / 20.0
    hi = round(lo + 0.05, 2)
    return f"{lo:.2f}–{hi:.2f}"


def _pnl_if_bought(price: float, would_have_won: Optional[bool], shares: float) -> Optional[float]:
    """Binary-market P&L if we had bought `shares` at `price` and held to resolution."""
    if would_have_won is None or shares <= 0:
        return None
    if would_have_won:
        return round(shares * (1.0 - price), 4)
    return round(-shares * price, 4)


def _run_at_ts(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _build_price_indexes(
    rows: list[dict[str, Any]],
    *,
    data_dir: Optional[Path] = None,
) -> tuple[dict[str, list[tuple[float, float]]], dict[str, float], dict[str, float]]:
    """market_id -> [(ts, price)], market_id -> event price, event_id -> top-yes price."""
    by_market_snap: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        mid = str(row.get("market_id") or "")
        price = _row_selection_price(row)
        if not mid or price is None:
            continue
        ts = _run_at_ts(row.get("_run_at")) or 0.0
        by_market_snap.setdefault(mid, []).append((ts, price))

    # Also index selection rows (bought) that carry prices.
    root = SELECTIONS_DIR
    if root.exists():
        for path in root.glob("markets_yes_*.json"):
            try:
                payload = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            ts = _run_at_ts(payload.get("run_at")) or 0.0
            for key in ("selections", "skipped_bought"):
                for row in payload.get(key) or []:
                    if not isinstance(row, dict):
                        continue
                    mid = str(row.get("market_id") or "")
                    price = _row_selection_price(row)
                    if not mid or price is None:
                        continue
                    by_market_snap.setdefault(mid, []).append((ts, price))

    for mid, points in by_market_snap.items():
        points.sort(key=lambda item: item[0])

    by_market_event: dict[str, float] = {}
    by_event_top: dict[str, float] = {}
    root_data = data_dir or DATA_DIR
    for path in root_data.glob("events_*.json"):
        try:
            events = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            eid = str(event.get("id") or "")
            best: Optional[float] = None
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                mid = str(market.get("id") or "")
                price = (
                    _parse_price(market.get("outcomePrices"))
                    or _parse_price(market.get("lastTradePrice"))
                    or _parse_price(market.get("midpoint"))
                    or _parse_price(market.get("bestBid"))
                )
                if mid and price is not None:
                    by_market_event[mid] = price
                if price is not None and (best is None or price > best):
                    best = price
            if eid and best is not None:
                by_event_top[eid] = best

    return by_market_snap, by_market_event, by_event_top


def _lookup_skip_price(
    row: dict[str, Any],
    *,
    by_market_snap: dict[str, list[tuple[float, float]]],
    by_market_event: dict[str, float],
    by_event_top: dict[str, float],
) -> tuple[Optional[float], Optional[str]]:
    """Resolve a buy price for a skip row. Returns (price, source)."""
    direct = _row_selection_price(row)
    if direct is not None:
        return direct, "direct"

    mid = str(row.get("market_id") or "")
    ts = _run_at_ts(row.get("_run_at"))
    if mid and mid in by_market_snap:
        points = by_market_snap[mid]
        if ts is None:
            return points[-1][1], "selection_snapshot"
        # Nearest snapshot by time.
        best = min(points, key=lambda item: abs(item[0] - ts))
        return best[1], "selection_snapshot"

    if mid and mid in by_market_event:
        return by_market_event[mid], "events_market"

    eid = str(row.get("event_id") or "")
    if eid and eid in by_event_top:
        return by_event_top[eid], "events_top_yes"

    return None, None


def _empty_reason_stats(reason: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "count": 0,
        "with_temp": 0,
        "with_slug": 0,
        "with_price": 0,
        "resolved": 0,
        "would_have_won": 0,
        "would_have_lost": 0,
        "unknown_outcome": 0,
        "_price_sum": 0.0,
        "_pnl_sum": 0.0,
        "_pnl_n": 0,
    }


def _finalize_stats(stats: dict[str, Any]) -> dict[str, Any]:
    resolved = int(stats["resolved"])
    won = int(stats["would_have_won"])
    whw_pct = round(100.0 * won / resolved, 1) if resolved else None
    with_price = int(stats["with_price"])
    avg_price = (
        round(float(stats["_price_sum"]) / with_price, 4) if with_price else None
    )
    pnl_n = int(stats["_pnl_n"])
    total_pnl = round(float(stats["_pnl_sum"]), 2) if pnl_n else None
    out = {
        k: v
        for k, v in stats.items()
        if not str(k).startswith("_")
    }
    out.update(
        {
            "avg_price": avg_price,
            "total_pnl_if_bought": total_pnl,
            "pnl_n": pnl_n,
            "would_have_won_pct": whw_pct,
            "filter_costly": bool(whw_pct is not None and whw_pct >= 50 and resolved >= 5),
            "filter_helpful": bool(
                whw_pct is not None and whw_pct < 40 and resolved >= 5
            ),
        }
    )
    return out


def _empty_forecast_group(group: str) -> dict[str, Any]:
    return {
        "group": group,
        "count": 0,
        "resolved": 0,
        "would_have_won": 0,
        "would_have_lost": 0,
        "om_match_resolved": 0,
        "om_match": 0,
        "wu_match_resolved": 0,
        "wu_match": 0,
        "_price_sum": 0.0,
        "with_price": 0,
        "_pnl_sum": 0.0,
        "_pnl_n": 0,
    }


def _finalize_forecast_group(stats: dict[str, Any]) -> dict[str, Any]:
    resolved = int(stats["resolved"])
    won = int(stats["would_have_won"])
    om_r = int(stats["om_match_resolved"])
    wu_r = int(stats["wu_match_resolved"])
    with_price = int(stats["with_price"])
    pnl_n = int(stats["_pnl_n"])
    return {
        "group": stats["group"],
        "count": int(stats["count"]),
        "resolved": resolved,
        "would_have_won": won,
        "would_have_lost": int(stats["would_have_lost"]),
        "would_have_won_pct": round(100.0 * won / resolved, 1) if resolved else None,
        "om_match": int(stats["om_match"]),
        "om_match_resolved": om_r,
        "om_match_pct": round(100.0 * int(stats["om_match"]) / om_r, 1) if om_r else None,
        "wu_match": int(stats["wu_match"]),
        "wu_match_resolved": wu_r,
        "wu_match_pct": round(100.0 * int(stats["wu_match"]) / wu_r, 1) if wu_r else None,
        "avg_price": round(float(stats["_price_sum"]) / with_price, 4) if with_price else None,
        "total_pnl_if_bought": round(float(stats["_pnl_sum"]), 2) if pnl_n else None,
        "pnl_n": pnl_n,
    }


def _bump_forecast_group(
    stats: dict[str, Any],
    *,
    whw: Optional[bool],
    om_match: Optional[bool],
    wu_match: Optional[bool],
    price: Optional[float],
    pnl: Optional[float],
) -> None:
    stats["count"] += 1
    if whw is True:
        stats["resolved"] += 1
        stats["would_have_won"] += 1
    elif whw is False:
        stats["resolved"] += 1
        stats["would_have_lost"] += 1
    if om_match is True:
        stats["om_match_resolved"] += 1
        stats["om_match"] += 1
    elif om_match is False:
        stats["om_match_resolved"] += 1
    if wu_match is True:
        stats["wu_match_resolved"] += 1
        stats["wu_match"] += 1
    elif wu_match is False:
        stats["wu_match_resolved"] += 1
    if price is not None:
        stats["with_price"] += 1
        stats["_price_sum"] += price
    if pnl is not None:
        stats["_pnl_sum"] += pnl
        stats["_pnl_n"] += 1


def _spread_band(spread: Optional[float]) -> str:
    if spread is None:
        return "unknown"
    try:
        s = float(spread)
    except (TypeError, ValueError):
        return "unknown"
    if s < 0:
        return "<0.00"
    if s >= 0.50:
        return "0.50+"
    lo = int(s * 20) / 20.0
    hi = round(lo + 0.05, 2)
    return f"{lo:.2f}–{hi:.2f}"


def _time_band_from_minutes(total: int, *, start: int = 12 * 60, end: int = 16 * 60) -> str:
    if total < start:
        return f"before {start // 60:02d}:{start % 60:02d}"
    if total >= end:
        return f"after {end // 60:02d}:{end % 60:02d}"
    band_start = start + ((total - start) // 15) * 15
    band_end = band_start + 15
    return (
        f"{band_start // 60:02d}:{band_start % 60:02d}-"
        f"{band_end // 60:02d}:{band_end % 60:02d}"
    )


def _load_city_timezones() -> dict[str, str]:
    path = DATA_DIR / "city_timezones.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


def _local_slot_for_skip(row: dict[str, Any], tz_map: dict[str, str]) -> str:
    city = str(row.get("city") or "")
    run_at = row.get("_run_at")
    tz_name = tz_map.get(city)
    if not run_at or not tz_name:
        return "unknown"
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            from datetime import timezone as _tz

            dt = dt.replace(tzinfo=_tz.utc)
        local = dt.astimezone(ZoneInfo(tz_name))
        return _time_band_from_minutes(local.hour * 60 + local.minute)
    except Exception:
        return "unknown"


def _market_dedupe_key(row: dict[str, Any]) -> Optional[str]:
    """Identity for one event market (same city/day/temp across hourly re-skips)."""
    mid = row.get("market_id")
    if mid is not None and str(mid):
        return f"market:{mid}"
    eid = row.get("event_id")
    title = _bought_title(row)
    temp = extract_temp_label(str(title)) if title else ""
    if eid is not None and temp:
        return f"event:{eid}:{temp}"
    slug = row.get("_resolved_event_slug") or row.get("event_slug")
    if slug and temp:
        return f"slug:{slug}:{temp}"
    city = str(row.get("city") or "")
    event_date = _date_from_selection_meta(row)
    if city and event_date and temp:
        return f"citydate:{city.lower()}:{event_date}:{temp}"
    return None


def _first_yes_price_max_skip_ids(rows: list[dict[str, Any]]) -> set[int]:
    """Earliest yes_price_max skip per market — later re-skips of same market excluded."""
    chronological = sorted(rows, key=lambda r: _run_at_ts(r.get("_run_at")) or 0.0)
    first_ids: set[int] = set()
    seen: set[str] = set()
    for row in chronological:
        if str(row.get("reason") or "") != "yes_price_max":
            continue
        key = _market_dedupe_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        first_ids.add(id(row))
    return first_ids


def _parse_float_opt(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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

    shares = float(getattr(settings, "share_count", 10) or 10)
    by_market_snap, by_market_event, by_event_top = _build_price_indexes(rows)
    by_reason: dict[str, dict[str, Any]] = {}
    yes_price_max_bands: dict[str, dict[str, Any]] = {}
    yes_price_max_first_bands: dict[str, dict[str, Any]] = {}
    recent_rows: list[dict[str, Any]] = []
    slug_hits = 0
    price_hits = 0
    price_sources: dict[str, int] = {}
    total_pnl_sum = 0.0
    total_pnl_n = 0
    forecast_n = 0
    wu_n = 0
    om_wu_diff_sum = 0.0
    om_wu_diff_n = 0
    om_wu_agree_1c = 0
    delta_win_sum = 0.0
    delta_win_n = 0
    delta_lose_sum = 0.0
    delta_lose_n = 0

    tz_map = _load_city_timezones()
    fc_overall = _empty_forecast_group("all")
    fc_by_slot: dict[str, dict[str, Any]] = {}
    fc_by_reason: dict[str, dict[str, Any]] = {}
    fc_by_price: dict[str, dict[str, Any]] = {}
    fc_by_spread: dict[str, dict[str, Any]] = {}

    # Earliest yes_price_max skip per market only (e.g. London 27°C at 14:15 counts;
    # same market again at 14:45 does not). Other skip reasons do not consume the slot.
    first_ypm_skip_ids = _first_yes_price_max_skip_ids(rows)

    for row in rows:
        reason = str(row.get("reason") or "unknown")
        stats = by_reason.setdefault(reason, _empty_reason_stats(reason))
        stats["count"] += 1
        title = _bought_title(row)
        if title:
            stats["with_temp"] += 1
        slug = row.get("_resolved_event_slug") or _event_slug_from_row(row, id_index=id_index)
        if slug:
            stats["with_slug"] += 1
            slug_hits += 1
        price, price_source = _lookup_skip_price(
            row,
            by_market_snap=by_market_snap,
            by_market_event=by_market_event,
            by_event_top=by_event_top,
        )
        if price is not None:
            stats["with_price"] += 1
            stats["_price_sum"] += price
            price_hits += 1
            if price_source:
                price_sources[price_source] = price_sources.get(price_source, 0) + 1
        whw = _would_have_won(row, res_map, id_index=id_index)
        if whw is True:
            stats["resolved"] += 1
            stats["would_have_won"] += 1
        elif whw is False:
            stats["resolved"] += 1
            stats["would_have_lost"] += 1
        elif title:
            stats["unknown_outcome"] += 1

        pnl = _pnl_if_bought(price, whw, shares) if price is not None else None
        if pnl is not None:
            stats["_pnl_sum"] += pnl
            stats["_pnl_n"] += 1
            total_pnl_sum += pnl
            total_pnl_n += 1

        fc = _parse_float_opt(row.get("forecast_temp_c"))
        ff = _parse_float_opt(row.get("forecast_temp_f"))
        wu_c = _parse_float_opt(row.get("forecast_wu_temp_c"))
        wu_f = _parse_float_opt(row.get("forecast_wu_temp_f"))
        delta = row.get("forecast_delta_c")
        if delta is None and title and (fc is not None or ff is not None):
            try:
                delta = forecast_delta_c(
                    str(title),
                    forecast_temp_f=ff,
                    forecast_temp_c=fc,
                )
            except (TypeError, ValueError):
                delta = None
        if fc is not None or ff is not None:
            forecast_n += 1
        if wu_c is not None or wu_f is not None:
            wu_n += 1
        if fc is not None and wu_c is not None:
            try:
                diff = abs(float(fc) - float(wu_c))
                om_wu_diff_sum += diff
                om_wu_diff_n += 1
                if diff <= 1.0:
                    om_wu_agree_1c += 1
            except (TypeError, ValueError):
                pass
        if delta is not None:
            try:
                dabs = abs(float(delta))
            except (TypeError, ValueError):
                dabs = None
            if dabs is not None:
                if whw is True:
                    delta_win_sum += dabs
                    delta_win_n += 1
                elif whw is False:
                    delta_lose_sum += dabs
                    delta_lose_n += 1

        winning = res_map.get(slug or "") if slug else None
        # Forecast vs actual event result (winning temp) — needs forecast + resolution.
        om_match = forecast_matches_winning_temp(
            winning, forecast_temp_c=fc, forecast_temp_f=ff
        )
        wu_match = forecast_matches_winning_temp(
            winning, forecast_temp_c=wu_c, forecast_temp_f=wu_f
        )
        local_slot = _local_slot_for_skip(row, tz_map)
        spread_raw = _parse_float_opt(row.get("spread"))
        spread_band = _spread_band(spread_raw)
        price_band = _buy_price_band(price) if price is not None else "unknown"

        # Every skip row contributes to forecast-compare groups (would-win vs event
        # result whenever resolved; OM/WU match when that skip also has a forecast).
        _bump_forecast_group(
            fc_overall, whw=whw, om_match=om_match, wu_match=wu_match, price=price, pnl=pnl
        )
        for group_map, group_key in (
            (fc_by_slot, local_slot),
            (fc_by_reason, reason),
            (fc_by_price, price_band),
            (fc_by_spread, spread_band),
        ):
            gstats = group_map.setdefault(group_key, _empty_forecast_group(group_key))
            _bump_forecast_group(
                gstats, whw=whw, om_match=om_match, wu_match=wu_match, price=price, pnl=pnl
            )

        is_first_ypm_skip = id(row) in first_ypm_skip_ids

        if reason == "yes_price_max" and price is not None:
            band = _buy_price_band(price)
            band_stats = yes_price_max_bands.setdefault(band, _empty_reason_stats(band))
            band_stats["count"] += 1
            band_stats["with_price"] += 1
            band_stats["_price_sum"] += price
            if title:
                band_stats["with_temp"] += 1
            if slug:
                band_stats["with_slug"] += 1
            if whw is True:
                band_stats["resolved"] += 1
                band_stats["would_have_won"] += 1
            elif whw is False:
                band_stats["resolved"] += 1
                band_stats["would_have_lost"] += 1
            elif title:
                band_stats["unknown_outcome"] += 1
            if pnl is not None:
                band_stats["_pnl_sum"] += pnl
                band_stats["_pnl_n"] += 1

            if is_first_ypm_skip:
                first_stats = yes_price_max_first_bands.setdefault(
                    band, _empty_reason_stats(band)
                )
                first_stats["count"] += 1
                first_stats["with_price"] += 1
                first_stats["_price_sum"] += price
                if title:
                    first_stats["with_temp"] += 1
                if slug:
                    first_stats["with_slug"] += 1
                if whw is True:
                    first_stats["resolved"] += 1
                    first_stats["would_have_won"] += 1
                elif whw is False:
                    first_stats["resolved"] += 1
                    first_stats["would_have_lost"] += 1
                elif title:
                    first_stats["unknown_outcome"] += 1
                if pnl is not None:
                    first_stats["_pnl_sum"] += pnl
                    first_stats["_pnl_n"] += 1

        recent_rows.append(
            {
                "run_at": row.get("_run_at"),
                "city": row.get("city"),
                "reason": reason,
                "temp": extract_temp_label(title) if title else None,
                "selection_price": price,
                "price_source": price_source,
                "pnl_if_bought": pnl,
                "event_slug": slug,
                "would_have_won": whw,
                "timezone": row.get("timezone"),
                "forecast_temp_c": fc,
                "forecast_temp_f": ff,
                "forecast_wu_temp_c": wu_c,
                "forecast_wu_temp_f": wu_f,
                "forecast_source": row.get("forecast_source"),
                "forecast_delta_c": delta,
                "winning_temp": winning,
                "om_match_win": om_match,
                "wu_match_win": wu_match,
                "local_slot": local_slot,
                "spread": spread_raw,
                "first_skip": is_first_ypm_skip,
            }
        )

    reason_rows = [_finalize_stats(stats) for stats in by_reason.values()]
    reason_rows.sort(key=lambda r: (-int(r["count"]), r["reason"]))

    band_rows = [_finalize_stats(stats) for stats in yes_price_max_bands.values()]
    band_rows.sort(key=lambda r: r["reason"])
    first_band_rows = [_finalize_stats(stats) for stats in yes_price_max_first_bands.values()]
    first_band_rows.sort(key=lambda r: r["reason"])

    recent_rows.sort(
        key=lambda r: _run_at_ts(r.get("run_at")) or 0.0,
        reverse=True,
    )
    recent_skips = recent_rows[:15]

    def _sorted_fc_groups(groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows_out = [_finalize_forecast_group(s) for s in groups.values()]
        rows_out.sort(key=lambda r: str(r["group"]))
        return rows_out

    overall = _finalize_forecast_group(fc_overall)
    total = len(rows)
    resolved_total = sum(int(r["resolved"]) for r in reason_rows)
    won_total = sum(int(r["would_have_won"]) for r in reason_rows)
    return {
        "total_skips": total,
        "with_slug": slug_hits,
        "with_price": price_hits,
        "price_sources": price_sources,
        "share_count_assumed": shares,
        "resolved_skips": resolved_total,
        "would_have_won_total": won_total,
        "would_have_won_pct": round(100.0 * won_total / resolved_total, 1)
        if resolved_total
        else None,
        "total_pnl_if_bought": round(total_pnl_sum, 2) if total_pnl_n else None,
        "pnl_n": total_pnl_n,
        "resolutions_fetched": fetched,
        "by_reason": reason_rows,
        "yes_price_max_by_buy_band": band_rows,
        "yes_price_max_by_buy_band_first_skip": first_band_rows,
        "yes_price_max_includes_repeats": True,
        "recent_skips": recent_skips,
        # Keep samples alias for older dashboard payloads.
        "samples": recent_skips,
        "forecast_compare": {
            "with_forecast": forecast_n,
            "with_wu": wu_n,
            "om_wu_pairs": om_wu_diff_n,
            "om_wu_avg_abs_diff_c": round(om_wu_diff_sum / om_wu_diff_n, 2)
            if om_wu_diff_n
            else None,
            "om_wu_agree_within_1c": om_wu_agree_1c,
            "om_wu_agree_within_1c_pct": round(100.0 * om_wu_agree_1c / om_wu_diff_n, 1)
            if om_wu_diff_n
            else None,
            "avg_abs_delta_would_win_c": round(delta_win_sum / delta_win_n, 2)
            if delta_win_n
            else None,
            "avg_abs_delta_would_lose_c": round(delta_lose_sum / delta_lose_n, 2)
            if delta_lose_n
            else None,
            "delta_would_win_n": delta_win_n,
            "delta_would_lose_n": delta_lose_n,
            "overall": overall,
            "by_local_slot": _sorted_fc_groups(fc_by_slot),
            "by_reason": _sorted_fc_groups(fc_by_reason),
            "by_price_band": _sorted_fc_groups(fc_by_price),
            "by_spread_band": _sorted_fc_groups(fc_by_spread),
        },
    }
