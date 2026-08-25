"""Counterfactual: also buy the runner-up (2nd-best Yes) with same shares."""

from __future__ import annotations

from typing import Any, Optional

from src.analysis.models import TradeRecord, _has_meaningful_shares
from src.utils.market_parser import compare_temp_buckets


def _buy_price(rec: TradeRecord) -> Optional[float]:
    try:
        price = float(rec.buy_price)
    except (TypeError, ValueError):
        return None
    if price <= 0 or price >= 1:
        return None
    return price


def _runner_price(rec: TradeRecord) -> Optional[float]:
    try:
        price = float(rec.runner_up_yes) if rec.runner_up_yes is not None else None
    except (TypeError, ValueError):
        return None
    if price is None or price <= 0 or price >= 1:
        return None
    return price


def dual_buy_row(rec: TradeRecord) -> Optional[dict[str, Any]]:
    """Hold-to-resolution dual buy of top + runner-up with same share count.

    Returns None when the counterfactual cannot be scored (open, dust, missing
    runner price/temp when needed, or unresolved winner).
    """
    if not _has_meaningful_shares(rec):
        return None
    if rec.result == "open" or not rec.winning_temp:
        return None

    p1 = _buy_price(rec)
    p2 = _runner_price(rec)
    if p1 is None or p2 is None:
        return None

    shares = float(rec.shares)
    bought_wins = compare_temp_buckets(rec.bought_temp or "", rec.winning_temp) == "same"
    runner_temp = (rec.runner_up_temp or "").strip() or None

    if bought_wins:
        runner_wins = False
        cover = "bought"
    elif not runner_temp:
        # Lost primary and no runner temp → cannot tell if top-2 covered.
        return None
    else:
        runner_wins = compare_temp_buckets(runner_temp, rec.winning_temp) == "same"
        cover = "runner_up" if runner_wins else "neither"

    cost_bought = round(shares * p1, 4)
    cost_runner = round(shares * p2, 4)
    cost_dual = round(cost_bought + cost_runner, 4)

    payout_bought = shares if bought_wins else 0.0
    payout_runner = shares if runner_wins else 0.0
    payout_dual = shares if (bought_wins or runner_wins) else 0.0

    pnl_bought_hold = round(payout_bought - cost_bought, 4)
    pnl_runner_hold = round(payout_runner - cost_runner, 4)
    pnl_dual = round(payout_dual - cost_dual, 4)

    actual_pnl = rec.realized_pnl_usd
    if actual_pnl is None:
        actual_pnl = rec.final_value_usd

    return {
        "date": rec.date,
        "city": rec.city,
        "bought_temp": rec.bought_temp,
        "runner_up_temp": runner_temp,
        "winning_temp": rec.winning_temp,
        "shares": shares,
        "buy_price": p1,
        "runner_up_yes": p2,
        "yes_gap": rec.yes_gap,
        "cover": cover,
        "cost_bought": cost_bought,
        "cost_runner": cost_runner,
        "cost_dual": cost_dual,
        "pnl_bought_hold": pnl_bought_hold,
        "pnl_runner_hold": pnl_runner_hold,
        "pnl_dual": pnl_dual,
        "pnl_delta_vs_bought_hold": round(pnl_dual - pnl_bought_hold, 4),
        "actual_pnl": actual_pnl,
        "dual_profit": pnl_dual > 0,
        "event_slug": rec.event_slug,
    }


def compute_dual_buy_analysis(records: list[TradeRecord]) -> dict[str, Any]:
    """Aggregate dual-buy (top + runner-up) hold-to-resolution counterfactual."""
    rows: list[dict[str, Any]] = []
    for rec in records:
        row = dual_buy_row(rec)
        if row is not None:
            rows.append(row)

    n = len(rows)
    cover_counts = {"bought": 0, "runner_up": 0, "neither": 0}
    for row in rows:
        cover_counts[row["cover"]] = cover_counts.get(row["cover"], 0) + 1

    dual_pnl = sum(r["pnl_dual"] for r in rows)
    bought_hold_pnl = sum(r["pnl_bought_hold"] for r in rows)
    runner_hold_pnl = sum(r["pnl_runner_hold"] for r in rows)
    dual_wins = sum(1 for r in rows if r["pnl_dual"] > 0)
    dual_flat = sum(1 for r in rows if r["pnl_dual"] == 0)
    covered = cover_counts["bought"] + cover_counts["runner_up"]

    # Recent examples: newest date first
    recent = sorted(
        rows,
        key=lambda r: (r.get("date") or "", r.get("city") or ""),
        reverse=True,
    )[:40]

    by_cover: dict[str, dict[str, Any]] = {}
    for cover in ("bought", "runner_up", "neither"):
        subset = [r for r in rows if r["cover"] == cover]
        by_cover[cover] = {
            "cover": cover,
            "n": len(subset),
            "pnl_dual": round(sum(r["pnl_dual"] for r in subset), 2),
            "pnl_bought_hold": round(sum(r["pnl_bought_hold"] for r in subset), 2),
            "pnl_runner_hold": round(sum(r["pnl_runner_hold"] for r in subset), 2),
            "avg_cost_dual": round(
                sum(r["cost_dual"] for r in subset) / len(subset), 2
            )
            if subset
            else None,
            "profit_pct": round(
                100.0 * sum(1 for r in subset if r["pnl_dual"] > 0) / len(subset), 1
            )
            if subset
            else None,
        }

    return {
        "n": n,
        "covered_n": covered,
        "covered_pct": round(100.0 * covered / n, 1) if n else None,
        "cover_counts": cover_counts,
        "dual_pnl": round(dual_pnl, 2),
        "bought_hold_pnl": round(bought_hold_pnl, 2),
        "runner_hold_pnl": round(runner_hold_pnl, 2),
        "pnl_delta_vs_bought_hold": round(dual_pnl - bought_hold_pnl, 2),
        "dual_profit_n": dual_wins,
        "dual_flat_n": dual_flat,
        "dual_loss_n": n - dual_wins - dual_flat,
        "dual_profit_pct": round(100.0 * dual_wins / n, 1) if n else None,
        "avg_dual_pnl": round(dual_pnl / n, 2) if n else None,
        "avg_cost_dual": round(sum(r["cost_dual"] for r in rows) / n, 2) if n else None,
        "by_cover": by_cover,
        "recent": recent,
        "assumption": (
            "Same share count on bought + runner-up Yes at buy-time prices; "
            "both held to resolution (ignores early sells)."
        ),
    }
