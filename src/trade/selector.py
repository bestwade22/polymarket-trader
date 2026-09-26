import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from config.settings import settings
from src.trade.strategies.base import BaseStrategy, MarketSelection
from src.trade.strategies.forecast_match import ForecastMatchStrategy
from src.trade.strategies.highest_yes import HighestYesStrategy
from src.utils.market_parser import get_spread
from src.utils.time_window import is_event_tradable_now, next_trading_window_hint, trading_window_label

logger = logging.getLogger(__name__)


def _selection_buy_price(sel: MarketSelection) -> Optional[float]:
    """Prefer live ask (buy_price); fall back to selection/mid price."""
    if sel.buy_price is not None:
        return float(sel.buy_price)
    if sel.yes_price is not None:
        return float(sel.yes_price)
    return None


def _event_local_now(
    event: Optional[dict],
    now_utc: Optional[datetime] = None,
) -> Optional[datetime]:
    if not event:
        return None
    event_date = event.get("event_date")
    tz_name = event.get("timezone")
    if not event_date or not tz_name:
        return None
    now = now_utc or datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(str(tz_name))
        return now.astimezone(tz)
    except (ValueError, KeyError):
        return None

STRATEGIES: dict[str, type[BaseStrategy]] = {
    "highest_yes": HighestYesStrategy,
    "forecast_match": ForecastMatchStrategy,
}


def get_strategy(name: Optional[str] = None) -> BaseStrategy:
    strategy_name = (name or settings.strategy).lower()
    cls = STRATEGIES.get(strategy_name)
    if not cls:
        raise ValueError(f"Unknown strategy: {strategy_name}. Choose from {list(STRATEGIES)}")
    return cls()


def filter_tradable_events(events: list[dict], *, all_cities: bool = False) -> list[dict]:
    if all_cities:
        logger.info("Skipping noon window filter; treating all %d events as tradable", len(events))
        return list(events)

    tradable = []
    window_label = trading_window_label()
    for event in events:
        if is_event_tradable_now(event):
            tradable.append(event)
        else:
            logger.debug(
                "event=%s city=%s not in %s local window",
                event.get("id"),
                event.get("city"),
                window_label,
            )
    logger.info("Found %d tradable events in %s local window", len(tradable), window_label)
    if not tradable and events:
        from src.utils.time_window import next_trading_window_hint

        logger.info(
            "No cities in %s local window right now. %s "
            "Use --all-cities to trade every event for the date.",
            window_label,
            next_trading_window_hint(events),
        )
    return tradable


def select_markets_for_events(
    events: list[dict],
    strategy_name: Optional[str] = None,
) -> tuple[list[MarketSelection], list[dict]]:
    strategy = get_strategy(strategy_name)
    selections: list[MarketSelection] = []
    skipped: list[dict] = []
    for event in events:
        event.pop("_last_skip", None)
        selection = strategy.select_market(event)
        if selection:
            selections.append(selection)
        else:
            skip = event.pop("_last_skip", None)
            if skip:
                skipped.append(skip)
    return selections, skipped


def filter_by_on_edge(
    selections: list[MarketSelection],
    *,
    enabled: Optional[bool] = None,
) -> tuple[list[MarketSelection], list[dict]]:
    """Drop selections on the cool edge when SKIP_ON_EDGE is enabled.

    Only skips when cooler buckets exist and all are <1% Yes (vacuous edge
    with no cooler markets is allowed).
    """
    from src.analysis.edge import cooler_markets, is_on_edge

    do_skip = settings.skip_on_edge if enabled is None else enabled
    if not do_skip:
        return selections, []

    kept: list[MarketSelection] = []
    skipped: list[dict] = []
    for sel in selections:
        markets = (sel.event or {}).get("markets") or []
        cooler = cooler_markets(markets, sel.group_item_title) if markets else None
        on_edge = sel.on_edge
        if on_edge is None and markets:
            on_edge = is_on_edge(markets, sel.group_item_title)
            sel.on_edge = on_edge
        # Require real cooler ladder — vacuous edge (no cooler buckets) is fine.
        if on_edge is True and cooler:
            logger.info(
                "event=%s city=%s market=%s on_edge=True; skip",
                sel.event_id,
                sel.city,
                sel.market_id,
            )
            step_log = sel.event.get("_step_logger") if sel.event else None
            if step_log:
                step_log.log_step(
                    "filter_on_edge",
                    skipped=True,
                    market_id=sel.market_id,
                )
            skipped.append(
                {
                    "event_id": sel.event_id,
                    "city": sel.city,
                    "market_id": sel.market_id,
                    "group_item_title": sel.group_item_title,
                    "event_slug": (sel.event or {}).get("slug") if sel.event else None,
                    "reason": "on_edge",
                    "on_edge": True,
                    "selection_price": sel.yes_price,
                }
            )
            continue
        kept.append(sel)
    return kept, skipped


def filter_by_spread_max(
    selections: list[MarketSelection],
    *,
    spread_max: Optional[float] = None,
) -> tuple[list[MarketSelection], list[dict]]:
    """Drop selections whose live bid–ask spread is at or above SPREAD_MAX."""
    max_spread = settings.spread_max if spread_max is None else spread_max
    kept: list[MarketSelection] = []
    skipped: list[dict] = []
    for sel in selections:
        market = sel.market or {}
        spread = get_spread(market)
        if spread is not None and spread >= max_spread:
            logger.info(
                "event=%s city=%s market=%s spread %.3f >= max %.3f; skip",
                sel.event_id,
                sel.city,
                sel.market_id,
                spread,
                max_spread,
            )
            step_log = sel.event.get("_step_logger") if sel.event else None
            if step_log:
                step_log.log_step(
                    "filter_spread_max",
                    skipped=True,
                    spread=spread,
                    spread_max=max_spread,
                    market_id=sel.market_id,
                )
            skipped.append(
                {
                    "event_id": sel.event_id,
                    "city": sel.city,
                    "market_id": sel.market_id,
                    "group_item_title": sel.group_item_title,
                    "event_slug": (sel.event or {}).get("slug") if sel.event else None,
                    "reason": "spread_max",
                    "spread": spread,
                    "spread_max": max_spread,
                    "selection_price": sel.yes_price,
                }
            )
            continue
        kept.append(sel)
    return kept, skipped


def filter_by_yes_gap_min(
    selections: list[MarketSelection],
    *,
    yes_gap_min: Optional[float] = None,
) -> tuple[list[MarketSelection], list[dict]]:
    """Drop selections whose top−runner-up Yes gap is at or below YES_GAP_MIN.

    Missing gap (fewer than two priced markets) is allowed, same as missing spread.
    When YES_GAP_MIN <= 0 the filter is disabled.
    """
    from src.analysis.runner_up import runner_up_details

    min_gap = settings.yes_gap_min if yes_gap_min is None else yes_gap_min
    if min_gap is None or float(min_gap) <= 0:
        return selections, []

    threshold = float(min_gap)
    kept: list[MarketSelection] = []
    skipped: list[dict] = []
    for sel in selections:
        markets = (sel.event or {}).get("markets") or []
        details = runner_up_details(markets, selected_market_id=sel.market_id)
        gap = details.get("yes_gap")
        if gap is not None and float(gap) <= threshold:
            logger.info(
                "event=%s city=%s market=%s yes_gap %.3f <= min %.3f; skip",
                sel.event_id,
                sel.city,
                sel.market_id,
                gap,
                threshold,
            )
            step_log = sel.event.get("_step_logger") if sel.event else None
            if step_log:
                step_log.log_step(
                    "filter_yes_gap_min",
                    skipped=True,
                    yes_gap=gap,
                    yes_gap_min=threshold,
                    runner_up_yes=details.get("runner_up_yes"),
                    runner_up_temp=details.get("runner_up_temp"),
                    market_id=sel.market_id,
                )
            skipped.append(
                {
                    "event_id": sel.event_id,
                    "city": sel.city,
                    "market_id": sel.market_id,
                    "group_item_title": sel.group_item_title,
                    "event_slug": (sel.event or {}).get("slug") if sel.event else None,
                    "reason": "yes_gap_min",
                    "yes_gap": gap,
                    "yes_gap_min": threshold,
                    "runner_up_yes": details.get("runner_up_yes"),
                    "runner_up_temp": details.get("runner_up_temp"),
                    "selection_price": sel.yes_price,
                }
            )
            continue
        kept.append(sel)
    return kept, skipped


def filter_by_buy_band_high_yes_gap(
    selections: list[MarketSelection],
    *,
    band_min: Optional[float] = None,
    band_max: Optional[float] = None,
    yes_gap_min: Optional[float] = None,
) -> tuple[list[MarketSelection], list[dict]]:
    """In buy-price band [min, max), skip when yes_gap ≤ band yes-gap min.

    Defaults: buy in [0.60, 0.70) requires yes_gap > 0.25.
    Missing gap is allowed. Band rule disabled when yes_gap_min <= 0.
    """
    from src.analysis.runner_up import runner_up_details

    lo = settings.buy_band_high_min if band_min is None else band_min
    hi = settings.buy_band_high_max if band_max is None else band_max
    gap_min = (
        settings.buy_band_high_yes_gap_min if yes_gap_min is None else yes_gap_min
    )
    if gap_min is None or float(gap_min) <= 0 or float(hi) <= float(lo):
        return selections, []

    threshold = float(gap_min)
    kept: list[MarketSelection] = []
    skipped: list[dict] = []
    for sel in selections:
        price = _selection_buy_price(sel)
        if price is None or not (float(lo) <= price < float(hi)):
            kept.append(sel)
            continue
        markets = (sel.event or {}).get("markets") or []
        details = runner_up_details(markets, selected_market_id=sel.market_id)
        gap = details.get("yes_gap")
        if gap is not None and float(gap) <= threshold:
            logger.info(
                "event=%s city=%s market=%s buy=%.3f in [%.2f,%.2f) "
                "yes_gap %.3f <= band min %.3f; skip",
                sel.event_id,
                sel.city,
                sel.market_id,
                price,
                lo,
                hi,
                gap,
                threshold,
            )
            step_log = sel.event.get("_step_logger") if sel.event else None
            if step_log:
                step_log.log_step(
                    "filter_buy_band_high_yes_gap",
                    skipped=True,
                    buy_price=price,
                    yes_gap=gap,
                    yes_gap_min=threshold,
                    band_min=float(lo),
                    band_max=float(hi),
                    market_id=sel.market_id,
                )
            skipped.append(
                {
                    "event_id": sel.event_id,
                    "city": sel.city,
                    "market_id": sel.market_id,
                    "group_item_title": sel.group_item_title,
                    "event_slug": (sel.event or {}).get("slug") if sel.event else None,
                    "reason": "buy_band_high_yes_gap",
                    "buy_price": price,
                    "yes_gap": gap,
                    "yes_gap_min": threshold,
                    "band_min": float(lo),
                    "band_max": float(hi),
                    "selection_price": sel.yes_price,
                }
            )
            continue
        kept.append(sel)
    return kept, skipped


def filter_by_buy_band_low_local_time(
    selections: list[MarketSelection],
    *,
    band_min: Optional[float] = None,
    band_max: Optional[float] = None,
    min_local_hour: Optional[int] = None,
    min_local_minute: Optional[int] = None,
    now_utc: Optional[datetime] = None,
) -> tuple[list[MarketSelection], list[dict]]:
    """In buy-price band [min, max], skip when city local time is before cutoff.

    Defaults: buy in [0.45, 0.50] requires local time >= 14:45.
    Missing timezone/date is allowed (rule not applied). Band disabled when max < min.
    """
    lo = settings.buy_band_low_min if band_min is None else band_min
    hi = settings.buy_band_low_max if band_max is None else band_max
    hour = (
        settings.buy_band_low_min_local_hour
        if min_local_hour is None
        else min_local_hour
    )
    minute = (
        settings.buy_band_low_min_local_minute
        if min_local_minute is None
        else min_local_minute
    )
    if float(hi) < float(lo):
        return selections, []

    cutoff_mins = int(hour) * 60 + int(minute)
    kept: list[MarketSelection] = []
    skipped: list[dict] = []
    for sel in selections:
        price = _selection_buy_price(sel)
        if price is None or not (float(lo) <= price <= float(hi)):
            kept.append(sel)
            continue
        local_now = _event_local_now(sel.event, now_utc=now_utc)
        if local_now is None:
            kept.append(sel)
            continue
        local_mins = local_now.hour * 60 + local_now.minute
        if local_mins < cutoff_mins:
            local_label = f"{local_now.hour:02d}:{local_now.minute:02d}"
            cutoff_label = f"{int(hour):02d}:{int(minute):02d}"
            logger.info(
                "event=%s city=%s market=%s buy=%.3f in [%.2f,%.2f] "
                "local %s < %s; skip",
                sel.event_id,
                sel.city,
                sel.market_id,
                price,
                lo,
                hi,
                local_label,
                cutoff_label,
            )
            step_log = sel.event.get("_step_logger") if sel.event else None
            if step_log:
                step_log.log_step(
                    "filter_buy_band_low_local_time",
                    skipped=True,
                    buy_price=price,
                    local_time=local_label,
                    min_local_time=cutoff_label,
                    band_min=float(lo),
                    band_max=float(hi),
                    market_id=sel.market_id,
                )
            skipped.append(
                {
                    "event_id": sel.event_id,
                    "city": sel.city,
                    "market_id": sel.market_id,
                    "group_item_title": sel.group_item_title,
                    "event_slug": (sel.event or {}).get("slug") if sel.event else None,
                    "reason": "buy_band_low_local_time",
                    "buy_price": price,
                    "local_time": local_label,
                    "min_local_time": cutoff_label,
                    "band_min": float(lo),
                    "band_max": float(hi),
                    "selection_price": sel.yes_price,
                }
            )
            continue
        kept.append(sel)
    return kept, skipped


def filter_selections_after_live_refresh(
    selections: list[MarketSelection],
    strategy_name: Optional[str] = None,
) -> tuple[list[MarketSelection], list[dict]]:
    """Apply post-refresh guards: price/spread/gap, buy-band rules, optional on-edge."""
    strategy = get_strategy(strategy_name)
    kept = selections
    skipped_all: list[dict] = []
    if hasattr(strategy, "filter_by_yes_price_max"):
        kept, skipped = strategy.filter_by_yes_price_max(kept)
        skipped_all.extend(skipped)
    kept, skipped = filter_by_spread_max(kept)
    skipped_all.extend(skipped)
    kept, skipped = filter_by_yes_gap_min(kept)
    skipped_all.extend(skipped)
    kept, skipped = filter_by_buy_band_high_yes_gap(kept)
    skipped_all.extend(skipped)
    kept, skipped = filter_by_buy_band_low_local_time(kept)
    skipped_all.extend(skipped)
    kept, skipped = filter_by_on_edge(kept)
    skipped_all.extend(skipped)
    return kept, skipped_all
