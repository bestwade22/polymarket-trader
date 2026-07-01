import json
import re
from typing import Any, List, Optional, Tuple


def parse_float(value: Any) -> Optional[float]:
    """Parse API/CLOB price fields; treat blank strings as missing."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def parse_json_field(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def get_outcome_prices(market: dict) -> Optional[list[float]]:
    try:
        outcome_prices = parse_json_field(market.get("outcomePrices", []))
        parsed = [parse_float(p) for p in outcome_prices]
        if not parsed or any(p is None for p in parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _get_outcome_yes_price(market: dict) -> Optional[float]:
    try:
        outcome_prices = parse_json_field(market.get("outcomePrices", []))
        outcomes = parse_json_field(market.get("outcomes", []))
        for i, outcome in enumerate(outcomes):
            if str(outcome).lower() == "yes":
                return parse_float(outcome_prices[i])
    except (TypeError, ValueError, IndexError):
        return None
    return None


def _get_book_midpoint(market: dict) -> Optional[float]:
    midpoint = parse_float(market.get("midpoint"))
    if midpoint is not None:
        return midpoint
    bid = parse_float(market.get("bestBid"))
    ask = parse_float(market.get("bestAsk"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return None


def get_book_price(market: dict, source: Optional[str] = None) -> Optional[float]:
    """Live CLOB book price only — no Gamma outcomePrices fallback."""
    from config.settings import ORDER_PRICE_SOURCES, settings

    key = (source or settings.selection_price_source).strip().lower()
    if key not in ORDER_PRICE_SOURCES:
        raise ValueError(
            f"Unknown book price source: {key!r}. Choose from {list(ORDER_PRICE_SOURCES)}"
        )
    if key == "midpoint":
        return _get_book_midpoint(market)
    if key == "buy_price":
        return parse_float(market.get("clobBuyPrice")) or parse_float(market.get("bestAsk"))
    if key == "best_bid":
        return parse_float(market.get("bestBid"))
    if key == "best_ask":
        return parse_float(market.get("bestAsk"))
    if key == "yes_price":
        return get_yes_price(market)
    return None


def get_selection_price(market: dict, source: Optional[str] = None) -> Optional[float]:
    """Price used to rank markets; defaults to live book per SELECTION_PRICE_SOURCE."""
    return get_book_price(market, source=source)


def get_yes_price(market: dict) -> Optional[float]:
    """Current Yes probability for ranking — matches Polymarket UI % column."""
    outcome = _get_outcome_yes_price(market)
    if outcome is not None:
        return outcome
    book_mid = _get_book_midpoint(market)
    if book_mid is not None:
        return book_mid
    return parse_float(market.get("lastTradePrice"))


def apply_live_prices(market: dict, live: dict) -> dict:
    """Merge CLOB live order book prices into a market dict."""
    updated = dict(market)
    if live.get("best_bid") is not None:
        updated["bestBid"] = live["best_bid"]
    if live.get("best_ask") is not None:
        updated["bestAsk"] = live["best_ask"]
    if live.get("clob_buy_price") is not None:
        updated["clobBuyPrice"] = live["clob_buy_price"]
    if live.get("midpoint") is not None:
        updated["midpoint"] = live["midpoint"]
    # lastTradePrice kept for logging only; get_yes_price uses outcomePrices / book midpoint
    return updated


def get_yes_token_id(market: dict) -> Optional[str]:
    try:
        token_ids = parse_json_field(market.get("clobTokenIds", []))
        return str(token_ids[0]) if token_ids else None
    except (TypeError, ValueError, IndexError):
        return None


def get_order_price(market: dict, source: Optional[str] = None) -> Optional[float]:
    """Limit price for buy orders; source from ORDER_PRICE_SOURCE env (default midpoint)."""
    from config.settings import ORDER_PRICE_SOURCES, settings

    key = (source or settings.order_price_source).strip().lower()
    if key not in ORDER_PRICE_SOURCES:
        raise ValueError(
            f"Unknown ORDER_PRICE_SOURCE: {key!r}. Choose from {list(ORDER_PRICE_SOURCES)}"
        )
    if key in ("midpoint", "buy_price", "best_bid", "best_ask"):
        return get_book_price(market, source=key)
    if key == "yes_price":
        return get_yes_price(market)
    return None


def get_buy_price(market: dict) -> Optional[float]:
    clob_buy = parse_float(market.get("clobBuyPrice"))
    if clob_buy is not None:
        return clob_buy
    best_ask = parse_float(market.get("bestAsk"))
    if best_ask is not None:
        return best_ask
    last_trade = parse_float(market.get("lastTradePrice"))
    if last_trade is not None:
        return last_trade
    return get_yes_price(market)


def get_sell_price(market: dict) -> Optional[float]:
    """Limit price for sell orders — midpoint first, then best bid."""
    midpoint = _get_book_midpoint(market)
    if midpoint is not None:
        return midpoint
    return parse_float(market.get("bestBid"))


def get_order_min_size(market: dict) -> int:
    return int(market.get("orderMinSize") or 5)


def get_tick_size(market: dict) -> str:
    tick = market.get("orderPriceMinTickSize")
    if tick is not None:
        return str(tick)
    return "0.01"


def is_neg_risk(market: dict) -> bool:
    return bool(market.get("negRisk", False))


def parse_temperature_bucket(group_item_title: str) -> Optional[Tuple[int, Optional[int], str]]:
    """Parse groupItemTitle into (low, high, unit). high is None for open-ended buckets."""
    title = (group_item_title or "").strip()
    unit = "F" if "°F" in title or "F" in title else "C"

    below_match = re.match(r"(\d+)[°]?[FC]\s+or\s+below", title, re.IGNORECASE)
    if below_match:
        return int(below_match.group(1)), None, unit

    above_match = re.match(r"(\d+)[°]?[FC]\s+or\s+higher", title, re.IGNORECASE)
    if above_match:
        return int(above_match.group(1)), None, unit

    range_match = re.match(r"(\d+)-(\d+)[°]?[FC]", title, re.IGNORECASE)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2)), unit

    return None


def market_price_snapshot(market: dict) -> dict:
    """All live price fields for a market, for logging and selection output."""
    best_bid = market.get("bestBid")
    best_ask = market.get("bestAsk")
    clob_buy = market.get("clobBuyPrice")
    last_trade = market.get("lastTradePrice")
    return {
        "yes_price": get_yes_price(market),
        "selection_price": get_selection_price(market),
        "buy_price": get_buy_price(market),
        "order_price": get_order_price(market),
        "best_bid": parse_float(best_bid),
        "best_ask": parse_float(best_ask),
        "clob_buy_price": parse_float(clob_buy),
        "last_trade_price": parse_float(last_trade),
        "midpoint": parse_float(market.get("midpoint")),
        "outcomePrices": get_outcome_prices(market),
    }


def match_temp_to_market(markets: List[dict], temp_f: int) -> Optional[dict]:
    """Map integer Fahrenheit to the matching temperature bucket market."""
    for market in markets:
        bucket = parse_temperature_bucket(market.get("groupItemTitle", ""))
        if not bucket:
            continue
        low, high, unit = bucket
        temp = temp_f if unit == "F" else round((temp_f - 32) * 5 / 9)

        title = market.get("groupItemTitle", "").lower()
        if "or below" in title and temp <= low:
            return market
        if "or higher" in title and temp >= low:
            return market
        if high is not None and low <= temp <= high:
            return market
    return None
