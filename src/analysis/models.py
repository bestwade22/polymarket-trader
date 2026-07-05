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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_records(records: list[TradeRecord]) -> TradeSummary:
    summary = TradeSummary(total_count=len(records))
    realized_total = 0.0
    has_realized = False
    for rec in records:
        summary.total_cost_basis_usd += rec.cost_basis_usd
        if rec.result == "win":
            summary.win_count += 1
        elif rec.result == "loss":
            summary.loss_count += 1
        elif rec.result == "sold":
            summary.sold_count += 1
        elif rec.result == "open":
            summary.open_count += 1
        if rec.sold_but_would_have_won:
            summary.sold_but_would_have_won_count += 1
        if rec.realized_pnl_usd is not None:
            realized_total += rec.realized_pnl_usd
            has_realized = True
        elif rec.final_value_usd is not None:
            realized_total += rec.final_value_usd
            has_realized = True

    settled = summary.win_count + summary.loss_count + summary.sold_count
    summary.win_pct = round((summary.win_count / settled) * 100, 1) if settled else 0.0
    summary.total_cost_basis_usd = round(summary.total_cost_basis_usd, 2)
    if has_realized:
        summary.total_realized_pnl_usd = round(realized_total, 2)
    return summary
