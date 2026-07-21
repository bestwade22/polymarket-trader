"""Load or fetch daily weather event files for simulation."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from config.settings import events_file_for_date
from src.fetch.daily_events import run_daily_fetch

logger = logging.getLogger(__name__)


def date_range(start: date, end: date) -> list[date]:
    """Inclusive date range."""
    if end < start:
        return []
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def default_sim_date_range(*, today: Optional[date] = None) -> tuple[date, date]:
    """Last 7 calendar days ending yesterday."""
    day = today or date.today()
    end = day - timedelta(days=1)
    start = end - timedelta(days=6)
    return start, end


def load_events_for_date(
    target: date,
    *,
    fetch_if_missing: bool = True,
) -> list[dict]:
    """Load data/events_YYYY-MM-DD.json; fetch via daily pipeline if missing."""
    path = events_file_for_date(target)
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed reading %s: %s", path, exc)
            data = None
        if isinstance(data, list):
            logger.info("Loaded %d events from %s", len(data), path.name)
            return data

    if not fetch_if_missing:
        logger.warning("No events file for %s and fetch disabled", target.isoformat())
        return []

    logger.info("Events file missing for %s; fetching daily weather events", target.isoformat())
    return run_daily_fetch(target_date=target)
