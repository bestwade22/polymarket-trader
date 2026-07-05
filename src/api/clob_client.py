import logging
from typing import Any, Optional

import requests

from config.settings import CLOB_HOST
from src.utils.market_parser import parse_float

logger = logging.getLogger(__name__)


def _book_prices(entries: list[dict]) -> list[float]:
    prices = [parse_float(entry.get("price")) for entry in entries]
    return [price for price in prices if price is not None]


class ClobPriceClient:
    """Fetch live orderbook prices from Polymarket CLOB API."""

    def __init__(self, host: str = CLOB_HOST):
        self.host = host.rstrip("/")
        self.session = requests.Session()

    def get_order_book(self, token_id: str) -> Optional[dict[str, Any]]:
        try:
            resp = self.session.get(
                f"{self.host}/book",
                params={"token_id": token_id},
                timeout=15,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                return None
            return data
        except requests.RequestException as exc:
            logger.debug("CLOB book failed for token %s: %s", token_id, exc)
            return None

    def get_buy_price(self, token_id: str) -> Optional[float]:
        """Best price to buy Yes shares right now (lowest ask)."""
        book = self.get_order_book(token_id)
        if book and book.get("asks"):
            ask_prices = _book_prices(book["asks"])
            if ask_prices:
                return min(ask_prices)
        try:
            resp = self.session.get(
                f"{self.host}/price",
                params={"token_id": token_id, "side": "BUY"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                return None
            price = data.get("price")
            return parse_float(price)
        except (requests.RequestException, TypeError, ValueError) as exc:
            logger.warning("CLOB buy price failed for token %s: %s", token_id, exc)
            return None

    def get_midpoint(self, token_id: str) -> Optional[float]:
        try:
            resp = self.session.get(
                f"{self.host}/midpoint",
                params={"token_id": token_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                return None
            mid = data.get("mid")
            return parse_float(mid)
        except (requests.RequestException, TypeError, ValueError) as exc:
            logger.debug("CLOB midpoint failed for token %s: %s", token_id, exc)
            return None

    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        try:
            resp = self.session.get(
                f"{self.host}/last-trade-price",
                params={"token_id": token_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                return None
            price = data.get("price")
            return parse_float(price)
        except (requests.RequestException, TypeError, ValueError) as exc:
            logger.debug("CLOB last trade failed for token %s: %s", token_id, exc)
            return None

    def get_live_prices(self, token_id: str) -> dict[str, Optional[float]]:
        """Live bid/ask/trade prices from the CLOB order book."""
        book = self.get_order_book(token_id)
        best_bid = None
        best_ask = None
        last_trade = None

        if book:
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            bid_prices = _book_prices(bids)
            ask_prices = _book_prices(asks)
            if bid_prices:
                best_bid = max(bid_prices)
            if ask_prices:
                best_ask = min(ask_prices)
            last_trade = parse_float(book.get("last_trade_price"))

        if last_trade is None:
            last_trade = self.get_last_trade_price(token_id)

        midpoint = self.get_midpoint(token_id)
        buy_price = best_ask if best_ask is not None else self.get_buy_price(token_id)

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "clob_buy_price": buy_price,
            "last_trade_price": last_trade,
            "midpoint": midpoint,
        }

    def get_prices_history(
        self,
        token_id: str,
        *,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        interval: Optional[str] = None,
        fidelity: int = 60,
    ) -> list[dict[str, Any]]:
        """Historical price points: each {t: unix_seconds, p: price}."""
        params: dict[str, Any] = {"market": token_id, "fidelity": fidelity}
        if start_ts is not None and end_ts is not None:
            params["startTs"] = start_ts
            params["endTs"] = end_ts
        elif interval:
            params["interval"] = interval
        else:
            params["interval"] = "max"

        try:
            resp = self.session.get(
                f"{self.host}/prices-history",
                params=params,
                timeout=30,
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            history = data.get("history", data) if isinstance(data, dict) else data
            if not isinstance(history, list):
                return []
            return [point for point in history if isinstance(point, dict)]
        except requests.RequestException as exc:
            logger.debug("CLOB prices-history failed for token %s: %s", token_id, exc)
            return []


def first_price_below_threshold(
    history: list[dict[str, Any]],
    *,
    threshold: float,
    after_ts: int,
) -> Optional[int]:
    """First unix timestamp when price dropped below threshold after buy."""
    for point in sorted(history, key=lambda p: int(p.get("t") or 0)):
        ts = int(point.get("t") or 0)
        if ts < after_ts:
            continue
        price = parse_float(point.get("p"))
        if price is not None and price < threshold:
            return ts
    return None
