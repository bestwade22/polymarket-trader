"""Strategy insights computed from trade history ledger."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.analysis.models import TradeRecord


def _buy_price_band(price: float) -> str:
    if price < 0.50:
        return "<0.50"
    if price <= 0.60:
        return "0.50–0.60"
    return ">0.60"


def compute_insights(records: list[TradeRecord]) -> dict[str, Any]:
    by_city: dict[str, dict[str, int]] = defaultdict(lambda: {"win": 0, "total": 0})
    by_price_band: dict[str, dict[str, int]] = defaultdict(lambda: {"win": 0, "total": 0})
    loss_vs_bought: dict[str, int] = defaultdict(int)
    roi_by_result: dict[str, list[float]] = defaultdict(list)
    sell_value_pcts: list[float] = []

    best: list[tuple[float, TradeRecord]] = []
    worst: list[tuple[float, TradeRecord]] = []

    sold_count = 0
    sold_regret = 0

    for rec in records:
        if rec.result in ("win", "loss", "sold"):
            by_city[rec.city]["total"] += 1
            by_price_band[_buy_price_band(rec.buy_price)]["total"] += 1
            if rec.result == "win":
                by_city[rec.city]["win"] += 1
                by_price_band[_buy_price_band(rec.buy_price)]["win"] += 1

        if rec.result == "loss" and rec.win_temp_vs_bought != "unknown":
            loss_vs_bought[rec.win_temp_vs_bought] += 1

        if rec.result == "sold":
            sold_count += 1
            if rec.sold_but_would_have_won:
                sold_regret += 1
            if rec.sell_value_pct is not None:
                sell_value_pcts.append(rec.sell_value_pct)

        pnl = rec.realized_pnl_usd if rec.realized_pnl_usd is not None else rec.final_value_usd
        if pnl is not None:
            roi_by_result[rec.result].append(pnl)
            best.append((pnl, rec))
            worst.append((pnl, rec))

    def win_rate(stats: dict[str, int]) -> float:
        return round((stats["win"] / stats["total"]) * 100, 1) if stats["total"] else 0.0

    city_rates = {
        city: {"win_rate_pct": win_rate(s), **s}
        for city, s in sorted(by_city.items())
    }
    price_band_rates = {
        band: {"win_rate_pct": win_rate(s), **s}
        for band, s in sorted(by_price_band.items())
    }

    avg_roi = {
        result: round(sum(vals) / len(vals), 2) if vals else 0.0
        for result, vals in roi_by_result.items()
    }

    best.sort(key=lambda x: x[0], reverse=True)
    worst.sort(key=lambda x: x[0])

    def _brief(rec: TradeRecord) -> dict[str, Any]:
        return {
            "date": rec.date,
            "city": rec.city,
            "bought_temp": rec.bought_temp,
            "result": rec.result,
            "pnl_usd": rec.realized_pnl_usd or rec.final_value_usd,
        }

    return {
        "win_rate_by_city": city_rates,
        "win_rate_by_buy_price_band": price_band_rates,
        "stop_loss_regret_rate_pct": round((sold_regret / sold_count) * 100, 1)
        if sold_count
        else 0.0,
        "loss_misselection": dict(loss_vs_bought),
        "avg_pnl_by_result": avg_roi,
        "avg_sell_value_pct": round(sum(sell_value_pcts) / len(sell_value_pcts), 2)
        if sell_value_pcts
        else None,
        "best_trades": [_brief(rec) for _, rec in best[:5]],
        "worst_trades": [_brief(rec) for _, rec in worst[:5]],
    }
