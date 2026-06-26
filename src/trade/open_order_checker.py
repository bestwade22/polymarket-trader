import logging
from typing import Any, Optional

from config.settings import settings
from src.trade.executor import TradeExecutor, _import_clob
from src.utils.market_parser import get_yes_token_id

logger = logging.getLogger(__name__)


def collect_event_yes_token_ids(event: dict) -> set[str]:
    """All Yes CLOB token IDs for markets under one city/event."""
    token_ids: set[str] = set()
    for market in event.get("markets", []):
        token_id = get_yes_token_id(market)
        if token_id:
            token_ids.add(str(token_id))
    return token_ids


def _order_asset_id(order: dict[str, Any]) -> Optional[str]:
    raw = order.get("asset_id") or order.get("token_id") or order.get("tokenId")
    return str(raw) if raw is not None else None


def _is_buy_order(order: dict[str, Any]) -> bool:
    side = str(order.get("side", "")).upper()
    return side in ("BUY", "B", "0")


class LiveOpenOrderChecker:
    """Detect open CLOB buy orders for markets in a city/event."""

    def __init__(self, executor: TradeExecutor):
        self.executor = executor
        self._orders: Optional[list[dict[str, Any]]] = None

    def get_open_orders(self) -> list[dict[str, Any]]:
        if self._orders is not None:
            return self._orders
        if not settings.private_key:
            self._orders = []
            return self._orders
        try:
            version, _, _, _, _, _, _ = _import_clob()
            client = self.executor._get_client()
            if version == "v2":
                orders = client.get_open_orders()
            else:
                orders = client.get_orders()  # type: ignore[attr-defined]
            self._orders = orders if isinstance(orders, list) else []
        except Exception as exc:
            logger.warning("Failed to fetch open orders: %s", exc)
            self._orders = []
        return self._orders

    def event_has_open_order(self, event: dict) -> tuple[bool, list[dict[str, Any]]]:
        """True when any open buy order targets a Yes token in this event."""
        token_ids = collect_event_yes_token_ids(event)
        if not token_ids:
            return False, []

        matching: list[dict[str, Any]] = []
        for order in self.get_open_orders():
            asset_id = _order_asset_id(order)
            if asset_id and asset_id in token_ids and _is_buy_order(order):
                matching.append(order)
        return bool(matching), matching


def filter_events_without_open_orders(
    events: list[dict],
    checker: LiveOpenOrderChecker,
) -> tuple[list[dict], list[dict]]:
    """Drop events (cities) that already have an open buy order on any market."""
    kept: list[dict] = []
    skipped: list[dict] = []
    for event in events:
        event_id = str(event.get("id"))
        city = event.get("city", "")
        has_open, orders = checker.event_has_open_order(event)
        step_log = event.get("_step_logger")
        if has_open:
            order_ids = [
                o.get("id") or o.get("orderID") or o.get("order_id") for o in orders
            ]
            logger.info(
                "event=%s city=%s has %d open buy order(s); skip",
                event_id,
                city,
                len(orders),
            )
            if step_log:
                step_log.log_step(
                    "check_open_orders",
                    has_open_order=True,
                    open_order_count=len(orders),
                    open_order_ids=[oid for oid in order_ids if oid],
                )
                step_log.save()
            skipped.append(
                {
                    "event_id": event_id,
                    "city": city,
                    "reason": "open_order",
                    "open_order_count": len(orders),
                    "open_order_ids": [oid for oid in order_ids if oid],
                }
            )
            continue
        if step_log:
            step_log.log_step("check_open_orders", has_open_order=False)
        kept.append(event)
    return kept, skipped
