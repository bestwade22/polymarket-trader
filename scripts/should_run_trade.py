#!/usr/bin/env python3
"""Exit 0 when any event in dated events files is in its local trading window; else exit 1."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from zoneinfo import ZoneInfo

from src.utils.hk_time import format_hk
from src.utils.time_window import (  # noqa: E402
    is_event_tradable_now,
    trading_window_bounds_utc,
    trading_window_label,
)


def event_file_dates_to_check(now_utc: datetime) -> list[date]:
    today = now_utc.date()
    return [today, today - timedelta(days=1)]


def load_events_file(data_dir: Path, file_date: date) -> list[dict]:
    path = data_dir / f"events_{file_date.isoformat()}.json"
    if not path.exists():
        return []
    try:
        events = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return events if isinstance(events, list) else []


def tradable_events(events: list[dict], now_utc: datetime) -> list[dict]:
    return [event for event in events if is_event_tradable_now(event, now_utc=now_utc)]


def describe_event_gate(event: dict, now_utc: datetime) -> Dict[str, Any]:
    """Per-event gate breakdown for logging and verification."""
    city = str(event.get("city", "?"))
    event_date = str(event.get("event_date", "") or "")
    tz_name = str(event.get("timezone", "") or "")
    tradable = is_event_tradable_now(event, now_utc=now_utc)
    detail: Dict[str, Any] = {
        "city": city,
        "event_date": event_date,
        "timezone": tz_name,
        "tradable": tradable,
    }

    if not event_date or not tz_name:
        detail["reason"] = "missing_event_date_or_timezone"
        return detail

    try:
        tz = ZoneInfo(tz_name)
        local_now = now_utc.astimezone(tz)
        detail["local_now"] = local_now.strftime("%Y-%m-%d %H:%M")
    except (ValueError, KeyError):
        detail["reason"] = "invalid_timezone"
        return detail

    if local_now.date().isoformat() != event_date:
        detail["reason"] = "local_date_not_event_date"
        return detail

    bounds = trading_window_bounds_utc(event_date, tz_name)
    if not bounds:
        detail["reason"] = "window_bounds_unavailable"
        return detail

    start, end = bounds
    detail["window_local"] = trading_window_label()
    detail["window_hkt_start"] = format_hk(start)
    detail["window_hkt_end"] = format_hk(end)

    if now_utc < start:
        detail["reason"] = "before_window"
    elif now_utc > end:
        detail["reason"] = "after_window"
    else:
        detail["reason"] = "in_window"

    return detail


def tradable_event_file_dates(
    now_utc: Optional[datetime] = None,
    data_dir: Optional[Path] = None,
) -> list[str]:
    """File dates (events_YYYY-MM-DD.json) that contain at least one tradable event now."""
    now = now_utc or datetime.now(timezone.utc)
    root = data_dir or PROJECT_ROOT / "data"
    dates: list[str] = []
    for file_date in event_file_dates_to_check(now):
        events = load_events_file(root, file_date)
        if tradable_events(events, now):
            dates.append(file_date.isoformat())
    return dates


def evaluate_trade_gate(
    now_utc: Optional[datetime] = None,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Summarize whether trade-hourly should run now."""
    now = now_utc or datetime.now(timezone.utc)
    root = data_dir or PROJECT_ROOT / "data"
    window = trading_window_label()

    file_dates = event_file_dates_to_check(now)
    files_checked = [f"events_{d.isoformat()}.json" for d in file_dates]
    loaded_events: list[dict] = []
    for file_date in file_dates:
        loaded_events.extend(load_events_file(root, file_date))

    event_checks = [describe_event_gate(event, now) for event in loaded_events]
    tradable_checks = [check for check in event_checks if check.get("tradable")]
    tradable = tradable_events(loaded_events, now)

    base: Dict[str, Any] = {
        "now_hkt": format_hk(now),
        "window": window,
        "data_dir": str(root),
        "files_checked": files_checked,
        "events_loaded": len(loaded_events),
        "event_checks": event_checks,
    }

    if tradable:
        cities = sorted({str(e.get("city", "?")) for e in tradable})
        return {
            **base,
            "should_run": True,
            "status": "go",
            "reason": "tradable_events",
            "tradable_cities": cities,
            "tradable_count": len(tradable_checks),
        }

    if loaded_events:
        return {
            **base,
            "should_run": False,
            "status": "skip",
            "reason": "no_tradable_events",
            "tradable_cities": [],
            "tradable_count": 0,
        }

    return {
        **base,
        "should_run": False,
        "status": "no_data",
        "reason": "no_events_files",
        "tradable_cities": [],
        "tradable_count": 0,
    }


def should_run_trade(
    now_utc: Optional[datetime] = None,
    data_dir: Optional[Path] = None,
) -> bool:
    """True when at least one event in today/yesterday files is in its trading window."""
    result = evaluate_trade_gate(now_utc=now_utc, data_dir=data_dir)
    return bool(result["should_run"])


def main() -> None:
    result = evaluate_trade_gate()
    print(
        f"Gate: status={result['status']} reason={result['reason']} "
        f"window={result['window']} events_loaded={result['events_loaded']} "
        f"tradable={result.get('tradable_count', 0)}"
    )
    for check in result.get("event_checks", []):
        if check.get("tradable"):
            print(
                f"  TRADABLE {check['city']}: local_now={check.get('local_now')} "
                f"window={check.get('window_local')} ({check.get('reason')})"
            )
    if not result["should_run"] and result.get("event_checks"):
        reasons: Dict[str, int] = {}
        for check in result["event_checks"]:
            if not check.get("tradable"):
                key = str(check.get("reason", "unknown"))
                reasons[key] = reasons.get(key, 0) + 1
        print(f"  Skipped by reason: {reasons}")
    if result["should_run"]:
        print(
            f"Trading window active for: {', '.join(result['tradable_cities'])} "
            f"({result['window']})"
        )
        raise SystemExit(0)
    print(
        f"No tradable events ({result['reason']}); "
        f"window={result['window']}; events_loaded={result['events_loaded']}"
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
