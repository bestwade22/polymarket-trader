import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import DATA_DIR, SELECTIONS_DIR, ensure_dirs, events_file_for_date, parse_event_date, settings
from src.trade.city_skip import filter_events_by_skip_cities, resolve_skip_cities
from src.trade.executor import TradeExecutor
from src.trade.open_order_checker import LiveOpenOrderChecker, filter_events_without_open_orders
from src.trade.position_checker import LivePositionChecker, filter_selections_without_position
from src.trade.position_tracker import PositionTracker
from src.trade.price_refresher import refresh_events_markets, refresh_selection_prices
from src.trade.selector import filter_tradable_events, filter_selections_after_live_refresh, select_markets_for_events
from src.utils.market_parser import market_price_snapshot
from src.utils.trade_logger import TradeStepLogger

logger = logging.getLogger(__name__)


def load_events(path: Optional[Path] = None, target_date: Optional[date] = None) -> list[dict]:
    events_path = path or events_file_for_date(target_date or parse_event_date())
    if not events_path.exists():
        legacy = DATA_DIR / "events.json"
        if legacy.exists():
            logger.warning("Dated events file missing; falling back to %s", legacy)
            events_path = legacy
    if not events_path.exists():
        logger.warning("Events file not found: %s", events_path)
        return []
    logger.info("Loading events from %s", events_path)
    return json.loads(events_path.read_text())


def save_selections(
    selections: list,
    strategy: str,
    *,
    order_results: Optional[list[dict]] = None,
    skipped_bought: Optional[list[dict]] = None,
) -> Path:
    ensure_dirs()
    now = datetime.now(timezone.utc)
    filename = f"markets_yes_{now.strftime('%Y-%m-%d')}_{now.strftime('%H%M')}.json"
    path = SELECTIONS_DIR / filename
    results_by_event = {
        str(r.get("event_id")): r for r in (order_results or []) if r.get("event_id")
    }
    saved_selections = []
    for sel in selections:
        row = sel.to_dict()
        outcome = results_by_event.get(str(sel.event_id), {})
        if outcome:
            row["order_status"] = outcome.get("status")
            row["order_id"] = outcome.get("order_id")
            row["dry_run"] = outcome.get("dry_run", True)
            row["order_type"] = outcome.get("order_type")
            row["expires_at"] = outcome.get("expires_at")
            if outcome.get("error"):
                row["order_error"] = outcome["error"]
        saved_selections.append(row)
    payload = {
        "run_at": now.isoformat(),
        "strategy": strategy,
        "selections": saved_selections,
    }
    if skipped_bought:
        payload["skipped_bought"] = skipped_bought
    path.write_text(json.dumps(payload, indent=2))
    logger.info(
        "Saved %d selections (%d skipped: open orders, positions, or other) to %s",
        len(saved_selections),
        len(skipped_bought or []),
        path,
    )
    return path


def run_hourly_trade(
    strategy_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    events_file: Optional[Path] = None,
    target_date: Optional[date] = None,
    all_cities: bool = False,
) -> dict:
    strategy = (strategy_name or settings.strategy).lower()
    trade_date = target_date or parse_event_date()
    events = load_events(path=events_file, target_date=trade_date)
    step_logger_global = TradeStepLogger("hourly_run")
    step_logger_global.log_step(
        "load_events",
        count=len(events),
        event_date=trade_date.isoformat(),
        events_file=str(events_file or events_file_for_date(trade_date)),
    )

    tradable = filter_tradable_events(events, all_cities=all_cities)
    step_logger_global.log_step("filter_tradable", count=len(tradable), all_cities=all_cities)

    tracker = PositionTracker()
    eligible = []
    skipped_bought = []
    for event in tradable:
        event_id = str(event.get("id"))
        city = event.get("city") or ""
        step_log = TradeStepLogger(event_id, city=city)
        step_log.log_step("load_events", tradable=True, city=city)
        event["_step_logger"] = step_log
        eligible.append(event)

    skip_cities = resolve_skip_cities()
    eligible, skipped_city_perf = filter_events_by_skip_cities(eligible, skip_cities)
    skipped_bought.extend(skipped_city_perf)
    step_logger_global.log_step(
        "filter_city_win_summary",
        count=len(eligible),
        skipped=len(skipped_city_perf),
        skip_cities=skip_cities,
    )

    eligible = refresh_events_markets(eligible)

    executor = TradeExecutor(dry_run=dry_run)
    order_checker = LiveOpenOrderChecker(executor)
    eligible, skipped_open_orders = filter_events_without_open_orders(eligible, order_checker)
    skipped_bought.extend(skipped_open_orders)

    for event in eligible:
        step_log = event.get("_step_logger")
        if step_log:
            step_log.log_step(
                "refresh_prices",
                refreshed_at=event.get("prices_refreshed_at"),
                markets=len(event.get("markets", [])),
            )

    selections = select_markets_for_events(eligible, strategy_name=strategy)
    selections, skipped_price_max = filter_selections_after_live_refresh(
        selections, strategy_name=strategy
    )
    skipped_bought.extend(skipped_price_max)

    position_checker = LivePositionChecker(executor)
    selections, skipped_positions = filter_selections_without_position(
        selections, position_checker
    )
    skipped_bought.extend(skipped_positions)

    selections = refresh_selection_prices(selections)
    selections, skipped_price_max_late = filter_selections_after_live_refresh(
        selections, strategy_name=strategy
    )
    skipped_bought.extend(skipped_price_max_late)
    for sel in selections:
        step_log = sel.event.get("_step_logger") if sel.event else None
        if step_log:
            step_log.log_step(
                "select_market",
                strategy=strategy,
                market_id=sel.market_id,
                yes_price=sel.yes_price,
                buy_price=sel.buy_price,
                group_item_title=sel.group_item_title,
                forecast_temp_f=sel.forecast_temp_f,
                price_snapshot=(
                    {"market_id": sel.market_id, **market_price_snapshot(sel.market)}
                    if sel.market
                    else None
                ),
            )

    selection_path = None

    results = []
    for sel in selections:
        step_log = (
            sel.event.get("_step_logger")
            if sel.event
            else TradeStepLogger(sel.event_id, city=sel.city or "")
        )
        try:
            result = executor.buy_yes(sel)
            result["event_id"] = sel.event_id
            step_log.log_step(
                "place_order",
                dry_run=result.get("dry_run", True),
                order_id=result.get("order_id"),
                status=result.get("status"),
                market_id=sel.market_id,
                share_count=sel.share_count,
                price=result.get("price", sel.buy_price),
                order_type=result.get("order_type"),
                expires_at=result.get("expires_at"),
            )
            if not result.get("dry_run"):
                tracker.record_buy(
                    event_id=sel.event_id,
                    market_id=sel.market_id,
                    strategy=sel.strategy,
                    order_id=result.get("order_id"),
                    extra={"price": sel.buy_price, "size": sel.share_count},
                )
            results.append(result)
        except Exception as exc:
            err_result = {"event_id": sel.event_id, "error": str(exc)}
            results.append(err_result)
            step_log.log_step("place_order", error=str(exc))
            logger.exception("Failed to buy for event %s", sel.event_id)
        finally:
            step_log.save()

    if selections or skipped_bought:
        selection_path = save_selections(
            selections,
            strategy,
            order_results=results,
            skipped_bought=skipped_bought or None,
        )

    step_logger_global.log_step(
        "complete",
        tradable=len(tradable),
        eligible=len(eligible),
        skipped_bought=len(skipped_bought),
        selections=len(selections),
        orders=len(results),
    )
    step_logger_global.save()

    return {
        "event_date": trade_date.isoformat(),
        "tradable": len(tradable),
        "eligible": len(eligible),
        "skipped_bought": len(skipped_bought),
        "selections": len(selections),
        "orders": len(results),
        "selection_file": str(selection_path) if selection_path else None,
        "events_file": str(events_file or events_file_for_date(trade_date)),
    }
