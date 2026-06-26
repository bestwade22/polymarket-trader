"""End-to-end dry-run test with sample event in noon window."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import ensure_dirs, events_file_for_date
from src.trade.hourly_runner import run_hourly_trade
from src.utils.city_parser import parse_city_from_title, parse_event_date_from_title
from src.utils.time_window import compute_city_noon_utc

SAMPLE_EVENT_PATH = PROJECT_ROOT.parent / "poly weather" / "event.json"


def main():
    ensure_dirs()
    events = json.loads(SAMPLE_EVENT_PATH.read_text())
    event = events[0]
    event["city"] = parse_city_from_title(event["title"])
    event["event_date"] = parse_event_date_from_title(event["title"]).isoformat()
    event["timezone"] = "America/Los_Angeles"
    now = datetime.now(timezone.utc)
    event["city_noon_utc"] = (now - timedelta(minutes=30)).isoformat()

    today = date.today()
    events_path = events_file_for_date(today)
    events_path.write_text(json.dumps([event], indent=2))

    with patch("src.trade.hourly_runner.refresh_events_markets", side_effect=lambda x: x), patch(
        "src.trade.hourly_runner.refresh_selection_prices", side_effect=lambda s: s
    ):
        result = run_hourly_trade(strategy_name="highest_yes", dry_run=True, target_date=today)
    assert result["selections"] >= 1, f"Expected selections, got {result}"
    assert result["orders"] >= 1
    print("Dry-run hourly trade OK:", result)


if __name__ == "__main__":
    main()
