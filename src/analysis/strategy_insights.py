"""Strategy insights computed from trade history ledger."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from src.analysis.models import TradeRecord


def _buy_price_band(price: float) -> str:
    if price < 0.40:
        return "<0.40"
    if price < 0.50:
        return "0.40–0.50"
    if price < 0.55:
        return "0.50–0.55"
    if price < 0.60:
        return "0.55–0.60"
    if price <= 0.70:
        return "0.60–0.70"
    return ">0.70"


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


def _timezone_group(city: str) -> str:
    if city in {"Tokyo", "Seoul", "Busan"}:
        return "Asia"
    if city in {"Paris", "London", "Milan", "Madrid", "Berlin"}:
        return "Europe"
    if city in {"New York", "Chicago", "Dallas", "Houston", "Atlanta", "Phoenix", "Denver"}:
        return "North America"
    if city in {"Sydney", "Melbourne", "Brisbane", "Wellington", "Auckland"}:
        return "Oceania"
    if city in {"Sao Paulo", "Rio de Janeiro", "Buenos Aires", "Santiago"}:
        return "South America"
    return "Other"


def _record_pnl(rec: TradeRecord) -> float | None:
    if rec.realized_pnl_usd is not None:
        return rec.realized_pnl_usd
    return rec.final_value_usd


def _weekday_label(date_str: str) -> str:
    if not date_str:
        return "Unknown"
    try:
        return datetime.fromisoformat(date_str).strftime("%A")
    except ValueError:
        return "Unknown"


def _group_metrics(records: list[TradeRecord], key_fn) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"count": 0, "wins": 0, "settled": 0, "pnl_usd": 0.0, "buy_usd": 0.0}
    )
    for rec in records:
        key = key_fn(rec)
        stats = grouped[key]
        stats["count"] += 1
        stats["buy_usd"] += rec.cost_basis_usd
        pnl = _record_pnl(rec)
        if pnl is not None:
            stats["pnl_usd"] += pnl
        if rec.result in ("win", "loss", "sold"):
            stats["settled"] += 1
        if rec.result == "win":
            stats["wins"] += 1

    result: dict[str, dict[str, float | int]] = {}
    for key, stats in grouped.items():
        count = int(stats["count"])
        settled = int(stats["settled"])
        wins = int(stats["wins"])
        pnl_usd = float(stats["pnl_usd"])
        buy_usd = float(stats["buy_usd"])
        result[key] = {
            "count": count,
            "wins": wins,
            "settled": settled,
            "win_rate_pct": round((wins / settled) * 100, 1) if settled else 0.0,
            "avg_buy_usd": round(buy_usd / count, 2) if count else 0.0,
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

        pnl = _record_pnl(rec)
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
            "pnl_usd": _record_pnl(rec),
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
