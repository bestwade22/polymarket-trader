#!/usr/bin/env python3
"""Exit 0 when any event in dated events files is in the sell-win local window; else exit 1."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.trade.sell_win import active_sell_win_tier


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


def event_in_sell_win_window(event: dict, now_utc: datetime) -> bool:
    tier, reason = active_sell_win_tier(event, now_utc=now_utc)
    return tier is not None and reason == "ok"


def evaluate_sell_win_gate(
    data_dir: Path,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    eligible: list[dict] = []
    for file_date in event_file_dates_to_check(now):
        for event in load_events_file(data_dir, file_date):
            if event_in_sell_win_window(event, now):
                eligible.append(
                    {
                        "city": event.get("city"),
                        "event_date": event.get("event_date"),
                        "timezone": event.get("timezone"),
                    }
                )
    return {
        "now_utc": now.isoformat(),
        "eligible_count": len(eligible),
        "eligible_events": eligible,
        "should_run": len(eligible) > 0,
    }


def main() -> int:
    from config.settings import DATA_DIR

    result = evaluate_sell_win_gate(DATA_DIR)
    print(json.dumps(result, indent=2))
    return 0 if result["should_run"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
