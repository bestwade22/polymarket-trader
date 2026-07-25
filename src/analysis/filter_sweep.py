"""Filter-stack sweep on trade history with walk-forward OOS check."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from src.analysis.models import (
    TradeRecord,
    _counts_toward_win_summary,
    _counts_toward_win_summary_denom,
    _record_pnl_value,
)
from src.analysis.strategy_insights import timezone_group
from src.trade.city_skip import lowest_win_summary_timezones


Predicate = Callable[[TradeRecord], bool]


@dataclass(frozen=True)
class FilterDef:
    name: str
    pred: Predicate


def _spread(rec: TradeRecord) -> float:
    return float(rec.spread) if rec.spread is not None else 999.0


def _buy(rec: TradeRecord) -> float:
    return float(rec.buy_price or 0.0)


def _oi(rec: TradeRecord) -> float:
    return float(rec.open_interest or 0.0)


def _metrics(records: list[TradeRecord]) -> dict[str, Any]:
    den = sum(1 for r in records if _counts_toward_win_summary_denom(r))
    num = sum(1 for r in records if _counts_toward_win_summary(r))
    pnl = 0.0
    for r in records:
        if r.result == "open":
            continue
        val = _record_pnl_value(r)
        if val is not None:
            pnl += float(val)
    ws = round(100.0 * num / den, 1) if den else 0.0
    return {
        "n": len(records),
        "denom": den,
        "win_summary": num,
        "win_summary_pct": ws,
        "pnl_usd": round(pnl, 2),
    }


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return datetime.min


def _split_oos(
    records: list[TradeRecord],
    *,
    train_frac: float = 0.7,
) -> tuple[list[TradeRecord], list[TradeRecord]]:
    """Split by date: earliest train_frac of distinct dates → train, rest → test."""
    dates = sorted({r.date for r in records if r.date}, key=_parse_date)
    if len(dates) < 4:
        cut = max(1, len(dates) - 1) if dates else 0
    else:
        cut = max(1, int(len(dates) * train_frac))
        cut = min(cut, len(dates) - 1)
    train_dates = set(dates[:cut])
    test_dates = set(dates[cut:])
    train = [r for r in records if r.date in train_dates]
    test = [r for r in records if r.date in test_dates]
    return train, test


def _bottom_tz_set(records: list[TradeRecord], n: int = 7) -> set[str]:
    return set(lowest_win_summary_timezones(records, bottom_n=n))


def build_filter_catalog(records: list[TradeRecord]) -> list[FilterDef]:
    """Named predicates; skip-bottom-7 TZ is computed from the provided records."""
    bottom7 = _bottom_tz_set(records, 7)

    def skip7(r: TradeRecord) -> bool:
        return timezone_group(r.city or "") not in bottom7

    catalog: list[FilterDef] = [
        FilterDef("all", lambda _r: True),
        FilterDef("skip_bottom7_tz", skip7),
        FilterDef("spread<0.10", lambda r: _spread(r) < 0.10),
        FilterDef("spread<0.08", lambda r: _spread(r) < 0.08),
        FilterDef("spread<0.05", lambda r: _spread(r) < 0.05),
        FilterDef("buy>=0.40", lambda r: _buy(r) >= 0.40),
        FilterDef("buy>=0.45", lambda r: _buy(r) >= 0.45),
        FilterDef("buy>=0.50", lambda r: _buy(r) >= 0.50),
        FilterDef("buy>=0.55", lambda r: _buy(r) >= 0.55),
        FilterDef("not_on_edge", lambda r: r.on_edge is False),
        FilterDef("oi>=10000", lambda r: _oi(r) >= 10000),
        FilterDef("yes_gap>=0.05", lambda r: (r.yes_gap or 0) >= 0.05),
        FilterDef("yes_gap>=0.10", lambda r: (r.yes_gap or 0) >= 0.10),
    ]
    return catalog


def _combine(name_parts: list[str], preds: list[Predicate]) -> FilterDef:
    def pred(r: TradeRecord) -> bool:
        return all(p(r) for p in preds)

    return FilterDef(" + ".join(name_parts), pred)


def candidate_stacks(records: list[TradeRecord]) -> list[FilterDef]:
    """Single filters + high-value combinations for ≥60% win-summary research."""
    cat = {f.name: f for f in build_filter_catalog(records)}
    stacks: list[FilterDef] = list(cat.values())

    combos: list[list[str]] = [
        ["skip_bottom7_tz", "spread<0.08"],
        ["skip_bottom7_tz", "spread<0.05"],
        ["skip_bottom7_tz", "buy>=0.45"],
        ["skip_bottom7_tz", "buy>=0.50"],
        ["skip_bottom7_tz", "not_on_edge"],
        ["spread<0.08", "buy>=0.45"],
        ["spread<0.08", "buy>=0.45", "not_on_edge"],
        ["spread<0.08", "buy>=0.50"],
        ["spread<0.05", "buy>=0.45"],
        ["spread<0.05", "buy>=0.50"],
        ["skip_bottom7_tz", "spread<0.08", "buy>=0.45"],
        ["skip_bottom7_tz", "spread<0.08", "buy>=0.45", "not_on_edge"],
        ["skip_bottom7_tz", "spread<0.05", "buy>=0.45"],
        ["skip_bottom7_tz", "spread<0.05", "buy>=0.50"],
        ["skip_bottom7_tz", "not_on_edge", "spread<0.08"],
        ["skip_bottom7_tz", "not_on_edge", "buy>=0.50"],
        ["not_on_edge", "buy>=0.45", "spread<0.08"],
        ["skip_bottom7_tz", "yes_gap>=0.05", "spread<0.08"],
        ["skip_bottom7_tz", "oi>=10000", "buy>=0.50"],
        ["spread<0.08", "buy>=0.45", "not_on_edge"],
    ]
    seen = {s.name for s in stacks}
    for names in combos:
        if not all(n in cat for n in names):
            continue
        combo = _combine(names, [cat[n].pred for n in names])
        if combo.name not in seen:
            stacks.append(combo)
            seen.add(combo.name)
    return stacks


def evaluate_stack(
    records: list[TradeRecord],
    filt: FilterDef,
    *,
    train: Optional[list[TradeRecord]] = None,
    test: Optional[list[TradeRecord]] = None,
) -> dict[str, Any]:
    if train is None or test is None:
        train, test = _split_oos(records)

    # Bottom-7 TZ for skip filters should be derived from train only for OOS honesty
    # when the filter name includes skip_bottom7_tz. Rebuild train-scoped skip if needed.
    pred = filt.pred
    if "skip_bottom7_tz" in filt.name:
        bottom7 = _bottom_tz_set(train, 7)

        def pred(r: TradeRecord, _inner=filt.pred, _b7=bottom7) -> bool:
            # Re-apply other clauses via filtering: for skip stacks, replace timezone check
            if timezone_group(r.city or "") in _b7:
                return False
            # If stack is only skip, done; else need remaining predicates from catalog on train
            # Simpler: apply original pred but with train bottom7 — rebuild from name parts
            return _pred_from_name_parts(filt.name, r, _b7)

    all_m = _metrics([r for r in records if pred(r)])
    train_m = _metrics([r for r in train if pred(r)])
    test_m = _metrics([r for r in test if pred(r)])
    return {
        "stack": filt.name,
        "n": all_m["n"],
        "denom": all_m["denom"],
        "win_summary_pct": all_m["win_summary_pct"],
        "pnl_usd": all_m["pnl_usd"],
        "train_denom": train_m["denom"],
        "train_win_summary_pct": train_m["win_summary_pct"],
        "train_pnl_usd": train_m["pnl_usd"],
        "oos_denom": test_m["denom"],
        "oos_win_summary_pct": test_m["win_summary_pct"],
        "oos_pnl_usd": test_m["pnl_usd"],
        "oos_pass_60": test_m["win_summary_pct"] >= 60.0 and test_m["denom"] >= 10,
    }


def _pred_from_name_parts(name: str, r: TradeRecord, bottom7: set[str]) -> bool:
    parts = [p.strip() for p in name.split("+")]
    for part in parts:
        if part == "skip_bottom7_tz":
            if timezone_group(r.city or "") in bottom7:
                return False
        elif part == "spread<0.10":
            if not (_spread(r) < 0.10):
                return False
        elif part == "spread<0.08":
            if not (_spread(r) < 0.08):
                return False
        elif part == "spread<0.05":
            if not (_spread(r) < 0.05):
                return False
        elif part == "buy>=0.40":
            if not (_buy(r) >= 0.40):
                return False
        elif part == "buy>=0.45":
            if not (_buy(r) >= 0.45):
                return False
        elif part == "buy>=0.50":
            if not (_buy(r) >= 0.50):
                return False
        elif part == "buy>=0.55":
            if not (_buy(r) >= 0.55):
                return False
        elif part == "not_on_edge":
            if r.on_edge is not False:
                return False
        elif part == "oi>=10000":
            if not (_oi(r) >= 10000):
                return False
        elif part == "yes_gap>=0.05":
            if not ((r.yes_gap or 0) >= 0.05):
                return False
        elif part == "yes_gap>=0.10":
            if not ((r.yes_gap or 0) >= 0.10):
                return False
        elif part == "all":
            continue
        else:
            # Unknown atom — fail closed
            return False
    return True


def compute_filter_sweep(
    records: list[TradeRecord],
    *,
    min_oos_denom: int = 10,
    target_ws: float = 60.0,
) -> dict[str, Any]:
    """Evaluate filter stacks; highlight loosest OOS-passing stack (≥ target_ws)."""
    train, test = _split_oos(records)
    rows: list[dict[str, Any]] = []
    for filt in candidate_stacks(records):
        # Always evaluate with train-scoped bottom7 via name parser for consistency
        bottom7 = _bottom_tz_set(train, 7)

        def pred(r: TradeRecord, _name=filt.name, _b7=bottom7) -> bool:
            if _name == "all":
                return True
            return _pred_from_name_parts(_name, r, _b7)

        # For non-skip filters that don't use name parser pieces correctly when name
        # has spaces around +, normalize
        name = " + ".join(p.strip() for p in filt.name.split("+"))

        def pred2(r: TradeRecord, _name=name, _b7=bottom7) -> bool:
            if _name == "all":
                return True
            return _pred_from_name_parts(_name, r, _b7)

        all_m = _metrics([r for r in records if pred2(r)])
        train_m = _metrics([r for r in train if pred2(r)])
        test_m = _metrics([r for r in test if pred2(r)])
        rows.append(
            {
                "stack": name,
                "n": all_m["n"],
                "denom": all_m["denom"],
                "win_summary_pct": all_m["win_summary_pct"],
                "pnl_usd": all_m["pnl_usd"],
                "train_denom": train_m["denom"],
                "train_win_summary_pct": train_m["win_summary_pct"],
                "train_pnl_usd": train_m["pnl_usd"],
                "oos_denom": test_m["denom"],
                "oos_win_summary_pct": test_m["win_summary_pct"],
                "oos_pnl_usd": test_m["pnl_usd"],
                "oos_pass_60": (
                    test_m["win_summary_pct"] >= target_ws
                    and test_m["denom"] >= min_oos_denom
                ),
            }
        )

    rows.sort(
        key=lambda r: (
            not r["oos_pass_60"],
            -r["oos_denom"],
            -r["win_summary_pct"],
            -r["pnl_usd"],
        )
    )

    oos_pass = [r for r in rows if r["oos_pass_60"]]
    # Loosest = most OOS denom among passers (then all-period denom, then PnL).
    recommended = None
    if oos_pass:
        recommended = max(
            oos_pass,
            key=lambda r: (r["oos_denom"], r["denom"], r["pnl_usd"]),
        )

    train_dates = sorted({r.date for r in train})
    test_dates = sorted({r.date for r in test})
    return {
        "target_win_summary_pct": target_ws,
        "train_dates": {"from": train_dates[0] if train_dates else None, "to": train_dates[-1] if train_dates else None, "n": len(train_dates)},
        "oos_dates": {"from": test_dates[0] if test_dates else None, "to": test_dates[-1] if test_dates else None, "n": len(test_dates)},
        "recommended": recommended,
        "stacks": rows,
    }
