"""Strategy insights computed from trade history ledger."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from functools import lru_cache
from statistics import median
from typing import Any, Callable, Optional

from config.settings import DATA_DIR
from src.analysis.models import (
    TradeRecord,
    _counts_toward_win_summary,
    _counts_toward_win_summary_denom,
    _is_sold_lose,
    _is_sold_win,
    _is_sold_would_lose,
    _is_sold_would_win,
    _record_pnl_value,
    compute_outcome_value,
)
from src.utils.market_parser import forecast_vs_result_delta_c

# Align with Weather.com / WU scrape forecast path (same cutoff as forecast_compare).
FORECAST_BIAS_SINCE = date(2026, 8, 4)


def _buy_price_band(price: float) -> str:
    if price < 0.30:
        return "<0.30"
    if price > 0.60:
        return ">0.60"
    idx = min(int((price - 0.30) / 0.05), 5)
    lo = 0.30 + idx * 0.05
    hi = lo + 0.05
    return f"{lo:.2f}–{hi:.2f}"


def _time_band_from_minutes(total: int, *, start: int, end: int) -> str:
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


def _local_time_band(local_time: str) -> str:
    if not local_time or ":" not in local_time:
        return "unknown"
    hour, minute = (int(part) for part in local_time.split(":", 1))
    return _time_band_from_minutes(hour * 60 + minute, start=12 * 60, end=16 * 60)


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


def timezone_group(city: str) -> str:
    """Dashboard 'By city timezone' label for a city (e.g. US East (UTC-5/-4))."""
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


def _month_label(date_str: str) -> str:
    if not date_str:
        return "Unknown"
    return date_str[:7] if len(date_str) >= 7 else "Unknown"


def _week_label(date_str: str) -> str:
    if not date_str:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(date_str)
        year, week, _ = dt.isocalendar()
        return f"{year}-W{week:02d}"
    except ValueError:
        return "Unknown"


def _day_label(date_str: str) -> str:
    if not date_str:
        return "Unknown"
    return date_str[:10] if len(date_str) >= 10 else date_str


def _roi_band(rec: TradeRecord) -> str:
    if rec.roi_pct is None:
        return "unknown"
    roi = rec.roi_pct
    if roi < -50:
        return "<-50%"
    if roi < 0:
        return "-50–0%"
    if roi < 50:
        return "0–50%"
    if roi < 100:
        return "50–100%"
    return ">100%"


def _spread_band(spread: float | None) -> str:
    if spread is None:
        return "unknown"
    if spread < 0:
        return "unknown"
    idx = int(spread / 0.05)
    lo = idx * 0.05
    hi = lo + 0.05
    return f"{lo:.2f}–{hi:.2f}"


def _edge_label(on_edge: bool | None) -> str:
    if on_edge is None:
        return "unknown"
    return "Yes" if on_edge else "No"


def _competitive_band(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.98:
        return "0.98–1.00"
    if score < 0.80:
        return "<0.80"
    idx = int((score - 0.80) / 0.02)
    lo = 0.80 + idx * 0.02
    hi = lo + 0.02
    return f"{lo:.2f}–{hi:.2f}"


def _open_interest_band(open_interest: float | None) -> str:
    if open_interest is None:
        return "unknown"
    if open_interest < 0:
        return "unknown"
    step = 2000
    idx = int(open_interest / step)
    lo = idx * step
    hi = lo + step
    if lo >= 30000:
        return "≥30000"
    return f"{int(lo)}–{int(hi)}"


def _yes_gap_band(gap: float | None) -> str:
    if gap is None:
        return "unknown"
    if gap < 0:
        return "unknown"
    if gap >= 0.30:
        return "≥0.30"
    idx = int(gap / 0.05)
    lo = idx * 0.05
    hi = lo + 0.05
    return f"{lo:.2f}–{hi:.2f}"


def _loss_autopsy_label(tag: str | None) -> str:
    return tag or "n/a"


def _parse_record_date(rec: TradeRecord) -> Optional[date]:
    raw = (rec.date or "")[:10]
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _om_vs_result_delta(rec: TradeRecord) -> Optional[float]:
    return forecast_vs_result_delta_c(
        rec.winning_temp,
        forecast_temp_c=rec.forecast_temp_c,
        forecast_temp_f=rec.forecast_temp_f,
    )


def _wu_vs_result_delta(rec: TradeRecord) -> Optional[float]:
    return forecast_vs_result_delta_c(
        rec.winning_temp,
        forecast_temp_c=rec.forecast_wu_temp_c,
        forecast_temp_f=rec.forecast_wu_temp_f,
    )


def _forecast_vs_result_band(delta: Optional[float]) -> str:
    """Integer °C bands for forecast − result (positive = forecast hot)."""
    if delta is None:
        return "unknown"
    rounded = int(round(float(delta)))
    if rounded <= -3:
        return "≤-3"
    if rounded >= 3:
        return "≥+3"
    if rounded > 0:
        return f"+{rounded}"
    return str(rounded)


def _mean(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _median(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    return round(float(median(vals)), 2)


def compute_city_forecast_bias(
    records: list[TradeRecord],
    *,
    since: date = FORECAST_BIAS_SINCE,
) -> dict[str, dict[str, Any]]:
    """Per-city forecast − result bias (Weather.com/OM primary + WU) since `since`.

    `om_corr` / `wu_corr` = −mean bias (add to future forecasts to de-bias).
    """
    om_by_city: dict[str, list[float]] = defaultdict(list)
    wu_by_city: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        d = _parse_record_date(rec)
        if d is None or d < since:
            continue
        if not rec.winning_temp:
            continue
        city = rec.city or "Unknown"
        om = _om_vs_result_delta(rec)
        if om is not None:
            om_by_city[city].append(float(om))
        wu = _wu_vs_result_delta(rec)
        if wu is not None:
            wu_by_city[city].append(float(wu))

    cities = set(om_by_city) | set(wu_by_city)
    out: dict[str, dict[str, Any]] = {}
    for city in cities:
        om_vals = om_by_city.get(city) or []
        wu_vals = wu_by_city.get(city) or []
        om_mean = _mean(om_vals)
        wu_mean = _mean(wu_vals)
        out[city] = {
            "bias_since": since.isoformat(),
            "om_bias_n": len(om_vals),
            "om_bias_mean": om_mean,
            "om_bias_median": _median(om_vals),
            "om_corr": round(-om_mean, 2) if om_mean is not None else None,
            "wu_bias_n": len(wu_vals),
            "wu_bias_mean": wu_mean,
            "wu_bias_median": _median(wu_vals),
            "wu_corr": round(-wu_mean, 2) if wu_mean is not None else None,
        }
    return out


def _merge_city_bias(
    city_stats: dict[str, dict[str, Any]],
    bias: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = dict(city_stats)
    for city, row in merged.items():
        b = bias.get(city) or {}
        row.update(
            {
                "om_bias_n": b.get("om_bias_n", 0),
                "om_bias_mean": b.get("om_bias_mean"),
                "om_bias_median": b.get("om_bias_median"),
                "om_corr": b.get("om_corr"),
                "wu_bias_n": b.get("wu_bias_n", 0),
                "wu_bias_mean": b.get("wu_bias_mean"),
                "wu_bias_median": b.get("wu_bias_median"),
                "wu_corr": b.get("wu_corr"),
            }
        )
    # Cities with bias but no trades in filtered set still useful? Skip — city table is trades.
    return merged


def _sold_outcome_label(rec: TradeRecord) -> str:
    if rec.result != "sold":
        return "not_sold"
    if _is_sold_would_win(rec):
        return "would_win"
    if _is_sold_would_lose(rec):
        return "would_lose"
    if _is_sold_win(rec):
        return "sold_win"
    if _is_sold_lose(rec):
        return "sold_lose"
    return "sold"


def _group_metrics(
    records: list[TradeRecord],
    key_fn: Callable[[TradeRecord], str],
    *,
    track_cities: bool = False,
) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, dict[str, float | int | set[str]]] = defaultdict(
        lambda: {
            "count": 0,
            "wins": 0,
            "sold_wins": 0,
            "sold_would_loses": 0,
            "sold_loses": 0,
            "win_summary": 0,
            "settled": 0,
            "win_summary_denom": 0,
            "pnl_usd": 0.0,
            "buy_usd": 0.0,
            "buy_price": 0.0,
            "spread": 0.0,
            "spread_count": 0,
            "outcome_usd": 0.0,
            "outcome_count": 0,
            "cities": set(),
        }
    )
    for rec in records:
        key = key_fn(rec)
        stats = grouped[key]
        stats["count"] += 1
        stats["buy_usd"] += rec.cost_basis_usd
        stats["buy_price"] += rec.buy_price
        if rec.spread is not None:
            stats["spread"] += float(rec.spread)
            stats["spread_count"] += 1
        if track_cities and rec.city:
            stats["cities"].add(rec.city)
        pnl = _record_pnl_value(rec)
        if pnl is not None:
            stats["pnl_usd"] += pnl
        outcome = rec.outcome_value_usd
        if outcome is None:
            outcome = compute_outcome_value(rec)
        if outcome is not None:
            stats["outcome_usd"] += float(outcome)
            stats["outcome_count"] += 1
        if rec.result in ("win", "loss", "sold"):
            stats["settled"] += 1
        if _counts_toward_win_summary_denom(rec):
            stats["win_summary_denom"] += 1
        if rec.result == "win":
            stats["wins"] += 1
        if _is_sold_win(rec):
            stats["sold_wins"] += 1
        if _is_sold_would_lose(rec):
            stats["sold_would_loses"] += 1
        if _is_sold_lose(rec):
            stats["sold_loses"] += 1
        if _counts_toward_win_summary(rec):
            stats["win_summary"] += 1

    result: dict[str, dict[str, float | int]] = {}
    for key, stats in grouped.items():
        count = int(stats["count"])
        settled = int(stats["settled"])
        win_summary_denom = int(stats["win_summary_denom"])
        wins = int(stats["wins"])
        sold_wins = int(stats["sold_wins"])
        win_plus_sold = int(stats["win_summary"])
        pnl_usd = float(stats["pnl_usd"])
        buy_usd = float(stats["buy_usd"])
        buy_price = float(stats["buy_price"])
        outcome_count = int(stats["outcome_count"])
        outcome_usd = float(stats["outcome_usd"])
        row: dict[str, float | int] = {
            "count": count,
            "wins": wins,
            "sold_wins": sold_wins,
            "sold_loses": int(stats["sold_loses"]),
            "win_plus_sold_win": win_plus_sold,
            "settled": settled,
            "win_rate_pct": round((wins / settled) * 100, 1) if settled else 0.0,
            "win_plus_sold_win_pct": (
                round((win_plus_sold / win_summary_denom) * 100, 1) if win_summary_denom else 0.0
            ),
            "avg_buy_usd": round(buy_usd / count, 2) if count else 0.0,
            "avg_buy_price": round(buy_price / count, 3) if count else 0.0,
            "avg_spread": (
                round(float(stats["spread"]) / int(stats["spread_count"]), 4)
                if int(stats["spread_count"])
                else 0.0
            ),
            "avg_pnl_usd": round(pnl_usd / count, 2) if count else 0.0,
            "total_pnl_usd": round(pnl_usd, 2),
            "avg_outcome_value_usd": round(outcome_usd / outcome_count, 2) if outcome_count else 0.0,
            "total_outcome_value_usd": round(outcome_usd, 2),
        }
        if track_cities:
            cities = stats["cities"]
            row["city_count"] = len(cities) if isinstance(cities, set) else 0
        result[key] = row
    return dict(sorted(result.items()))


def _surviving_records_for_insights(records: list[TradeRecord]) -> list[TradeRecord]:
    """Lazy import avoids circular import with city_skip → timezone_group."""
    from src.trade.city_skip import surviving_records_for_skip

    return surviving_records_for_skip(records)


def compute_insights(records: list[TradeRecord]) -> dict[str, Any]:
    loss_vs_bought: dict[str, int] = defaultdict(int)
    pnl_by_result: dict[str, list[float]] = defaultdict(list)
    sell_value_pcts: list[float] = []

    best: list[tuple[float, TradeRecord]] = []
    worst: list[tuple[float, TradeRecord]] = []

    sold_count = 0
    sold_regret = 0
    sold_would_lose = 0

    for rec in records:
        if rec.result == "loss" and rec.win_temp_vs_bought != "unknown":
            loss_vs_bought[rec.win_temp_vs_bought] += 1

        if rec.result == "sold":
            sold_count += 1
            if _is_sold_would_win(rec):
                sold_regret += 1
            if _is_sold_would_lose(rec):
                sold_would_lose += 1
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
            "outcome_value_usd": rec.outcome_value_usd or compute_outcome_value(rec),
        }

    city_bias = compute_city_forecast_bias(records)
    summary_by_city = _merge_city_bias(
        _group_metrics(records, lambda rec: rec.city or "Unknown"),
        city_bias,
    )

    bias_eligible = [
        rec
        for rec in records
        if (_parse_record_date(rec) or date.min) >= FORECAST_BIAS_SINCE
        and rec.winning_temp
    ]
    om_eligible = [r for r in bias_eligible if _om_vs_result_delta(r) is not None]
    wu_eligible = [r for r in bias_eligible if _wu_vs_result_delta(r) is not None]

    return {
        "summary_by_city": summary_by_city,
        "city_forecast_bias": city_bias,
        "forecast_bias_since": FORECAST_BIAS_SINCE.isoformat(),
        "summary_by_buy_price_band": _group_metrics(records, lambda rec: _buy_price_band(rec.buy_price)),
        "summary_by_local_buy_time_band": _group_metrics(
            records, lambda rec: _local_time_band(rec.bought_at_local)
        ),
        "summary_by_win_temp_vs_bought": _group_metrics(
            records, lambda rec: rec.win_temp_vs_bought or "unknown"
        ),
        "summary_by_weekday": _group_metrics(records, lambda rec: _weekday_label(rec.date)),
        "summary_by_day": _group_metrics(records, lambda rec: _day_label(rec.date)),
        "summary_by_week": _group_metrics(records, lambda rec: _week_label(rec.date)),
        "summary_by_month": _group_metrics(records, lambda rec: _month_label(rec.date)),
        "summary_by_result": _group_metrics(records, lambda rec: rec.result or "unknown"),
        "summary_by_sold_outcome": _group_metrics(records, lambda rec: _sold_outcome_label(rec)),
        "summary_by_trade_window": _group_metrics(
            records, lambda rec: rec.trade_window or "unknown"
        ),
        "summary_by_roi_band": _group_metrics(records, lambda rec: _roi_band(rec)),
        "summary_by_spread_band": _group_metrics(
            records, lambda rec: _spread_band(rec.spread)
        ),
        "summary_by_edge": _group_metrics(
            records, lambda rec: _edge_label(rec.on_edge)
        ),
        "summary_by_competitive_band": _group_metrics(
            records, lambda rec: _competitive_band(rec.competitive)
        ),
        "summary_by_open_interest_band": _group_metrics(
            records, lambda rec: _open_interest_band(rec.open_interest)
        ),
        "summary_by_yes_gap_band": _group_metrics(
            records, lambda rec: _yes_gap_band(rec.yes_gap)
        ),
        "summary_by_loss_autopsy": _group_metrics(
            records, lambda rec: _loss_autopsy_label(rec.loss_autopsy)
        ),
        "summary_by_om_vs_result_band": _group_metrics(
            om_eligible,
            lambda rec: _forecast_vs_result_band(_om_vs_result_delta(rec)),
        ),
        "summary_by_wu_vs_result_band": _group_metrics(
            wu_eligible,
            lambda rec: _forecast_vs_result_band(_wu_vs_result_delta(rec)),
        ),
        "summary_by_city_timezone": _group_metrics(
            records, lambda rec: timezone_group(rec.city)
        ),
        # Same pool Lambda uses for daily timezone skip (buy/spread stack survivors).
        "summary_by_city_timezone_surviving": _group_metrics(
            _surviving_records_for_insights(records),
            lambda rec: timezone_group(rec.city),
        ),
        "stop_loss_regret_rate_pct": round((sold_regret / sold_count) * 100, 1)
        if sold_count
        else 0.0,
        "sold_would_lose_rate_pct": round((sold_would_lose / sold_count) * 100, 1)
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
