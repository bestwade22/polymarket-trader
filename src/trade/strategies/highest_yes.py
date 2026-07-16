import logging
from typing import Optional  # noqa: F401 used in __init__

from config.settings import settings
from src.trade.strategies.base import BaseStrategy, MarketSelection
from src.utils.market_parser import (
    get_book_price,
    get_buy_price,
    get_gamma_yes_price,
    get_order_min_size,
    get_selection_price,
    get_tick_size,
    get_yes_token_id,
    is_neg_risk,
)

logger = logging.getLogger(__name__)


def _best_market_by(markets: list[dict], price_fn) -> tuple[Optional[dict], Optional[float]]:
    best_market = None
    best_price = float("-inf")
    for market in markets:
        price = price_fn(market)
        if price is None:
            continue
        if price > best_price:
            best_price = price
            best_market = market
    if best_market is None:
        return None, None
    return best_market, best_price


class HighestYesStrategy(BaseStrategy):
    name = "highest_yes"

    def __init__(self, yes_price_max: Optional[float] = None, share_count: Optional[int] = None):
        self.yes_price_max = yes_price_max if yes_price_max is not None else settings.yes_price_max
        self.share_count = share_count if share_count is not None else settings.share_count

    def select_market(self, event: dict) -> Optional[MarketSelection]:
        """Select only when CLOB midpoint and Gamma Yes agree on the same top market."""
        markets = event.get("markets", [])
        event_id = event.get("id")
        city = event.get("city", "")

        mid_market, mid_price = _best_market_by(
            markets, lambda m: get_book_price(m, "midpoint")
        )
        gamma_market, gamma_price = _best_market_by(markets, get_gamma_yes_price)

        if not mid_market:
            logger.info("event=%s city=%s no markets with CLOB midpoint", event_id, city)
            return None
        if not gamma_market:
            logger.info("event=%s city=%s no markets with Gamma yes price", event_id, city)
            return None

        mid_id = str(mid_market.get("id", ""))
        gamma_id = str(gamma_market.get("id", ""))
        if mid_id != gamma_id:
            logger.info(
                "event=%s city=%s highest_yes disagree: "
                "clob_mid=%s (%s %.3f) gamma=%s (%s %.3f); skip",
                event_id,
                city,
                mid_id,
                mid_market.get("groupItemTitle", ""),
                mid_price,
                gamma_id,
                gamma_market.get("groupItemTitle", ""),
                gamma_price,
            )
            step_log = event.get("_step_logger")
            if step_log:
                step_log.log_step(
                    "select_market",
                    skipped=True,
                    reason="clob_gamma_disagree",
                    clob_mid_market_id=mid_id,
                    clob_mid_title=mid_market.get("groupItemTitle", ""),
                    clob_mid_price=mid_price,
                    gamma_market_id=gamma_id,
                    gamma_title=gamma_market.get("groupItemTitle", ""),
                    gamma_price=gamma_price,
                )
            return None

        best_market = mid_market
        selection_price = get_selection_price(best_market)
        if selection_price is None:
            selection_price = mid_price

        yes_token = get_yes_token_id(best_market)
        buy_price = get_buy_price(best_market)
        if not yes_token or buy_price is None:
            return None

        return MarketSelection(
            event_id=str(event.get("id")),
            city=event.get("city", ""),
            market_id=str(best_market.get("id")),
            group_item_title=best_market.get("groupItemTitle", ""),
            yes_price=selection_price,
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
