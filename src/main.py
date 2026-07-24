import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import ensure_dirs, parse_event_date, settings
from src.fetch.daily_events import run_daily_fetch
from src.analysis.sync_runner import run_sync_trade_history
from src.simulation.runner import run_simulate_trades
from src.trade.hourly_runner import run_hourly_trade
from src.trade.sell_win_runner import run_sell_win_check
from src.trade.stop_loss_runner import run_stop_loss_check
from src.utils.hk_time import utc_clock_label
from src.utils.trade_logger import setup_app_logging


def _add_date_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Event date to fetch or trade (default: today, or EVENT_DATE env)",
    )


def cmd_fetch_daily(args: argparse.Namespace) -> None:
    target = parse_event_date(args.date)
    events = run_daily_fetch(target_date=target)
    logging.info("Daily fetch complete for %s: %d events", target.isoformat(), len(events))


def cmd_trade_hourly(args: argparse.Namespace) -> None:
    target = parse_event_date(args.date)
    result = run_hourly_trade(
        strategy_name=args.strategy,
        dry_run=args.dry_run,
        target_date=target,
        all_cities=args.all_cities,
    )
    logging.info("Hourly trade complete: %s", result)


def cmd_check_stop_loss(args: argparse.Namespace) -> None:
    run_stop_loss_check(dry_run=args.dry_run)


def cmd_check_sell_win(args: argparse.Namespace) -> None:
    run_sell_win_check(dry_run=args.dry_run)


def cmd_sync_trade_history(args: argparse.Namespace) -> None:
    result = run_sync_trade_history(
        init_days=args.init_days,
        fetch_price_drop=not args.skip_price_drop,
    )
    logging.info("Trade history sync complete: %s", result)


def cmd_simulate_trades(args: argparse.Namespace) -> None:
    from_date = parse_event_date(args.from_date) if args.from_date else None
    to_date = parse_event_date(args.to_date) if args.to_date else None
    result = run_simulate_trades(
        from_date=from_date,
        to_date=to_date,
        strategy_name=args.strategy,
        yes_price_max=args.yes_price_max,
        spread_max=args.spread_max,
        share_count=args.share_count,
        fetch_if_missing=not args.no_fetch,
        force=args.force,
    )
    logging.info("Simulate trades complete: %s", result)


def cmd_run_scheduler(_args: argparse.Namespace) -> None:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: run_daily_fetch(target_date=date.today()),
        CronTrigger(hour=settings.daily_fetch_hour_utc, minute=0),
        id="daily_fetch",
    )
    scheduler.add_job(
        lambda: run_hourly_trade(target_date=date.today()),
        CronTrigger(minute="15,45"),
        id="hourly_trade",
    )
    logging.info(
        "Scheduler started: daily fetch at %s, trade at %s and %s UTC each hour",
        utc_clock_label(settings.daily_fetch_hour_utc),
        utc_clock_label(0, 15),
        utc_clock_label(0, 45),
    )
    scheduler.start()


def main() -> None:
    ensure_dirs()
    setup_app_logging()

    parser = argparse.ArgumentParser(description="Polymarket weather trading bot")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_parser = sub.add_parser("fetch-daily", help="Fetch highest-temp events for a date")
    _add_date_arg(fetch_parser)

    trade_parser = sub.add_parser("trade-hourly", help="Run hourly trade strategy")
    _add_date_arg(trade_parser)
    trade_parser.add_argument(
        "--strategy",
        choices=["highest_yes", "forecast_match"],
        default=None,
        help="Override STRATEGY env var",
    )
    trade_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Simulate orders without placing them",
    )
    trade_parser.add_argument(
        "--live",
        action="store_true",
        help="Place real orders (overrides DRY_RUN env)",
    )
    trade_parser.add_argument(
        "--all-cities",
        action="store_true",
        help="Trade all events for the date (skip configured local trading window)",
    )

    sub.add_parser("run-scheduler", help="Run daily + hourly scheduler")

    stop_loss_parser = sub.add_parser("check-stop-loss", help="Check live positions for stop-loss")
    stop_loss_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Simulate sells without placing orders",
    )
    stop_loss_parser.add_argument(
        "--live",
        action="store_true",
        help="Place real sell orders (overrides DRY_RUN env)",
    )

    sell_win_parser = sub.add_parser(
        "check-sell-win",
        help="Place tiered sell-win limit orders on live positions",
    )
    sell_win_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Simulate sell orders without placing them",
    )
    sell_win_parser.add_argument(
        "--live",
        action="store_true",
        help="Place real sell orders (overrides SELL_WIN_DRY_RUN env)",
    )

    sync_parser = sub.add_parser(
        "sync-trade-history",
        help="Sync wallet highest-temp trade history from Data API activity",
    )
    sync_parser.add_argument(
        "--init-days",
        type=int,
        default=None,
        metavar="N",
        help="Backfill last N days (replaces incremental window)",
    )
    sync_parser.add_argument(
        "--skip-price-drop",
        action="store_true",
        help="Skip CLOB price-history lookups for loss/sold rows",
    )

    sim_parser = sub.add_parser(
        "simulate-trades",
        help="Replay strategies on historical weather events (writes sim_trade_history.json)",
    )
    sim_parser.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Start date inclusive (default: 7 days ending yesterday)",
    )
    sim_parser.add_argument(
        "--to",
        dest="to_date",
        metavar="YYYY-MM-DD",
        default=None,
        help="End date inclusive (default: yesterday)",
    )
    sim_parser.add_argument(
        "--strategy",
        choices=["highest_yes", "forecast_match"],
        default=None,
        help="Override STRATEGY env var (default: highest_yes)",
    )
    sim_parser.add_argument(
        "--yes-price-max",
        type=float,
        default=None,
        metavar="P",
        help="Skip buys at or above this Yes %% (default: YES_PRICE_MAX)",
    )
    sim_parser.add_argument(
        "--spread-max",
        type=float,
        default=None,
        metavar="S",
        help="Skip when markets_yes_* spread >= this (default: SPREAD_MAX)",
    )
    sim_parser.add_argument(
        "--share-count",
        type=int,
        default=None,
        metavar="N",
        help="Simulated share size (default: SHARE_COUNT)",
    )
    sim_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not fetch missing events_*.json files",
    )
    sim_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-simulate even when strategy/process already completed for those dates",
    )

    args = parser.parse_args()

    if args.command == "trade-hourly":
        if args.live:
            args.dry_run = False
        elif args.dry_run is None:
            args.dry_run = settings.dry_run

    if args.command == "check-stop-loss":
        if args.live:
            args.dry_run = False
        elif args.dry_run is None:
            args.dry_run = settings.stop_loss_dry_run

    if args.command == "check-sell-win":
        if args.live:
            args.dry_run = False
        elif args.dry_run is None:
            args.dry_run = settings.sell_win_dry_run

    commands = {
        "fetch-daily": cmd_fetch_daily,
        "trade-hourly": cmd_trade_hourly,
        "check-stop-loss": cmd_check_stop_loss,
        "check-sell-win": cmd_check_sell_win,
        "sync-trade-history": cmd_sync_trade_history,
        "simulate-trades": cmd_simulate_trades,
        "run-scheduler": cmd_run_scheduler,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
