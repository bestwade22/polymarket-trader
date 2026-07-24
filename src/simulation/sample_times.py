"""City-local sample timestamps inside the trading window."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.utils.time_window import trading_window_bounds_utc


def sample_local_minutes_in_window(
    *,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    sample_minutes: tuple[int, ...] = (15, 45),
) -> list[tuple[int, int]]:
    """Return [(hour, minute), ...] for :15/:45 samples inside the local window.

    Default window 14:00–16:00 → 14:15, 14:45, 15:15, 15:45.
    """
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    if end_total < start_total:
        return []

    result: list[tuple[int, int]] = []
    for total in range(start_total, end_total + 1):
        minute = total % 60
        if minute in sample_minutes:
            result.append((total // 60, minute))
    return result


def sample_times_utc_for_event(
    event: dict,
    *,
    sample_minutes: tuple[int, ...] = (15, 45),
) -> list[datetime]:
    """UTC datetimes for city-local :15/:45 samples inside the trading window."""
    from config.settings import settings

    event_date = event.get("event_date")
    tz_name = event.get("timezone")
    if not event_date or not tz_name:
        return []

    bounds = trading_window_bounds_utc(str(event_date), str(tz_name))
    if not bounds:
        return []
    start_utc, end_utc = bounds

    pairs = sample_local_minutes_in_window(
        start_hour=settings.trading_window_start_hour,
        start_minute=settings.trading_window_start_minute,
        end_hour=settings.trading_window_end_hour,
        end_minute=settings.trading_window_end_minute,
        sample_minutes=sample_minutes,
    )
    try:
        tz = ZoneInfo(str(tz_name))
        day = datetime.strptime(str(event_date), "%Y-%m-%d").date()
    except (ValueError, KeyError):
        return []

    out: list[datetime] = []
    for hour, minute in pairs:
        local = datetime(day.year, day.month, day.day, hour, minute, 0, tzinfo=tz)
        utc = local.astimezone(timezone.utc)
        if start_utc <= utc <= end_utc:
            out.append(utc)
    return out


def format_sample_time_local(dt_utc: datetime, tz_name: str) -> str:
    """HH:MM in city local time."""
    try:
        return dt_utc.astimezone(ZoneInfo(tz_name)).strftime("%H:%M")
    except Exception:
        return ""
