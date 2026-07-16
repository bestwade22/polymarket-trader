"""Detect whether the selected temp bucket is on the cool edge at order time.

A market is on the edge when every cooler temperature bucket in the same event
has Yes probability below 1% (< 0.01).
"""

from __future__ import annotations

from typing import Any, Optional

from src.utils.market_parser import (
    get_book_price,
    get_gamma_yes_price,
    get_yes_token_id,
    parse_float,
    parse_temperature_bucket,
    temp_bucket_sort_value,
)

EDGE_PRICE_THRESHOLD = 0.01


def market_yes_prob(market: dict[str, Any]) -> Optional[float]:
    """Best available Yes probability for edge checks (Gamma, then book mid)."""
    gamma = get_gamma_yes_price(market)
    if gamma is not None:
        return gamma
    mid = get_book_price(market, "midpoint")
    if mid is not None:
        return mid
    return parse_float(market.get("lastTradePrice"))


def cooler_markets(
    markets: list[dict[str, Any]],
    selected_title: str,
) -> Optional[list[dict[str, Any]]]:
    """Markets with a strictly cooler temp bucket than selected. None if unparseable."""
    selected = parse_temperature_bucket(selected_title)
    selected_val = temp_bucket_sort_value(selected)
    if selected_val is None:
        return None
    cooler: list[dict[str, Any]] = []
    for market in markets:
        title = str(market.get("groupItemTitle") or market.get("title") or "")
        bucket = parse_temperature_bucket(title)
        value = temp_bucket_sort_value(bucket)
        if value is not None and value < selected_val:
            cooler.append(market)
    return cooler


def is_on_edge(
    markets: list[dict[str, Any]],
    selected_title: str,
    *,
    threshold: float = EDGE_PRICE_THRESHOLD,
) -> Optional[bool]:
    """True when all cooler buckets are below threshold. Vacuous True if none cooler."""
    cooler = cooler_markets(markets, selected_title)
    if cooler is None:
        return None
    if not cooler:
        return True
    for market in cooler:
        price = market_yes_prob(market)
        if price is None:
            return None
        if price >= threshold:
            return False
    return True


def cooler_token_ids(
    markets: list[dict[str, Any]],
    selected_title: str,
) -> Optional[list[str]]:
    cooler = cooler_markets(markets, selected_title)
    if cooler is None:
        return None
    tokens: list[str] = []
    for market in cooler:
        token_id = get_yes_token_id(market)
        if token_id:
            tokens.append(str(token_id))
    return tokens
