"""Simulate sell-win tiers on historical Yes-% for a bought position."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from src.simulation.price_at_time import PriceHistoryStore
from src.trade.sell_win import (
    build_sell_win_tiers,
    is_sell_win_eligible_event,
    is_sell_win_price_eligible,
    sell_win_order_price,
)
from src.utils.market_parser import parse_float

logger = logging.getLogger(__name__)


@dataclass
class SimulatedSell:
    sold_at: datetime
    sell_price: float
    tier_name: str


def _tier_window_utc(
    event: dict,
    tier_start,
    tier_expire,
) -> Optional[tuple[datetime, datetime]]:
    event_date = event.get("event_date")
    tz_name = event.get("timezone")
    if not event_date or not tz_name:
        return None
    try:
        day = datetime.strptime(str(event_date), "%Y-%m-%d").date()
        tz = ZoneInfo(str(tz_name))
    except (ValueError, KeyError):
        return None
    start_local = datetime(
        day.year, day.month, day.day,
        tier_start.hour, tier_start.minute, 0, tzinfo=tz,
    )
    expire_local = datetime(
        day.year, day.month, day.day,
        tier_expire.hour, tier_expire.minute, 0, tzinfo=tz,
    )
    return start_local.astimezone(timezone.utc), expire_local.astimezone(timezone.utc)


def try_sell_win(
    event: dict,
    token_id: str,
    store: PriceHistoryStore,
    *,
    bought_at: datetime,
    history_start_ts: int,
    history_end_ts: int,
) -> Optional[SimulatedSell]:
    """If historical % reaches a sell-win tier floor before expiry, return the sell.

    Walks tiers in order; within each tier uses history points from tier start
    until expire_before. First eligible fill wins (100% fill assumption).
    """
    slug = str(event.get("slug") or "")
    title = str(event.get("title") or "")
    if not is_sell_win_eligible_event(event_slug=slug, title=title, slug=slug):
        return None

    history = store.get_history(
        token_id, start_ts=history_start_ts, end_ts=history_end_ts
    )
    bought_ts = int(bought_at.timestamp())

    for tier in build_sell_win_tiers():
        window = _tier_window_utc(event, tier.start_local, tier.expire_before_local)
        if window is None:
            continue
        tier_start, tier_expire = window
        start_ts = int(tier_start.timestamp())
        expire_ts = int(tier_expire.timestamp())
        if expire_ts <= bought_ts:
            continue

        # Prefer history points inside [max(bought, tier_start), expire)
        lo = max(start_ts, bought_ts)
        candidates: list[tuple[int, float]] = []
        for point in history:
            t = point.get("t")
            p = parse_float(point.get("p"))
            if t is None or p is None:
                continue
            try:
                ts = int(t)
            except (TypeError, ValueError):
                continue
            if lo <= ts < expire_ts:
                candidates.append((ts, p))
        candidates.sort(key=lambda x: x[0])

        for ts, price in candidates:
            if not is_sell_win_price_eligible(price):
                continue
            if price < tier.floor_price:
                continue
            order_price = sell_win_order_price(tier.floor_price, price)
            sold_at = datetime.fromtimestamp(ts, tz=timezone.utc)
            logger.info(
                "sim sell-win: city=%s tier=%s price=%.3f at=%s",
                event.get("city"),
                tier.name,
                order_price,
                sold_at.isoformat(),
            )
            return SimulatedSell(
                sold_at=sold_at,
                sell_price=float(order_price),
                tier_name=tier.name,
            )

    return None
