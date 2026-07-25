"""Runner-up Yes % and gap (top − 2nd) for highest-yes strength."""

from __future__ import annotations

from typing import Any, Optional

from src.utils.market_parser import get_book_price, get_gamma_yes_price


def _market_yes(market: dict[str, Any]) -> Optional[float]:
    """Prefer Gamma Yes, then CLOB midpoint."""
    gamma = get_gamma_yes_price(market)
    if gamma is not None:
        return gamma
    mid = get_book_price(market, "midpoint")
    if mid is not None:
        return mid
    return None


def runner_up_yes_and_gap(
    markets: list[dict[str, Any]],
    *,
    selected_market_id: Optional[str] = None,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (top_yes, runner_up_yes, gap) across event markets.

    gap = top − runner_up. None when fewer than two priced markets.
    """
    priced: list[tuple[float, str]] = []
    for market in markets or []:
        price = _market_yes(market)
        if price is None:
            continue
        mid = str(market.get("id") or "")
        priced.append((float(price), mid))
    if not priced:
        return None, None, None
    priced.sort(key=lambda item: item[0], reverse=True)

    # If a selected market is provided and priced, use its price as top when it ranks #1;
    # otherwise still report global top/runner for gap strength.
    top_yes = priced[0][0]
    if selected_market_id:
        sel = str(selected_market_id)
        for price, mid in priced:
            if mid == sel:
                top_yes = price
                break

    if len(priced) < 2:
        return round(top_yes, 4), None, None

    # Runner-up = highest price among markets other than the top id
    top_id = priced[0][1]
    runner: Optional[float] = None
    for price, mid in priced:
        if mid != top_id:
            runner = price
            break
    if runner is None:
        return round(top_yes, 4), None, None
    gap = round(top_yes - runner, 4)
    return round(top_yes, 4), round(runner, 4), gap
