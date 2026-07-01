"""Periodic stop-loss check over live wallet positions."""

import json
import logging
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Any, Optional

from config.settings import DATA_DIR, STOP_LOSS_DIR, ensure_dirs, settings
from src.api.data_client import DataClient, LivePosition, fetch_user_positions
from src.api.gamma_client import GammaClient
from src.trade.executor import TradeExecutor
from src.trade.open_order_checker import LiveOpenOrderChecker
from src.trade.position_checker import MIN_POSITION_SHARES
from src.trade.position_tracker import SoldPositionTracker
from src.trade.price_refresher import refresh_market_prices
from src.trade.stop_loss import is_stop_loss_eligible_event, should_stop_loss
from src.trade.strategies.base import MarketSelection
from src.utils.market_parser import (
    get_buy_price,
    get_order_min_size,
    get_sell_price,
    get_tick_size,
    get_yes_token_id,
    is_neg_risk,
)

logger = logging.getLogger(__name__)


def _find_market_in_event(event: dict, token_id: str, market_id: str) -> Optional[dict]:
    for market in event.get("markets", []):
        mid = str(market.get("id", ""))
        tid = get_yes_token_id(market)
        if tid == token_id or (market_id and mid == market_id):
            return market
    return None


def _load_events_from_cache(event_slug: str = "", token_id: str = "") -> Optional[dict]:
    pattern = str(DATA_DIR / "events_*.json")
    for path in sorted(glob(pattern), reverse=True):
        try:
            events = json.loads(Path(path).read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for event in events:
            slug = str(event.get("slug") or "")
            if event_slug and event_slug in slug:
                return event
            if token_id and _find_market_in_event(event, token_id, ""):
                return event
    return None


def resolve_event_for_position(
    position: LivePosition,
    gamma: Optional[GammaClient] = None,
) -> Optional[dict]:
    gamma = gamma or GammaClient()

    if position.event_slug or position.token_id:
        cached = _load_events_from_cache(
            event_slug=position.event_slug,
            token_id=position.token_id,
        )
        if cached:
            return cached

    if position.event_id:
        event = gamma.fetch_event_by_id(position.event_id)
        if event:
            return event

    if position.event_slug:
        matches = gamma.search_events(position.event_slug, limit=20)
        for event in matches:
            slug = str(event.get("slug") or "")
            if position.event_slug in slug:
                return event

    if position.market_id:
        market = gamma.fetch_market_by_id(position.market_id)
        if market:
            event_id = str(market.get("eventId") or market.get("event_id") or "")
            if event_id:
                return gamma.fetch_event_by_id(event_id)

    return None


def selection_from_position(
    event: dict,
    market: dict,
    position: LivePosition,
    share_count: float,
) -> MarketSelection:
    yes_token = get_yes_token_id(market) or position.token_id
    buy_price = get_buy_price(market) or position.avg_price
    return MarketSelection(
        event_id=str(event.get("id", position.event_id)),
        city=event.get("city", ""),
        market_id=str(market.get("id", position.market_id)),
        group_item_title=market.get("groupItemTitle", position.title),
        yes_price=position.avg_price,
        yes_token_id=yes_token,
        buy_price=buy_price,
        share_count=max(int(share_count), get_order_min_size(market)),
        neg_risk=is_neg_risk(market),
        tick_size=get_tick_size(market),
        order_min_size=get_order_min_size(market),
        strategy="stop_loss",
        event=event,
        market=market,
    )


def save_stop_loss_run(result: dict) -> Path:
    ensure_dirs()
    now = datetime.now(timezone.utc)
    path = STOP_LOSS_DIR / f"stop_loss_{now.strftime('%Y-%m-%dT%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2))
    return path


def run_stop_loss_check(
    dry_run: Optional[bool] = None,
    wallet_address: Optional[str] = None,
) -> dict[str, Any]:
    ensure_dirs()
    executor = TradeExecutor(dry_run=dry_run)
    open_order_checker = LiveOpenOrderChecker(executor)
    sold_tracker = SoldPositionTracker()
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
            "sold": [],
            "errors": [],
        }

    skipped: list[dict[str, Any]] = []
    sold: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    checked = 0

    for position in positions:
        if position.size < MIN_POSITION_SHARES:
            continue
        if sold_tracker.is_market_sold(position.market_id):
            skipped.append(
                {
                    "market_id": position.market_id,
                    "token_id": position.token_id,
                    "reason": "already_sold",
                }
            )
            continue

        if not is_stop_loss_eligible_event(
            event_slug=position.event_slug,
            title=position.title,
        ):
            logger.info(
                "stop-loss skip: token=%s event_slug=%s reason=not_temp_market",
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

            if not is_stop_loss_eligible_event(
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

            checked += 1
            trigger, reason, value_pct = should_stop_loss(
                position.avg_price,
                current_mid,
                settings.stop_loss_pct,
            )

            log_line = (
                f"stop-loss check: event_slug={position.event_slug} market={position.market_id} "
                f"size={position.size} avg_buy={position.avg_price:.4f} mid={current_mid:.4f} "
                f"value_pct={value_pct:.1f}% action={'sell' if trigger else 'hold'}"
            )
            logger.info(log_line)

            if not trigger:
                skipped.append(
                    {
                        "market_id": position.market_id,
                        "token_id": position.token_id,
                        "event_slug": position.event_slug,
                        "reason": reason,
                        "value_pct": value_pct,
                        "avg_price": position.avg_price,
                        "current_mid": current_mid,
                    }
                )
                continue

            sell_size = position.size
            if settings.stop_loss_sell_shares is not None:
                sell_size = min(sell_size, float(settings.stop_loss_sell_shares))

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
                        "open_order_count": len(open_sell_orders),
                        "open_order_ids": [oid for oid in order_ids if oid],
                    }
                )
                logger.info(
                    "stop-loss skip: market=%s token=%s reason=open_sell_order count=%d",
                    position.market_id,
                    position.token_id[:16],
                    len(open_sell_orders),
                )
                continue

            selection = selection_from_position(event, market, position, sell_size)
            order_result = executor.sell_yes(selection, share_count=sell_size)

            if not order_result.get("dry_run"):
                sold_tracker.record_sell(
                    market_id=position.market_id,
                    token_id=position.token_id,
                    order_id=order_result.get("order_id"),
                    extra={
                        "event_slug": position.event_slug,
                        "price": order_result.get("price"),
                        "size": sell_size,
                        "avg_buy_price": position.avg_price,
                        "value_pct": value_pct,
                        "reason": reason,
                    },
                )

            sold.append(
                {
                    "market_id": position.market_id,
                    "token_id": position.token_id,
                    "event_slug": position.event_slug,
                    "value_pct": value_pct,
                    "order": order_result,
                }
            )
        except Exception as exc:
            logger.exception(
                "Stop-loss check failed for market %s token %s",
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
        "sold": sold,
        "errors": errors,
        "threshold_pct": settings.stop_loss_pct,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    run_path = save_stop_loss_run(result)
    result["run_file"] = str(run_path)
    return result
