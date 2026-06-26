import logging
from typing import Any, Optional

from config.settings import settings
from src.trade.executor import TradeExecutor, _import_clob
from src.trade.open_order_checker import collect_event_yes_token_ids
from src.utils.market_parser import get_yes_token_id, parse_float

logger = logging.getLogger(__name__)

CONDITIONAL_TOKEN_DECIMALS = 6
MIN_POSITION_SHARES = 0.01


def parse_conditional_balance(response: dict) -> float:
    """CLOB conditional token balance in shares."""
    raw = response.get("balance")
    if raw is None:
        return 0.0
    amount = parse_float(raw)
    if amount is None:
        return 0.0
    return amount / (10**CONDITIONAL_TOKEN_DECIMALS)


def _event_token_market_map(event: dict) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for market in event.get("markets", []):
        token_id = get_yes_token_id(market)
        if token_id:
            mapping[str(token_id)] = market
    return mapping


class LivePositionChecker:
    """Detect live Yes share holdings for markets in a city/event."""

    def __init__(self, executor: TradeExecutor):
        self.executor = executor
        self._balance_cache: dict[str, Optional[float]] = {}

    def get_yes_balance(self, token_id: str) -> Optional[float]:
        """Return Yes shares held, or None when live lookup is unavailable."""
        if not token_id or not settings.private_key:
            return None
        cached = self._balance_cache.get(token_id)
        if cached is not None or token_id in self._balance_cache:
            return cached
        try:
            version, _, _, _, _, _, _ = _import_clob()
            client = self.executor._get_client()
            if version == "v2":
                from py_clob_client_v2 import AssetType, BalanceAllowanceParams

                params = BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=str(token_id),
                )
            else:
                from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

                params = BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=str(token_id),
                )
            response = client.get_balance_allowance(params)
            if not isinstance(response, dict):
                logger.warning("Unexpected balance response for token %s: %r", token_id, response)
                self._balance_cache[token_id] = None
                return None
            balance = parse_conditional_balance(response)
            self._balance_cache[token_id] = balance
            return balance
        except Exception as exc:
            logger.warning("Live position check failed for token %s: %s", token_id, exc)
            self._balance_cache[token_id] = None
            return None

    def has_position(
        self,
        token_id: str,
        min_shares: float = MIN_POSITION_SHARES,
    ) -> tuple[bool, Optional[float]]:
        balance = self.get_yes_balance(token_id)
        if balance is None:
            return False, None
        return balance >= min_shares, balance

    def event_has_position(
        self,
        event: dict,
        min_shares: float = MIN_POSITION_SHARES,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """True when wallet holds Yes shares for any market in this event."""
        token_markets = _event_token_market_map(event)
        if not token_markets:
            return False, []

        holdings: list[dict[str, Any]] = []
        for token_id, market in token_markets.items():
            has_position, balance = self.has_position(token_id, min_shares=min_shares)
            if has_position:
                holdings.append(
                    {
                        "token_id": token_id,
                        "market_id": str(market.get("id", "")),
                        "group_item_title": market.get("groupItemTitle", ""),
                        "balance": balance,
                    }
                )
                break
        return bool(holdings), holdings


def filter_selections_without_position(
    selections: list,
    checker: LivePositionChecker,
) -> tuple[list, list[dict]]:
    """Drop selections whose city/event already holds Yes shares on any market."""
    kept = []
    skipped: list[dict] = []
    for sel in selections:
        event = sel.event
        if not event:
            kept.append(sel)
            continue
        event_id = str(event.get("id", sel.event_id))
        city = sel.city or event.get("city", "")
        has_position, holdings = checker.event_has_position(event)
        step_log = event.get("_step_logger")
        if has_position:
            logger.info(
                "event=%s city=%s has Yes position on %d market(s); skip",
                event_id,
                city,
                len(holdings),
            )
            if step_log:
                step_log.log_step(
                    "check_position",
                    has_position=True,
                    position_count=len(holdings),
                    positions=holdings,
                )
                step_log.save()
            skipped.append(
                {
                    "event_id": event_id,
                    "city": city,
                    "market_id": sel.market_id,
                    "group_item_title": sel.group_item_title,
                    "reason": "has_position",
                    "position_count": len(holdings),
                    "positions": holdings,
                }
            )
            continue
        if step_log:
            step_log.log_step("check_position", has_position=False)
        kept.append(sel)
    return kept, skipped


def filter_events_without_position(
    events: list[dict],
    checker: LivePositionChecker,
) -> tuple[list[dict], list[dict]]:
    """Drop events (cities) where the wallet already holds Yes shares on any market."""
    kept: list[dict] = []
    skipped: list[dict] = []
    for event in events:
        event_id = str(event.get("id"))
        city = event.get("city", "")
        has_position, holdings = checker.event_has_position(event)
        step_log = event.get("_step_logger")
        if has_position:
            logger.info(
                "event=%s city=%s has Yes position on %d market(s); skip",
                event_id,
                city,
                len(holdings),
            )
            if step_log:
                step_log.log_step(
                    "check_position",
                    has_position=True,
                    position_count=len(holdings),
                    positions=holdings,
                )
                step_log.save()
            skipped.append(
                {
                    "event_id": event_id,
                    "city": city,
                    "reason": "has_position",
                    "position_count": len(holdings),
                    "positions": holdings,
                }
            )
            continue
        if step_log:
            step_log.log_step("check_position", has_position=False)
        kept.append(event)
    return kept, skipped
