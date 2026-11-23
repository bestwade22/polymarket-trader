"""Shared context for sell-order (sell-win / stop-loss) Telegram rows.

Both sell paths know the held market inside its parent event, so they can
report the live market price and whether the held market is still the
highest-Yes bucket of that event.
"""

from __future__ import annotations

from typing import Any, Optional

from src.utils.market_parser import (
    get_gamma_yes_price,
    get_selection_price,
    get_yes_token_id,
)


def _market_yes_price(market: dict) -> Optional[float]:
    """Current Yes probability for a market (live book first, then Gamma)."""
    price = get_selection_price(market)
    if price is None:
        price = get_gamma_yes_price(market)
    return None if price is None else float(price)


def highest_yes_status(
    event: Optional[dict],
    token_id: str,
    *,
    market_id: str = "",
) -> Optional[bool]:
    """True when the held market is still the top-Yes bucket of its event.

    Mirrors the buy-side ranking in ``highest_yes`` (live book midpoint per
    ``SELECTION_PRICE_SOURCE``, Gamma Yes as fallback). Returns ``None`` when
    the event has no comparable markets, so callers can omit the field rather
    than claim a wrong answer.
    """
    if not event:
        return None
    markets = event.get("markets") or []
    if not markets:
        return None

    held_token = str(token_id or "")
    held_market_id = str(market_id or "")

    best_market: Optional[dict] = None
    best_price = float("-inf")
    held_price: Optional[float] = None

    for market in markets:
        price = _market_yes_price(market)
        if price is None:
            continue
        if best_price == float("-inf") or price > best_price:
            best_price = price
            best_market = market
        mid = str(market.get("id", ""))
        tid = get_yes_token_id(market)
        if (held_token and tid == held_token) or (held_market_id and mid == held_market_id):
            held_price = price

    if best_market is None:
        return None
    if held_price is None:
        # Held market not present in the event — cannot claim it is still top.
        return False

    best_token = get_yes_token_id(best_market)
    best_id = str(best_market.get("id", ""))
    return best_token == held_token or best_id == held_market_id


def sell_row_context(
    event: Optional[dict],
    market: dict,
    *,
    token_id: str = "",
    market_id: str = "",
) -> dict[str, Any]:
    """Context fields for a Telegram sell-order row."""
    current_mid = None
    if market:
        from src.utils.market_parser import get_sell_price

        current_mid = get_sell_price(market)
    return {
        "current_mid": current_mid,
        "is_highest_yes": highest_yes_status(
            event,
            token_id,
            market_id=market_id,
        ),
    }
