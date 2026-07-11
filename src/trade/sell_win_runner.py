"""Periodic sell-win check over live wallet positions."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import ensure_dirs, settings
from src.api.data_client import LivePosition, fetch_user_positions
from src.api.gamma_client import GammaClient
from src.trade.executor import TradeExecutor
from src.trade.open_order_checker import LiveOpenOrderChecker
from src.trade.position_checker import MIN_POSITION_SHARES
from src.trade.position_tracker import SoldPositionTracker
from src.trade.price_refresher import refresh_market_prices
from src.trade.sell_win import (
    active_sell_win_tier,
    is_sell_win_eligible_event,
    is_sell_win_price_eligible,
    sell_win_expiration_utc,
    sell_win_order_price,
)
from src.trade.stop_loss_runner import (
    _find_market_in_event,
    resolve_event_for_position,
    selection_from_position,
)
from src.utils.market_parser import get_sell_price

logger = logging.getLogger(__name__)


def _selection_for_sell_win(
    event: dict,
    market: dict,
    position: LivePosition,
    share_count: float,
):
    selection = selection_from_position(event, market, position, share_count)
    selection.strategy = "sell_win"
    return selection


def _log_sell_order_placed(
    position: LivePosition,
    order_result: dict[str, Any],
    *,
    tier: str,
    floor_price: float,
    current_mid: float,
) -> None:
    logger.info(
        "sell-win order placed: tier=%s event_slug=%s market=%s token=%s order_id=%s "
        "floor=%.4f mid=%.4f price=%.4f size=%s dry_run=%s expires_at=%s",
        tier,
        position.event_slug,
        position.market_id,
        position.token_id[:16],
        order_result.get("order_id") or "n/a",
        floor_price,
        current_mid,
        float(order_result.get("price") or 0),
        order_result.get("size"),
        order_result.get("dry_run"),
        order_result.get("expires_at") or "n/a",
    )


def _log_run_summary(result: dict[str, Any]) -> None:
    placed = result.get("placed", [])
    if placed:
        for entry in placed:
            order = entry.get("order") or {}
            logger.info(
                "sell-win run placed: tier=%s event_slug=%s market=%s order_id=%s "
                "price=%s size=%s dry_run=%s expires_at=%s",
                entry.get("tier"),
                entry.get("event_slug"),
                entry.get("market_id"),
                order.get("order_id") or "n/a",
                order.get("price"),
                order.get("size"),
                order.get("dry_run"),
                order.get("expires_at") or "n/a",
            )
    else:
        logger.info("sell-win run placed: none")

    logger.info(
        "Sell-win check complete: status=%s dry_run=%s positions_loaded=%d "
        "positions_checked=%d placed_count=%d skipped_count=%d error_count=%d",
        result.get("status"),
        result.get("dry_run"),
        result.get("positions_loaded"),
        result.get("positions_checked"),
        len(placed),
        len(result.get("skipped", [])),
        len(result.get("errors", [])),
    )


def run_sell_win_check(
    dry_run: Optional[bool] = None,
    wallet_address: Optional[str] = None,
) -> dict[str, Any]:
    ensure_dirs()
    executor = TradeExecutor(dry_run=dry_run)
    open_order_checker = LiveOpenOrderChecker(executor)
    sell_tracker = SoldPositionTracker()
    gamma = GammaClient()

    wallet = wallet_address or settings.deposit_wallet_address
    if not wallet:
        return {"status": "error", "reason": "missing_wallet", "positions_checked": 0}

    try:
        positions = fetch_user_positions(wallet)
    except Exception as exc:
        logger.exception("Failed to fetch positions")
        return {"status": "error", "reason": str(exc), "positions_checked": 0}

    if not positions:
        return {
            "status": "skipped",
            "reason": "no_positions",
            "positions_checked": 0,
            "skipped": [],
            "placed": [],
            "errors": [],
        }

    skipped: list[dict[str, Any]] = []
    placed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    checked = 0

    for position in positions:
        if position.size < MIN_POSITION_SHARES:
            continue

        if not is_sell_win_eligible_event(
            event_slug=position.event_slug,
            title=position.title,
        ):
            logger.info(
                "sell-win skip: token=%s event_slug=%s reason=not_temp_market",
                position.token_id[:16],
                position.event_slug,
            )
            skipped.append(
                {
                    "market_id": position.market_id,
                    "token_id": position.token_id,
                    "event_slug": position.event_slug,
                    "reason": "not_temp_market",
                }
            )
            continue

        try:
            event = resolve_event_for_position(position, gamma=gamma)
            if not event:
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "reason": "event_not_found",
                    }
                )
                continue

            if not is_sell_win_eligible_event(
                event_slug=position.event_slug,
                title=str(event.get("title", "")),
                slug=str(event.get("slug", "")),
            ):
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "reason": "not_temp_market",
                    }
                )
                continue

            tier, tier_reason = active_sell_win_tier(event)
            if tier is None:
                logger.info(
                    "sell-win skip: market=%s token=%s event_slug=%s reason=%s",
                    position.market_id,
                    position.token_id[:16],
                    position.event_slug,
                    tier_reason,
                )
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "event_slug": position.event_slug,
                        "reason": tier_reason,
                    }
                )
                continue

            market = _find_market_in_event(event, position.token_id, position.market_id)
            if not market:
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "reason": "market_not_in_event",
                    }
                )
                continue

            market = refresh_market_prices(market, gamma=gamma)
            current_mid = get_sell_price(market)
            if current_mid is None:
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "reason": "no_midpoint",
                    }
                )
                continue

            if not is_sell_win_price_eligible(current_mid):
                logger.info(
                    "sell-win skip: market=%s token=%s event_slug=%s reason=price_too_low mid=%.4f",
                    position.market_id,
                    position.token_id[:16],
                    position.event_slug,
                    current_mid,
                )
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "event_slug": position.event_slug,
                        "reason": "price_too_low",
                        "current_mid": current_mid,
                    }
                )
                continue

            expiration_ts = sell_win_expiration_utc(event, tier)
            if expiration_ts is None:
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "event_slug": position.event_slug,
                        "reason": "past_tier_expiry",
                        "tier": tier.name,
                    }
                )
                continue

            checked += 1
            order_price = sell_win_order_price(tier.floor_price, current_mid)
            expires_at = datetime.fromtimestamp(expiration_ts, tz=timezone.utc).isoformat()

            logger.info(
                "sell-win check: tier=%s event_slug=%s market=%s size=%s "
                "floor=%.4f mid=%.4f order_price=%.4f expires_at=%s",
                tier.name,
                position.event_slug,
                position.market_id,
                position.size,
                tier.floor_price,
                current_mid,
                order_price,
                expires_at,
            )

            has_open_sell, open_sell_orders = open_order_checker.token_has_open_sell_order(
                position.token_id
            )
            if has_open_sell:
                order_ids = [
                    o.get("id") or o.get("orderID") or o.get("order_id")
                    for o in open_sell_orders
                ]
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "event_slug": position.event_slug,
                        "reason": "open_sell_order",
                        "tier": tier.name,
                        "open_order_count": len(open_sell_orders),
                        "open_order_ids": [oid for oid in order_ids if oid],
                    }
                )
                logger.info(
                    "sell-win skip: market=%s token=%s event_slug=%s reason=open_sell_order count=%d",
                    position.market_id,
                    position.token_id[:16],
                    position.event_slug,
                    len(open_sell_orders),
                )
                continue

            sell_size = position.size
            if settings.sold_win_sell_shares is not None:
                sell_size = min(sell_size, float(settings.sold_win_sell_shares))

            selection = _selection_for_sell_win(event, market, position, sell_size)
            order_result = executor.sell_yes(
                selection,
                share_count=sell_size,
                order_price=order_price,
                expiration_ts=expiration_ts,
            )

            if not order_result.get("dry_run"):
                sell_tracker.record_sell(
                    market_id=position.market_id,
                    token_id=position.token_id,
                    order_id=order_result.get("order_id"),
                    extra={
                        "action": "sell_order_placed",
                        "strategy": "sell_win",
                        "event_slug": position.event_slug,
                        "price": order_result.get("price"),
                        "size": sell_size,
                        "avg_buy_price": position.avg_price,
                        "tier": tier.name,
                        "floor_price": tier.floor_price,
                        "current_mid": current_mid,
                        "reason": tier.name,
                    },
                )

            placed.append(
                {
                    "market_id": position.market_id,
                    "token_id": position.token_id,
                    "event_slug": position.event_slug,
                    "tier": tier.name,
                    "floor_price": tier.floor_price,
                    "current_mid": current_mid,
                    "order": order_result,
                }
            )
            _log_sell_order_placed(
                position,
                order_result,
                tier=tier.name,
                floor_price=tier.floor_price,
                current_mid=current_mid,
            )
        except Exception as exc:
            logger.exception(
                "Sell-win check failed for market %s token %s",
                position.market_id,
                position.token_id,
            )
            errors.append(
                {
                    "market_id": position.market_id,
                    "token_id": position.token_id,
                    "error": str(exc),
                }
            )

    result = {
        "status": "ok",
        "dry_run": executor.dry_run,
        "positions_loaded": len(positions),
        "positions_checked": checked,
        "skipped": skipped,
        "placed": placed,
        "errors": errors,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    _log_run_summary(result)
    return result
