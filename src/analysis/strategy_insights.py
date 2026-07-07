"""Strategy insights computed from trade history ledger."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from typing import Any

from config.settings import DATA_DIR
from src.analysis.models import TradeRecord, _is_sold_lose, _is_sold_win, _record_pnl_value


def _buy_price_band(price: float) -> str:
    if price < 0.30:
        return "<0.30"
    if price > 0.60:
        return ">0.60"
    idx = min(int((price - 0.30) / 0.05), 5)
    lo = 0.30 + idx * 0.05
    hi = lo + 0.05
    return f"{lo:.2f}–{hi:.2f}"


def _local_time_band(local_time: str) -> str:
    if not local_time or ":" not in local_time:
        return "unknown"
    hour, minute = (int(part) for part in local_time.split(":", 1))
    total = hour * 60 + minute
    start = 12 * 60
    end = 15 * 60 + 30
    if total < start:
        return "before 12:00"
    if total >= end:
        return "after 15:30"
    band_start = start + ((total - start) // 15) * 15
    band_end = band_start + 15
    return (
        f"{band_start // 60:02d}:{band_start % 60:02d}-"
        f"{band_end // 60:02d}:{band_end % 60:02d}"
    )


_TZ_LABELS: dict[str, str] = {
    "Asia/Shanghai": "China (UTC+8)",
    "Asia/Hong_Kong": "Hong Kong (UTC+8)",
    "Asia/Taipei": "Taiwan (UTC+8)",
    "Asia/Singapore": "Singapore (UTC+8)",
    "Asia/Kuala_Lumpur": "Malaysia (UTC+8)",
    "Asia/Manila": "Philippines (UTC+8)",
    "Asia/Tokyo": "Japan (UTC+9)",
    "Asia/Seoul": "Korea (UTC+9)",
    "Asia/Kolkata": "India (UTC+5:30)",
    "Asia/Karachi": "Pakistan (UTC+5)",
    "Asia/Riyadh": "Arabia (UTC+3)",
    "Asia/Jerusalem": "Israel (UTC+2/+3)",
    "Europe/London": "UK (UTC+0/+1)",
    "Europe/Paris": "Central EU (UTC+1/+2)",
    "Europe/Berlin": "Central EU (UTC+1/+2)",
    "Europe/Rome": "Central EU (UTC+1/+2)",
    "Europe/Madrid": "Central EU (UTC+1/+2)",
    "Europe/Amsterdam": "Central EU (UTC+1/+2)",
    "Europe/Helsinki": "Eastern EU (UTC+2/+3)",
    "Europe/Istanbul": "Turkey (UTC+3)",
    "Europe/Moscow": "Russia (UTC+3)",
    "Europe/Warsaw": "Poland (UTC+1/+2)",
    "America/New_York": "US East (UTC-5/-4)",
    "America/Chicago": "US Central (UTC-6/-5)",
    "America/Denver": "US Mountain (UTC-7/-6)",
    "America/Los_Angeles": "US West (UTC-8/-7)",
    "America/Toronto": "Canada East (UTC-5/-4)",
    "America/Mexico_City": "Mexico (UTC-6)",
    "America/Panama": "Panama (UTC-5)",
    "America/Argentina/Buenos_Aires": "Argentina (UTC-3)",
    "America/Sao_Paulo": "Brazil (UTC-3)",
    "Pacific/Auckland": "NZ (UTC+12/+13)",
    "Africa/Johannesburg": "South Africa (UTC+2)",
}


@lru_cache(maxsize=1)
def _city_tz_map() -> dict[str, str]:
    path = DATA_DIR / "city_timezones.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _timezone_group(city: str) -> str:
    tz = _city_tz_map().get(city)
    if not tz:
        return "Unknown"
    return _TZ_LABELS.get(tz, tz)


def _weekday_label(date_str: str) -> str:
    if not date_str:
        return "Unknown"
    try:
        return datetime.fromisoformat(date_str).strftime("%A")
    except ValueError:
        return "Unknown"


def _group_metrics(records: list[TradeRecord], key_fn) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "count": 0,
            "wins": 0,
            "sold_wins": 0,
            "sold_loses": 0,
            "settled": 0,
            "pnl_usd": 0.0,
            "buy_usd": 0.0,
            "buy_price": 0.0,
        }
    )
    for rec in records:
        key = key_fn(rec)
        stats = grouped[key]
        stats["count"] += 1
        stats["buy_usd"] += rec.cost_basis_usd
        stats["buy_price"] += rec.buy_price
        pnl = _record_pnl_value(rec)
        if pnl is not None:
            stats["pnl_usd"] += pnl
        if rec.result in ("win", "loss", "sold"):
            stats["settled"] += 1
        if rec.result == "win":
            stats["wins"] += 1
        if _is_sold_win(rec):
            stats["sold_wins"] += 1
        if _is_sold_lose(rec):
            stats["sold_loses"] += 1

    result: dict[str, dict[str, float | int]] = {}
    for key, stats in grouped.items():
        count = int(stats["count"])
        settled = int(stats["settled"])
        wins = int(stats["wins"])
        sold_wins = int(stats["sold_wins"])
        win_plus_sold = wins + sold_wins
        pnl_usd = float(stats["pnl_usd"])
        buy_usd = float(stats["buy_usd"])
        buy_price = float(stats["buy_price"])
        result[key] = {
            "count": count,
            "wins": wins,
            "sold_wins": sold_wins,
            "sold_loses": int(stats["sold_loses"]),
            "win_plus_sold_win": win_plus_sold,
            "settled": settled,
            "win_rate_pct": round((wins / settled) * 100, 1) if settled else 0.0,
            "win_plus_sold_win_pct": round((win_plus_sold / settled) * 100, 1) if settled else 0.0,
            "avg_buy_usd": round(buy_usd / count, 2) if count else 0.0,
            "avg_buy_price": round(buy_price / count, 3) if count else 0.0,
            "avg_pnl_usd": round(pnl_usd / count, 2) if count else 0.0,
            "total_pnl_usd": round(pnl_usd, 2),
        }
    return dict(sorted(result.items()))


def compute_insights(records: list[TradeRecord]) -> dict[str, Any]:
    loss_vs_bought: dict[str, int] = defaultdict(int)
    pnl_by_result: dict[str, list[float]] = defaultdict(list)
    sell_value_pcts: list[float] = []

    best: list[tuple[float, TradeRecord]] = []
    worst: list[tuple[float, TradeRecord]] = []

    sold_count = 0
    sold_regret = 0

    for rec in records:
        if rec.result == "loss" and rec.win_temp_vs_bought != "unknown":
            loss_vs_bought[rec.win_temp_vs_bought] += 1

        if rec.result == "sold":
            sold_count += 1
            if rec.sold_but_would_have_won:
                sold_regret += 1
            if rec.sell_value_pct is not None:
                sell_value_pcts.append(rec.sell_value_pct)

        pnl = _record_pnl_value(rec)
        if pnl is not None:
            pnl_by_result[rec.result].append(pnl)
            best.append((pnl, rec))
            worst.append((pnl, rec))

    avg_pnl = {
        result: round(sum(vals) / len(vals), 2) if vals else 0.0
        for result, vals in pnl_by_result.items()
    }

    best.sort(key=lambda x: x[0], reverse=True)
    worst.sort(key=lambda x: x[0])

    def _brief(rec: TradeRecord) -> dict[str, Any]:
        return {
            "date": rec.date,
            "city": rec.city,
            "bought_temp": rec.bought_temp,
            "bought_at_local": rec.bought_at_local,
            "buy_price": rec.buy_price,
            "result": rec.result,
            "pnl_usd": _record_pnl_value(rec),
        }

    return {
        "summary_by_city": _group_metrics(records, lambda rec: rec.city or "Unknown"),
        "summary_by_timezone_group": _group_metrics(records, lambda rec: _timezone_group(rec.city)),
        "summary_by_buy_price_band": _group_metrics(records, lambda rec: _buy_price_band(rec.buy_price)),
        "summary_by_local_buy_time_band": _group_metrics(
            records, lambda rec: _local_time_band(rec.bought_at_local)
        ),
        "summary_by_win_temp_vs_bought": _group_metrics(
            records, lambda rec: rec.win_temp_vs_bought or "unknown"
        ),
        "summary_by_weekday": _group_metrics(
            records,
            lambda rec: _weekday_label(rec.date),
        ),
        "stop_loss_regret_rate_pct": round((sold_regret / sold_count) * 100, 1)
        if sold_count
        else 0.0,
        "loss_misselection": dict(loss_vs_bought),
        "avg_pnl_by_result": avg_pnl,
        "avg_sell_value_pct": round(sum(sell_value_pcts) / len(sell_value_pcts), 2)
        if sell_value_pcts
        else None,
        "best_trades": [_brief(rec) for _, rec in best[:5]],
        "worst_trades": [_brief(rec) for _, rec in worst[:5]],
    }
