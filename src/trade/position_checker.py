import logging
import math
from typing import Any, Optional

from config.settings import settings
from src.trade.executor import TradeExecutor, _import_clob
from src.trade.open_order_checker import collect_event_yes_token_ids
from src.utils.market_parser import get_yes_token_id, parse_float

logger = logging.getLogger(__name__)

CONDITIONAL_TOKEN_DECIMALS = 6
MIN_POSITION_SHARES = 0.01


def compute_top_up_shares(
    held_balance: float,
    target_share_count: int,
    order_min_size: int,
) -> tuple[bool, int, str]:
    """Return (should_skip, order_shares, reason) for the selected market."""
    if held_balance >= target_share_count:
        return True, 0, "has_full_position"
    if held_balance >= MIN_POSITION_SHARES:
        needed = target_share_count - held_balance
        order_shares = max(math.ceil(needed), order_min_size)
        return False, int(order_shares), "partial_top_up"
    order_shares = max(target_share_count, order_min_size)
    return False, int(order_shares), "no_position"


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


def _holding_record(token_id: str, market: dict, balance: float) -> dict[str, Any]:
    return {
        "token_id": token_id,
        "market_id": str(market.get("id", "")),
        "group_item_title": market.get("groupItemTitle", ""),
        "balance": balance,
    }


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
            version, _, _, _, _, _, _, _ = _import_clob()
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
        full_position_share_count: Optional[float] = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """True when wallet holds enough Yes shares on any market in this event."""
        threshold = (
            full_position_share_count
            if full_position_share_count is not None
            else min_shares
        )
        token_markets = _event_token_market_map(event)
        if not token_markets:
            return False, []

        holdings: list[dict[str, Any]] = []
        for token_id, market in token_markets.items():
            balance = self.get_yes_balance(token_id)
            if balance is None or balance < threshold:
                continue
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


def event_position_holdings(
    event: dict,
    checker: LivePositionChecker,
    target_share_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Return (full_holdings, partial_holdings, balance_unavailable)."""
    full_holdings: list[dict[str, Any]] = []
    partial_holdings: list[dict[str, Any]] = []
    balance_unavailable = False

    for token_id, market in _event_token_market_map(event).items():
        balance = checker.get_yes_balance(token_id)
        if balance is None:
            balance_unavailable = True
            continue
        if balance >= target_share_count:
            full_holdings.append(_holding_record(token_id, market, balance))
        elif balance >= MIN_POSITION_SHARES:
            partial_holdings.append(_holding_record(token_id, market, balance))

    return full_holdings, partial_holdings, balance_unavailable


def _append_position_skip(
    skipped: list[dict],
    *,
    event_id: str,
    city: str,
    sel,
    reason: str,
    target_share_count: int,
    held_shares: Optional[float] = None,
    held_market_id: Optional[str] = None,
    held_group_item_title: Optional[str] = None,
) -> None:
    skipped.append(
        {
            "event_id": event_id,
            "city": city,
            "market_id": sel.market_id,
            "group_item_title": sel.group_item_title,
            "reason": reason,
            "held_shares": held_shares,
            "held_market_id": held_market_id,
            "held_group_item_title": held_group_item_title,
            "target_share_count": target_share_count,
        }
    )


def filter_selections_without_position(
    selections: list,
    checker: LivePositionChecker,
) -> tuple[list, list[dict]]:
    """Drop selections that already hold SHARE_COUNT Yes shares on any city market.

    When 0 < held shares < SHARE_COUNT, only top up when the selected market is the
    same market that already holds the partial position.
    """
    kept = []
    skipped: list[dict] = []
    target_share_count = settings.share_count
    for sel in selections:
        event = sel.event
        if not event:
            kept.append(sel)
            continue
        event_id = str(event.get("id", sel.event_id))
        city = sel.city or event.get("city", "")
        step_log = event.get("_step_logger")

        full_holdings, partial_holdings, balance_unavailable = event_position_holdings(
            event,
            checker,
            target_share_count,
        )
        if balance_unavailable and not full_holdings and not partial_holdings:
            if step_log:
                step_log.log_step("check_position", has_position=False, balance_unavailable=True)
            kept.append(sel)
            continue

        if full_holdings:
            held = full_holdings[0]
            reason = (
                "has_full_position"
                if held["market_id"] == sel.market_id
                else "has_full_position_other_market"
            )
            logger.info(
                "event=%s city=%s selected=%s holds %.2f shares on market %s (target %d); skip",
                event_id,
                city,
                sel.market_id,
                held["balance"],
                held["market_id"],
                target_share_count,
            )
            if step_log:
                step_log.log_step(
                    "check_position",
                    has_position=True,
                    held_shares=held["balance"],
                    held_market_id=held["market_id"],
                    target_share_count=target_share_count,
                    reason=reason,
                )
                step_log.save()
            _append_position_skip(
                skipped,
                event_id=event_id,
                city=city,
                sel=sel,
                reason=reason,
                target_share_count=target_share_count,
                held_shares=held["balance"],
                held_market_id=held["market_id"],
                held_group_item_title=held["group_item_title"],
            )
            continue

        if partial_holdings:
            held = partial_holdings[0]
            if held["market_id"] != sel.market_id:
                logger.info(
                    "event=%s city=%s partial %.2f shares on market %s but selected %s; skip",
                    event_id,
                    city,
                    held["balance"],
                    held["market_id"],
                    sel.market_id,
                )
                if step_log:
                    step_log.log_step(
                        "check_position",
                        has_position=True,
                        held_shares=held["balance"],
                        held_market_id=held["market_id"],
                        selected_market_id=sel.market_id,
                        target_share_count=target_share_count,
                        reason="partial_on_other_market",
                    )
                    step_log.save()
                _append_position_skip(
                    skipped,
                    event_id=event_id,
                    city=city,
                    sel=sel,
                    reason="partial_on_other_market",
                    target_share_count=target_share_count,
                    held_shares=held["balance"],
                    held_market_id=held["market_id"],
                    held_group_item_title=held["group_item_title"],
                )
                continue

            should_skip, order_shares, reason = compute_top_up_shares(
                held_balance=held["balance"],
                target_share_count=target_share_count,
                order_min_size=sel.order_min_size,
            )
            if should_skip:
                if step_log:
                    step_log.log_step(
                        "check_position",
                        has_position=True,
                        held_shares=held["balance"],
                        target_share_count=target_share_count,
                        reason=reason,
                    )
                    step_log.save()
                _append_position_skip(
                    skipped,
                    event_id=event_id,
                    city=city,
                    sel=sel,
                    reason=reason,
                    target_share_count=target_share_count,
                    held_shares=held["balance"],
                    held_market_id=held["market_id"],
                    held_group_item_title=held["group_item_title"],
                )
                continue

            logger.info(
                "event=%s city=%s market=%s partial position %.2f/%d; order %d shares",
                event_id,
                city,
                sel.market_id,
                held["balance"],
                target_share_count,
                order_shares,
            )
            sel.share_count = order_shares
            if step_log:
                step_log.log_step(
                    "check_position",
                    has_position=True,
                    held_shares=held["balance"],
                    target_share_count=target_share_count,
                    order_shares=sel.share_count,
                    reason=reason,
                )
            kept.append(sel)
            continue

        balance = checker.get_yes_balance(sel.yes_token_id)
        if balance is None:
            if step_log:
                step_log.log_step("check_position", has_position=False, balance_unavailable=True)
            kept.append(sel)
            continue

        should_skip, order_shares, reason = compute_top_up_shares(
            held_balance=balance,
            target_share_count=target_share_count,
            order_min_size=sel.order_min_size,
        )
        if should_skip:
            logger.info(
                "event=%s city=%s market=%s holds %.2f shares (target %d); skip",
                event_id,
                city,
                sel.market_id,
                balance,
                target_share_count,
            )
            if step_log:
                step_log.log_step(
                    "check_position",
                    has_position=True,
                    held_shares=balance,
                    target_share_count=target_share_count,
                    reason=reason,
                )
                step_log.save()
            _append_position_skip(
                skipped,
                event_id=event_id,
                city=city,
                sel=sel,
                reason=reason,
                target_share_count=target_share_count,
                held_shares=balance,
            )
            continue

        if step_log:
            step_log.log_step(
                "check_position",
                has_position=False,
                held_shares=balance,
                target_share_count=target_share_count,
                order_shares=sel.share_count,
                reason=reason,
            )
        kept.append(sel)
    return kept, skipped


def filter_events_without_position(
    events: list[dict],
    checker: LivePositionChecker,
) -> tuple[list[dict], list[dict]]:
    """Drop events where the wallet already holds SHARE_COUNT Yes shares on any market."""
    kept: list[dict] = []
    skipped: list[dict] = []
    target_share_count = settings.share_count
    for event in events:
        event_id = str(event.get("id"))
        city = event.get("city", "")
        has_position, holdings = checker.event_has_position(
            event,
            full_position_share_count=target_share_count,
        )
        step_log = event.get("_step_logger")
        if has_position:
            logger.info(
                "event=%s city=%s has full Yes position on %d market(s); skip",
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
                    target_share_count=target_share_count,
                )
                step_log.save()
            skipped.append(
                {
                    "event_id": event_id,
                    "city": city,
                    "reason": "has_full_position",
                    "position_count": len(holdings),
                    "positions": holdings,
                    "target_share_count": target_share_count,
                }
            )
            continue
        if step_log:
            step_log.log_step("check_position", has_position=False)
        kept.append(event)
    return kept, skipped
