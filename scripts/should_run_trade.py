#!/usr/bin/env python3
"""Exit 0 when any event in dated events files is in its local trading window; else exit 1."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.time_window import is_event_tradable_now, trade_tick_label, trading_window_label  # noqa: E402


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
    ticks = trade_tick_label()

    loaded_events: list[dict] = []
    for file_date in event_file_dates_to_check(now):
        loaded_events.extend(load_events_file(root, file_date))

    tradable = tradable_events(loaded_events, now)
    if tradable:
        cities = sorted({str(e.get("city", "?")) for e in tradable})
        return {
            "should_run": True,
            "status": "go",
            "reason": "tradable_events",
            "now_utc": now.isoformat(),
            "window": window,
            "ticks": ticks,
            "tradable_cities": cities,
            "events_loaded": len(loaded_events),
        }

    if loaded_events:
        return {
            "should_run": False,
            "status": "skip",
            "reason": "no_tradable_events",
            "now_utc": now.isoformat(),
            "window": window,
            "ticks": ticks,
            "tradable_cities": [],
            "events_loaded": len(loaded_events),
        }

    return {
        "should_run": False,
        "status": "no_data",
        "reason": "no_events_files",
        "now_utc": now.isoformat(),
        "window": window,
        "ticks": ticks,
        "tradable_cities": [],
        "events_loaded": 0,
    }


def should_run_trade(
    now_utc: Optional[datetime] = None,
    data_dir: Optional[Path] = None,
) -> bool:
    """True when at least one event in today/yesterday files is on a trade tick."""
    result = evaluate_trade_gate(now_utc=now_utc, data_dir=data_dir)
    return bool(result["should_run"])


def main() -> None:
    result = evaluate_trade_gate()
    if result["should_run"]:
        print(
            f"Trading window active for: {', '.join(result['tradable_cities'])} "
            f"({result['window']}; {result['ticks']})"
        )
        raise SystemExit(0)
    print(
        f"No tradable events ({result['reason']}); "
        f"window={result['window']}; ticks={result['ticks']}; "
        f"events_loaded={result['events_loaded']}"
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
