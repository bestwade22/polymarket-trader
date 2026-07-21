"""Simulate buy pass: sample times → strategy → YES_PRICE_MAX → optional SPREAD_MAX."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from config.settings import settings
from src.simulation.market_snapshot import build_event_at_time
from src.simulation.price_at_time import PriceHistoryStore
from src.simulation.sample_times import format_sample_time_local, sample_times_utc_for_event
from src.simulation.snapshot_enrichment import SnapshotEnrichment
from src.trade.selector import filter_by_spread_max, get_strategy
from src.trade.strategies.base import MarketSelection
from src.trade.strategies.highest_yes import HighestYesStrategy
from src.utils.market_parser import get_spread
from src.utils.time_window import trading_window_bounds_utc

logger = logging.getLogger(__name__)


@dataclass
class SimulatedBuy:
    event: dict
    selection: MarketSelection
    bought_at: datetime
    sample_time_local: str
    buy_price: float
    gamma_proxy: bool
    spread: Optional[float]
    strategy_name: str


def history_window_ts(event: dict) -> tuple[int, int]:
    """Day window covering buy + sell-win for price history fetches."""
    event_date = str(event.get("event_date") or "")
    tz_name = str(event.get("timezone") or "UTC")
    bounds = trading_window_bounds_utc(
        event_date,
        tz_name,
        start_hour=0,
        start_minute=0,
        end_hour=23,
        end_minute=59,
    )
    if bounds:
        start, end = bounds
        # Widen slightly for CLOB history edges
        return int(start.timestamp()) - 3600, int(end.timestamp()) + 3600
    samples = sample_times_utc_for_event(event)
    if samples:
        return int(samples[0].timestamp()) - 3600, int(samples[-1].timestamp()) + 6 * 3600
    return 0, 0


def try_buy_event(
    event: dict,
    store: PriceHistoryStore,
    *,
    strategy_name: Optional[str] = None,
    yes_price_max: Optional[float] = None,
    spread_max: Optional[float] = None,
    share_count: Optional[int] = None,
    enrichment_index: Optional[dict[str, list[SnapshotEnrichment]]] = None,
) -> Optional[SimulatedBuy]:
    """Walk sample times; return first buy that passes filters, or None."""
    name = (strategy_name or settings.strategy).lower()
    strategy = get_strategy(name)
    if isinstance(strategy, HighestYesStrategy):
        if yes_price_max is not None:
            strategy.yes_price_max = yes_price_max
        if share_count is not None:
            strategy.share_count = share_count

    max_spread = settings.spread_max if spread_max is None else spread_max
    hist_start, hist_end = history_window_ts(event)
    samples = sample_times_utc_for_event(event)
    if not samples:
        logger.debug(
            "event=%s city=%s no sample times in window",
            event.get("id"),
            event.get("city"),
        )
        return None

    tz_name = str(event.get("timezone") or "")
    for at in samples:
        priced_event, gamma_proxy = build_event_at_time(
            event,
            at,
            store,
            history_start_ts=hist_start,
            history_end_ts=hist_end,
            enrichment_index=enrichment_index,
        )
        if not priced_event.get("markets"):
            continue

        selection = strategy.select_market(priced_event)
        if selection is None:
            continue

        # YES_PRICE_MAX
        if hasattr(strategy, "filter_by_yes_price_max"):
            kept, _skipped = strategy.filter_by_yes_price_max([selection])
            if not kept:
                continue
            selection = kept[0]

        # SPREAD_MAX only when snapshot spread known
        spread = get_spread(selection.market or {})
        if spread is not None:
            kept, _skipped = filter_by_spread_max([selection], spread_max=max_spread)
            if not kept:
                continue
            selection = kept[0]

        buy_price = selection.buy_price
        if buy_price is None:
            continue
        if share_count is not None:
            selection.share_count = share_count

        # Persist price cache for bought token only
        store.mark_bought(selection.yes_token_id)

        local = format_sample_time_local(at, tz_name) if tz_name else ""
        logger.info(
            "sim buy: city=%s temp=%s price=%.3f sample=%s gamma_proxy=%s spread=%s",
            selection.city,
            selection.group_item_title,
            buy_price,
            local,
            gamma_proxy,
            spread,
        )
        return SimulatedBuy(
            event=event,
            selection=selection,
            bought_at=at,
            sample_time_local=local,
            buy_price=float(buy_price),
            gamma_proxy=gamma_proxy,
            spread=spread,
            strategy_name=name,
        )

    return None
