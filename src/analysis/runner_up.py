"""Runner-up Yes % and gap (top − 2nd) for highest-yes strength."""

from __future__ import annotations

from typing import Any, Optional

from src.utils.market_parser import (
    extract_temp_label,
    get_book_price,
    get_gamma_yes_price,
    get_yes_token_id,
)


def _market_yes(market: dict[str, Any]) -> Optional[float]:
    """Prefer Gamma Yes, then CLOB midpoint."""
    gamma = get_gamma_yes_price(market)
    if gamma is not None:
        return gamma
    mid = get_book_price(market, "midpoint")
    if mid is not None:
        return mid
    return None


def _market_temp(market: dict[str, Any]) -> str:
    title = market.get("groupItemTitle") or market.get("group_item_title") or ""
    return extract_temp_label(str(title)) or str(title).strip()


def runner_up_details(
    markets: list[dict[str, Any]],
    *,
    selected_market_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return top/runner prices, gap, and runner-up temp label.

    Keys: top_yes, runner_up_yes, yes_gap, runner_up_temp, runner_up_token_id,
    runner_up_market_id.
    """
    empty = {
        "top_yes": None,
        "runner_up_yes": None,
        "yes_gap": None,
        "runner_up_temp": None,
        "runner_up_token_id": None,
        "runner_up_market_id": None,
    }
    priced: list[tuple[float, str, dict[str, Any]]] = []
    for market in markets or []:
        if not isinstance(market, dict):
            continue
        price = _market_yes(market)
        if price is None:
            continue
        mid = str(market.get("id") or "")
        priced.append((float(price), mid, market))
    if not priced:
        return empty
    priced.sort(key=lambda item: item[0], reverse=True)

    top_yes = priced[0][0]
    if selected_market_id:
        sel = str(selected_market_id)
        for price, mid, _market in priced:
            if mid == sel:
                top_yes = price
                break

    if len(priced) < 2:
        return {
            **empty,
            "top_yes": round(top_yes, 4),
        }

    # Runner-up = highest price among markets other than the global top id
    top_id = priced[0][1]
    runner_row: Optional[tuple[float, str, dict[str, Any]]] = None
    for price, mid, market in priced:
        if mid != top_id:
            runner_row = (price, mid, market)
            break
    if runner_row is None:
        return {
            **empty,
            "top_yes": round(top_yes, 4),
        }

    runner_price, runner_id, runner_market = runner_row
    gap = round(top_yes - runner_price, 4)
    token = get_yes_token_id(runner_market)
    return {
        "top_yes": round(top_yes, 4),
        "runner_up_yes": round(runner_price, 4),
        "yes_gap": gap,
        "runner_up_temp": _market_temp(runner_market) or None,
        "runner_up_token_id": str(token) if token else None,
        "runner_up_market_id": runner_id or None,
    }


def runner_up_yes_and_gap(
    markets: list[dict[str, Any]],
    *,
    selected_market_id: Optional[str] = None,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (top_yes, runner_up_yes, gap) across event markets.

    gap = top − runner_up. None when fewer than two priced markets.
    """
    details = runner_up_details(markets, selected_market_id=selected_market_id)
    return details["top_yes"], details["runner_up_yes"], details["yes_gap"]
