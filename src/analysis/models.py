"""Trade record model for analysis ledger."""

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class TradeRecord:
    date: str
    city: str
    bought_temp: str
    trade_window: str
    bought_at: str
    sold_at: Optional[str]
    redeemed_at: Optional[str]
    shares: float
    result: str
    final_value_usd: Optional[float]
    winning_temp: Optional[str]
    win_temp_vs_bought: str
    price_drop_below_threshold_at: Optional[str]
    sold_but_would_have_won: bool
    buy_price: float
    sell_price: Optional[float]
    cost_basis_usd: float
    realized_pnl_usd: Optional[float]
    roi_pct: Optional[float]
    sell_value_pct: Optional[float]
    held_hours: Optional[float]
    event_slug: str
    token_id: str
    condition_id: str
    transaction_hash: Optional[str]
    bought_at_hk: str = ""
    bought_at_local: str = ""
    sold_at_hk: str = ""
    price_drop_below_threshold_at_hk: str = ""
    share_count_target: int = 10
    shares_over_target: bool = False
    outcome_value_usd: Optional[float] = None
    spread: Optional[float] = None
    on_edge: Optional[bool] = None
    competitive: Optional[float] = None
    open_interest: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TradeSummary:
    total_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    sold_count: int = 0
    open_count: int = 0
    win_pct: float = 0.0
    total_cost_basis_usd: float = 0.0
    total_realized_pnl_usd: float = 0.0
    sold_but_would_have_won_count: int = 0
    avg_buy_usd: float = 0.0
    avg_buy_price: float = 0.0
    avg_spread: float = 0.0
    avg_pnl_usd: float = 0.0
    sold_win_count: int = 0
    sold_lose_count: int = 0
    win_plus_sold_win_count: int = 0
    win_plus_sold_win_pct: float = 0.0
    total_outcome_value_usd: float = 0.0
    avg_outcome_value_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _record_pnl_value(rec: TradeRecord) -> Optional[float]:
    if rec.realized_pnl_usd is not None:
        return rec.realized_pnl_usd
    return rec.final_value_usd


def _win_temp_is_same(rec: TradeRecord) -> bool:
    return rec.win_temp_vs_bought == "same"


def _win_temp_is_not_same(rec: TradeRecord) -> bool:
    """Final winner differs from bought bucket (higher/lower). Unknown is excluded."""
    return rec.win_temp_vs_bought in ("higher", "lower")


def _is_sold_win(rec: TradeRecord) -> bool:
    """Sold + P&L ≥ 0 + win vs bought = same → counts as win."""
    if rec.result != "sold":
        return False
    pnl = _record_pnl_value(rec)
    if pnl is None or pnl < 0:
        return False
    return _win_temp_is_same(rec)


def _is_sold_lose(rec: TradeRecord) -> bool:
    """Sold + P&L < 0 + win vs bought ≠ same → counts as lose."""
    if rec.result != "sold":
        return False
    pnl = _record_pnl_value(rec)
    if pnl is None or pnl >= 0:
        return False
    return _win_temp_is_not_same(rec)


def _is_sold_would_win(rec: TradeRecord) -> bool:
    """Sold + P&L < 0 + win vs bought = same → counts as lose (regret)."""
    if rec.result != "sold":
        return False
    pnl = _record_pnl_value(rec)
    if pnl is None or pnl >= 0:
        return False
    return _win_temp_is_same(rec)


def _is_sold_would_lose(rec: TradeRecord) -> bool:
    """Sold + P&L ≥ 0 + win vs bought ≠ same → counts as win."""
    if rec.result != "sold":
        return False
    pnl = _record_pnl_value(rec)
    if pnl is None or pnl < 0:
        return False
    return _win_temp_is_not_same(rec)


def _is_pnl_inferred_win(rec: TradeRecord) -> bool:
    """Sold + unknown win vs bought + P&L ≥ 0 counts toward win summary."""
    if rec.result != "sold":
        return False
    if rec.win_temp_vs_bought != "unknown":
        return False
    pnl = _record_pnl_value(rec)
    return pnl is not None and pnl >= 0


def _counts_toward_win_summary(rec: TradeRecord) -> bool:
    """True win, sold win, would lose, or sold unknown+pnl+. Would win and sold lose count as losses."""
    if rec.result == "win":
        return True
    if rec.result != "sold":
        return False
    return _is_sold_win(rec) or _is_sold_would_lose(rec) or _is_pnl_inferred_win(rec)


def recompute_sold_but_would_have_won(rec: TradeRecord) -> bool:
    """Keep the stored flag aligned with would-win classification."""
    return _is_sold_would_win(rec)


def compute_outcome_value(rec: TradeRecord) -> Optional[float]:
    """Cash returned for wins/sold; negative P&L for losses."""
    return compute_outcome_value_parts(
        result=rec.result,
        cost_basis_usd=rec.cost_basis_usd,
        pnl=_record_pnl_value(rec),
    )


def compute_outcome_value_parts(
    *,
    result: str,
    cost_basis_usd: float,
    pnl: Optional[float],
) -> Optional[float]:
    if pnl is None:
        return None
    if result == "loss":
        return round(pnl, 4)
    return round(cost_basis_usd + pnl, 4)


def summarize_records(records: list[TradeRecord]) -> TradeSummary:
    summary = TradeSummary(total_count=len(records))
    realized_total = 0.0
    has_realized = False
    pnl_count = 0
    buy_price_total = 0.0
    spread_total = 0.0
    spread_count = 0
    outcome_total = 0.0
    outcome_count = 0
    for rec in records:
        summary.total_cost_basis_usd += rec.cost_basis_usd
        buy_price_total += rec.buy_price
        if rec.spread is not None:
            spread_total += float(rec.spread)
            spread_count += 1
        if rec.result == "win":
            summary.win_count += 1
        elif rec.result == "loss":
            summary.loss_count += 1
        elif rec.result == "sold":
            summary.sold_count += 1
            if _is_sold_win(rec):
                summary.sold_win_count += 1
            elif _is_sold_lose(rec):
                summary.sold_lose_count += 1
        elif rec.result == "open":
            summary.open_count += 1
        if _is_sold_would_win(rec):
            summary.sold_but_would_have_won_count += 1
        pnl = _record_pnl_value(rec)
        if pnl is not None:
            realized_total += pnl
            has_realized = True
            pnl_count += 1
        outcome = rec.outcome_value_usd
        if outcome is None:
            outcome = compute_outcome_value(rec)
        if outcome is not None:
            outcome_total += float(outcome)
            outcome_count += 1

    settled = summary.win_count + summary.loss_count + summary.sold_count
    summary.win_pct = round((summary.win_count / settled) * 100, 1) if settled else 0.0
    summary.win_plus_sold_win_count = sum(1 for rec in records if _counts_toward_win_summary(rec))
    summary.win_plus_sold_win_pct = (
        round((summary.win_plus_sold_win_count / settled) * 100, 1) if settled else 0.0
    )
    summary.total_cost_basis_usd = round(summary.total_cost_basis_usd, 2)
    summary.avg_buy_usd = round(summary.total_cost_basis_usd / len(records), 2) if records else 0.0
    summary.avg_buy_price = round(buy_price_total / len(records), 3) if records else 0.0
    summary.avg_spread = round(spread_total / spread_count, 4) if spread_count else 0.0
    summary.total_outcome_value_usd = round(outcome_total, 2)
    summary.avg_outcome_value_usd = (
        round(outcome_total / outcome_count, 2) if outcome_count else 0.0
    )
    if has_realized:
        summary.total_realized_pnl_usd = round(realized_total, 2)
        summary.avg_pnl_usd = round(realized_total / pnl_count, 2) if pnl_count else 0.0
    return summary
