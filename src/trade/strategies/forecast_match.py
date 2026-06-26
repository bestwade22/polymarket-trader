import logging
from typing import Optional  # noqa: F401

from config.settings import settings
from src.api.weather_client import WeatherClient
from src.trade.strategies.base import BaseStrategy, MarketSelection
from src.utils.market_parser import (
    get_buy_price,
    get_order_min_size,
    get_tick_size,
    get_yes_price,
    get_yes_token_id,
    is_neg_risk,
    match_temp_to_market,
)

logger = logging.getLogger(__name__)


class ForecastMatchStrategy(BaseStrategy):
    name = "forecast_match"

    def __init__(self, share_count: Optional[int] = None, weather_client: Optional[WeatherClient] = None):
        self.share_count = share_count if share_count is not None else settings.share_count
        self.weather_client = weather_client or WeatherClient()

    def select_market(self, event: dict) -> Optional[MarketSelection]:
        city = event.get("city", "")
        event_date = event.get("event_date") or (event.get("endDateIso") or "")[:10]
        if not event_date:
            logger.warning("event=%s missing event_date", event.get("id"))
            return None

        resolution_source = event.get("resolutionSource")
        if not resolution_source and event.get("markets"):
            resolution_source = event["markets"][0].get("resolutionSource")

        forecast_f = self.weather_client.fetch_forecast_max_temp_f(
            city=city,
            event_date=event_date,
            resolution_source=resolution_source,
        )
        if forecast_f is None:
            logger.warning("event=%s no forecast for %s on %s", event.get("id"), city, event_date)
            return None

        market = match_temp_to_market(event.get("markets", []), forecast_f)
        if not market:
            logger.warning(
                "event=%s no bucket match for forecast %d°F",
                event.get("id"),
                forecast_f,
            )
            return None

        yes_price = get_yes_price(market) or 0.0
        yes_token = get_yes_token_id(market)
        buy_price = get_buy_price(market)
        if not yes_token or buy_price is None:
            return None

        return MarketSelection(
            event_id=str(event.get("id")),
            city=city,
            market_id=str(market.get("id")),
            group_item_title=market.get("groupItemTitle", ""),
            yes_price=yes_price,
            yes_token_id=yes_token,
            buy_price=buy_price,
            share_count=max(self.share_count, get_order_min_size(market)),
            neg_risk=is_neg_risk(market),
            tick_size=get_tick_size(market),
            order_min_size=get_order_min_size(market),
            strategy=self.name,
            forecast_temp_f=forecast_f,
            event=event,
            market=market,
        )
