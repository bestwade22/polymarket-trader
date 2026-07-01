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
from src.trade.hourly_runner import run_hourly_trade
from src.trade.stop_loss_runner import run_stop_loss_check
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


def cmd_run_scheduler(_args: argparse.Namespace) -> None:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: run_daily_fetch(target_date=date.today()),
        CronTrigger(hour=settings.daily_fetch_hour_utc, minute=0),
        id="daily_fetch",
    )
    scheduler.add_job(
        lambda: run_hourly_trade(target_date=date.today()),
        CronTrigger(minute="30"),
        id="hourly_trade",
    )
    logging.info(
        "Scheduler started: daily fetch at %02d:00 UTC, trade at :30 each hour",
        settings.daily_fetch_hour_utc,
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

    commands = {
        "fetch-daily": cmd_fetch_daily,
        "trade-hourly": cmd_trade_hourly,
        "check-stop-loss": cmd_check_stop_loss,
        "run-scheduler": cmd_run_scheduler,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
