import json
import logging
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config.settings import ensure_dirs, events_file_for_date, parse_event_date, settings
from src.api.gamma_client import GammaClient
from src.api.timezone_client import TimezoneClient
from src.utils.city_parser import parse_city_from_title, parse_event_date_from_title
from src.utils.time_window import compute_city_noon_utc

logger = logging.getLogger(__name__)


def get_resolution_date(event: dict) -> Optional[date]:
    """Event resolution date from endDate, endDateIso, or parsed title."""
    end_date = event.get("endDate")
    if end_date:
        try:
            return datetime.fromisoformat(end_date.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    end_iso = event.get("endDateIso")
    if end_iso:
        try:
            return datetime.strptime(end_iso, "%Y-%m-%d").date()
        except ValueError:
            pass
    return parse_event_date_from_title(event.get("title", ""))


def is_event_for_date(event: dict, target: date) -> bool:
    resolution = get_resolution_date(event)
    if resolution:
        return resolution == target
    parsed = parse_event_date_from_title(event.get("title", ""))
    return parsed == target if parsed else False


def enrich_event(event: dict, tz_client: TimezoneClient, target_date: date):
    title = event.get("title", "")
    city = parse_city_from_title(title)
    if not city:
        logger.debug("Skipping event %s: could not parse city from title", event.get("id"))
        return None

    if not is_event_for_date(event, target_date):
        return None

    tz_info = tz_client.get_timezone_for_city(city)
    if not tz_info or not tz_info.get("timezone"):
        logger.warning("Skipping event %s: no timezone for %s", event.get("id"), city)
        return None

    tz_name = tz_info["timezone"]
    event_date = get_resolution_date(event) or parse_event_date_from_title(title)
    if not event_date:
        event_date = target_date
    event_date_str = event_date.isoformat()
    city_noon_utc = compute_city_noon_utc(event_date_str, tz_name)
    window_start = (
        f"{settings.trading_window_start_hour:02d}:"
        f"{settings.trading_window_start_minute:02d}:00"
    )

    enriched = dict(event)
    enriched.update(
        {
            "city": city,
            "timezone": tz_name,
            "utc_offset_seconds": tz_info.get("utc_offset"),
            "city_noon_local": f"{event_date_str} {window_start}",
            "city_noon_utc": city_noon_utc,
            "event_date": event_date_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return enriched


def fetch_highest_temp_events_today(target_date: Optional[date] = None) -> list[dict]:
    target = target_date or date.today()
    gamma = GammaClient()
    tz_client = TimezoneClient()

    events = gamma.fetch_highest_temperature_events()
    logger.info(
        "Fetched %d highest-temperature events; filtering for %s",
        len(events),
        target.isoformat(),
    )

    seen_ids: set[str] = set()
    enriched: list[dict] = []
    skipped_no_tz = 0
    skipped_date = 0

    for event in events:
        eid = str(event.get("id", ""))
        if eid in seen_ids:
            continue
        seen_ids.add(eid)

        if not is_event_for_date(event, target):
            skipped_date += 1
            continue

        try:
            item = enrich_event(event, tz_client, target)
        except Exception as exc:
            logger.warning("Skipping event %s: %s", eid, exc)
            continue

        if item:
            enriched.append(item)
        else:
            city = parse_city_from_title(event.get("title", ""))
            if city and not tz_client.get_timezone_for_city(city):
                skipped_no_tz += 1

    logger.info(
        "Enriched %d events for %s (skipped %d wrong-date, %d no-timezone)",
        len(enriched),
        target.isoformat(),
        skipped_date,
        skipped_no_tz,
    )
    return enriched


def save_events(events: list[dict], target_date: Optional[date] = None, path: Optional[Path] = None) -> Path:
    ensure_dirs()
    out = path or events_file_for_date(target_date)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=out.parent, suffix=".tmp") as tmp:
        json.dump(events, tmp, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(out)
    logger.info("Saved %d events to %s", len(events), out)
    return out


def run_daily_fetch(target_date: Optional[date] = None) -> list[dict]:
    target = target_date or parse_event_date()
    events = fetch_highest_temp_events_today(target_date=target)
    save_events(events, target_date=target)
    return events
