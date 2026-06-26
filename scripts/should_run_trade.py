#!/usr/bin/env python3
"""Exit 0 when any city could be in its local trading window; else exit 1 (skip GHA run)."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.time_window import any_city_in_trading_window  # noqa: E402


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


def event_dates_to_check(now_utc: datetime) -> list[date]:
    today = now_utc.date()
    return [today, today - timedelta(days=1)]


def should_run_trade(now_utc: Optional[datetime] = None) -> bool:
    now = now_utc or datetime.now(timezone.utc)
    zones = load_city_timezones(PROJECT_ROOT / "data")
    if not zones:
        return True
    return any_city_in_trading_window(zones, event_dates_to_check(now), now_utc=now)


def main() -> None:
    if should_run_trade():
        print("Trading window active for at least one city timezone.")
        raise SystemExit(0)
    print("Outside global trading window; skipping trade run.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
