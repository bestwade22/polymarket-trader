from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from src.utils.market_parser import market_price_snapshot, parse_float


@dataclass
class MarketSelection:
    event_id: str
    city: str
    market_id: str
    group_item_title: str
    yes_price: float
    yes_token_id: str
    buy_price: float
    share_count: int
    neg_risk: bool
    tick_size: str
    order_min_size: int
    strategy: str
    forecast_temp_f: Optional[int] = None
    event: Optional[dict] = None
    market: Optional[dict] = None
    on_edge: Optional[bool] = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "event_id": self.event_id,
            "city": self.city,
            "market_id": self.market_id,
            "groupItemTitle": self.group_item_title,
            "yes_price": self.yes_price,
            "yes_token_id": self.yes_token_id,
            "buy_price": self.buy_price,
            "share_count": self.share_count,
            "neg_risk": self.neg_risk,
            "tick_size": self.tick_size,
            "order_min_size": self.order_min_size,
            "strategy": self.strategy,
            "forecast_temp_f": self.forecast_temp_f,
            "on_edge": self.on_edge,
        }
        if self.on_edge is None and self.event:
            from src.analysis.edge import is_on_edge

            data["on_edge"] = is_on_edge(
                self.event.get("markets") or [],
                self.group_item_title,
            )
        if self.market:
            prices = market_price_snapshot(self.market)
            data["order_price"] = prices["order_price"]
            data["selection_price"] = prices["selection_price"]
            data["gamma_yes_price"] = prices["yes_price"]
            data["best_bid"] = prices["best_bid"]
            data["best_ask"] = prices["best_ask"]
            data["spread"] = prices["spread"]
            data["clob_buy_price"] = prices["clob_buy_price"]
            data["last_trade_price"] = prices["last_trade_price"]
            data["midpoint"] = prices["midpoint"]
            data["outcomePrices"] = prices["outcomePrices"]
            data["competitive"] = parse_float(self.market.get("competitive"))
        if self.event:
            data["event_slug"] = self.event.get("slug")
            data["open_interest"] = parse_float(self.event.get("openInterest"))
        return data


class BaseStrategy(ABC):
    name: str

    @abstractmethod
    def select_market(self, event: dict) -> Optional[MarketSelection]:
        pass
