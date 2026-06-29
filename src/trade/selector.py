import logging
from typing import Optional

from config.settings import settings
from src.trade.strategies.base import BaseStrategy, MarketSelection
from src.trade.strategies.forecast_match import ForecastMatchStrategy
from src.trade.strategies.highest_yes import HighestYesStrategy
from src.utils.time_window import is_event_tradable_now, next_trading_window_hint, trading_window_label

logger = logging.getLogger(__name__)

STRATEGIES: dict[str, type[BaseStrategy]] = {
    "highest_yes": HighestYesStrategy,
    "forecast_match": ForecastMatchStrategy,
}


def get_strategy(name: Optional[str] = None) -> BaseStrategy:
    strategy_name = (name or settings.strategy).lower()
    cls = STRATEGIES.get(strategy_name)
    if not cls:
        raise ValueError(f"Unknown strategy: {strategy_name}. Choose from {list(STRATEGIES)}")
    return cls()


def filter_tradable_events(events: list[dict], *, all_cities: bool = False) -> list[dict]:
    if all_cities:
        logger.info("Skipping noon window filter; treating all %d events as tradable", len(events))
        return list(events)

    tradable = []
    window_label = trading_window_label()
    for event in events:
        if is_event_tradable_now(event):
            tradable.append(event)
        else:
            logger.debug(
                "event=%s city=%s not in %s local window",
                event.get("id"),
                event.get("city"),
                window_label,
            )
    logger.info("Found %d tradable events in %s local window", len(tradable), window_label)
    if not tradable and events:
        from src.utils.time_window import next_trading_window_hint

        logger.info(
            "No cities in %s local window right now. %s "
            "Use --all-cities to trade every event for the date.",
            window_label,
            next_trading_window_hint(events),
        )
    return tradable


def select_markets_for_events(
    events: list[dict],
    strategy_name: Optional[str] = None,
) -> list[MarketSelection]:
    strategy = get_strategy(strategy_name)
    selections: list[MarketSelection] = []
    for event in events:
        selection = strategy.select_market(event)
        if selection:
            selections.append(selection)
    return selections


def filter_selections_after_live_refresh(
    selections: list[MarketSelection],
    strategy_name: Optional[str] = None,
) -> tuple[list[MarketSelection], list[dict]]:
    strategy = get_strategy(strategy_name)
    if hasattr(strategy, "filter_by_yes_price_max"):
        return strategy.filter_by_yes_price_max(selections)
    return selections, []
