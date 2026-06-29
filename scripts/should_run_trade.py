#!/usr/bin/env python3
"""Exit 0 when any event in dated events files is in its local trading window; else exit 1."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.time_window import any_city_in_trading_window, is_event_tradable_now  # noqa: E402


def load_city_timezones(data_dir: Path) -> set[str]:
    zones: set[str] = set()

    coords_path = data_dir / "city_coords.json"
    if coords_path.exists():
        for row in json.loads(coords_path.read_text()):
            tz = row.get("timezone")
            if tz:
                zones.add(str(tz))

    for events_path in sorted(data_dir.glob("events_*.json"), reverse=True):
        try:
            events = json.loads(events_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for event in events:
            tz = event.get("timezone")
            if tz:
                zones.add(str(tz))
        if zones:
            break

    return zones


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


def should_run_trade(
    now_utc: Optional[datetime] = None,
    data_dir: Optional[Path] = None,
) -> bool:
    """True when at least one event in today/yesterday files is in its trading window."""
    now = now_utc or datetime.now(timezone.utc)
    root = data_dir or PROJECT_ROOT / "data"

    loaded_any = False
    for file_date in event_file_dates_to_check(now):
        events = load_events_file(root, file_date)
        if not events:
            continue
        loaded_any = True
        if tradable_events(events, now):
            return True

    if loaded_any:
        return False

    # No events files: fall back to timezone heuristic for UTC today only.
    zones = load_city_timezones(root)
    if not zones:
        return True
    return any_city_in_trading_window(zones, [now.date()], now_utc=now)


def main() -> None:
    if should_run_trade():
        print("Trading window active for at least one event.")
        raise SystemExit(0)
    print("No tradable events in dated files; skipping trade run.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
