import logging
from typing import Optional  # noqa: F401 used in __init__

from config.settings import settings
from src.trade.strategies.base import BaseStrategy, MarketSelection
from src.utils.market_parser import (
    get_buy_price,
    get_order_min_size,
    get_selection_price,
    get_tick_size,
    get_yes_token_id,
    is_neg_risk,
)

logger = logging.getLogger(__name__)


class HighestYesStrategy(BaseStrategy):
    name = "highest_yes"

    def __init__(self, yes_price_max: Optional[float] = None, share_count: Optional[int] = None):
        self.yes_price_max = yes_price_max if yes_price_max is not None else settings.yes_price_max
        self.share_count = share_count if share_count is not None else settings.share_count

    def select_market(self, event: dict) -> Optional[MarketSelection]:
        best_market = None
        best_selection_price = float("-inf")

        for market in event.get("markets", []):
            selection_price = get_selection_price(market)
            if selection_price is None:
                continue
            if selection_price > best_selection_price:
                best_selection_price = selection_price
                best_market = market

        if not best_market:
            logger.info("event=%s no markets with live book price", event.get("id"))
            return None

        yes_token = get_yes_token_id(best_market)
        buy_price = get_buy_price(best_market)
        if not yes_token or buy_price is None:
            return None

        return MarketSelection(
            event_id=str(event.get("id")),
            city=event.get("city", ""),
            market_id=str(best_market.get("id")),
            group_item_title=best_market.get("groupItemTitle", ""),
            yes_price=best_selection_price,
            yes_token_id=yes_token,
            buy_price=buy_price,
            share_count=max(self.share_count, get_order_min_size(best_market)),
            neg_risk=is_neg_risk(best_market),
            tick_size=get_tick_size(best_market),
            order_min_size=get_order_min_size(best_market),
            strategy=self.name,
            event=event,
            market=best_market,
        )

    def filter_by_yes_price_max(
        self,
        selections: list[MarketSelection],
    ) -> tuple[list[MarketSelection], list[dict]]:
        """Drop selections whose live selection price is at or above YES_PRICE_MAX."""
        kept: list[MarketSelection] = []
        skipped: list[dict] = []
        for sel in selections:
            if sel.yes_price is None or sel.yes_price >= self.yes_price_max:
                logger.info(
                    "event=%s live selection %.3f >= max %.3f; skip",
                    sel.event_id,
                    sel.yes_price if sel.yes_price is not None else float("nan"),
                    self.yes_price_max,
                )
                step_log = sel.event.get("_step_logger") if sel.event else None
                if step_log:
                    step_log.log_step(
                        "filter_yes_price_max",
                        skipped=True,
                        selection_price=sel.yes_price,
                        yes_price_max=self.yes_price_max,
                        market_id=sel.market_id,
                    )
                skipped.append(
                    {
                        "event_id": sel.event_id,
                        "city": sel.city,
                        "market_id": sel.market_id,
                        "group_item_title": sel.group_item_title,
                        "reason": "yes_price_max",
                        "selection_price": sel.yes_price,
                        "yes_price_max": self.yes_price_max,
                    }
                )
                continue
            kept.append(sel)
        return kept, skipped
