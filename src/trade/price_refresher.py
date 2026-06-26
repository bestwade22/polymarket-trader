import logging
from datetime import datetime, timezone
from typing import Optional

from src.api.clob_client import ClobPriceClient
from src.api.gamma_client import GammaClient
from src.trade.strategies.base import MarketSelection
from src.utils.market_parser import apply_live_prices, get_yes_token_id

logger = logging.getLogger(__name__)

ENRICHMENT_FIELDS = (
    "city",
    "timezone",
    "utc_offset_seconds",
    "city_noon_local",
    "city_noon_utc",
    "event_date",
    "fetched_at",
)


def _merge_event(cached: dict, fresh: dict) -> dict:
    """Keep local enrichment; replace markets and other live Gamma fields."""
    merged = dict(fresh)
    for field in ENRICHMENT_FIELDS:
        if field in cached:
            merged[field] = cached[field]
    for field in ("_step_logger",):
        if field in cached:
            merged[field] = cached[field]
    merged["prices_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    return merged


def refresh_market_prices(
    market: dict,
    gamma: Optional[GammaClient] = None,
    clob: Optional[ClobPriceClient] = None,
) -> dict:
    """Fetch latest prices for a single market from Gamma + CLOB."""
    gamma = gamma or GammaClient()
    clob = clob or ClobPriceClient()
    market_id = str(market.get("id", ""))

    fresh = gamma.fetch_market_by_id(market_id) if market_id else None
    updated = dict(fresh if fresh else market)

    token_id = get_yes_token_id(updated)
    if token_id:
        live = clob.get_live_prices(token_id)
        updated = apply_live_prices(updated, live)

    updated["prices_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    return updated


def refresh_event_markets(
    event: dict,
    gamma: Optional[GammaClient] = None,
    clob: Optional[ClobPriceClient] = None,
    use_clob: bool = True,
) -> dict:
    gamma = gamma or GammaClient()
    clob = clob or ClobPriceClient()
    event_id = str(event.get("id", ""))

    fresh = gamma.fetch_event_by_id(event_id)
    if not fresh or not fresh.get("markets"):
        logger.warning("Could not refresh event %s from Gamma API", event_id)
        return event

    merged = _merge_event(event, fresh)
    refreshed_markets = []
    for market in merged.get("markets", []):
        try:
            if use_clob:
                refreshed_markets.append(refresh_market_prices(market, gamma=gamma, clob=clob))
            else:
                refreshed_markets.append(dict(market))
        except Exception as exc:
            logger.warning(
                "Price refresh failed for market %s in event %s: %s",
                market.get("id"),
                event_id,
                exc,
            )
            refreshed_markets.append(dict(market))
    merged["markets"] = refreshed_markets

    logger.info(
        "Refreshed event %s (%s): %d markets",
        event_id,
        merged.get("city", "?"),
        len(refreshed_markets),
    )
    return merged


def refresh_events_markets(events: list[dict], use_clob: bool = True) -> list[dict]:
    if not events:
        return events

    gamma = GammaClient()
    clob = ClobPriceClient()
    refreshed: list[dict] = []

    for event in events:
        try:
            updated = refresh_event_markets(event, gamma=gamma, clob=clob, use_clob=use_clob)
            refreshed.append(updated)
        except Exception as exc:
            logger.warning("Price refresh failed for event %s: %s", event.get("id"), exc)
            refreshed.append(event)

    return refreshed


def refresh_selection_prices(
    selections: list[MarketSelection],
) -> list[MarketSelection]:
    """Re-fetch live prices for selected markets right before save/order."""
    if not selections:
        return selections

    gamma = GammaClient()
    clob = ClobPriceClient()
    updated: list[MarketSelection] = []

    for sel in selections:
        if not sel.market:
            updated.append(sel)
            continue
        fresh_market = refresh_market_prices(sel.market, gamma=gamma, clob=clob)
        from src.utils.market_parser import get_buy_price, get_order_price, get_selection_price

        sel.market = fresh_market
        sel.yes_price = get_selection_price(fresh_market) or sel.yes_price
        sel.buy_price = get_buy_price(fresh_market) or sel.buy_price
        order_price = get_order_price(fresh_market)
        updated.append(sel)
        logger.info(
            "Live prices market %s (%s): sel=%.3f order=%s bid=%s ask=%s mid=%s",
            sel.market_id,
            sel.group_item_title,
            sel.yes_price,
            f"{order_price:.3f}" if order_price is not None else None,
            fresh_market.get("bestBid"),
            fresh_market.get("bestAsk"),
            fresh_market.get("midpoint"),
        )

    return updated
