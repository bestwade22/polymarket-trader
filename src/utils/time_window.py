from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional, Union
from zoneinfo import ZoneInfo

from config.settings import settings


def _format_local_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def trading_window_duration(
    start_hour: Optional[int] = None,
    start_minute: Optional[int] = None,
    end_hour: Optional[int] = None,
    end_minute: Optional[int] = None,
) -> timedelta:
    """Length of the configured trading window."""
    sh = settings.trading_window_start_hour if start_hour is None else start_hour
    sm = settings.trading_window_start_minute if start_minute is None else start_minute
    eh = settings.trading_window_end_hour if end_hour is None else end_hour
    em = settings.trading_window_end_minute if end_minute is None else end_minute
    start_total = sh * 60 + sm
    end_total = eh * 60 + em
    return timedelta(minutes=end_total - start_total)


def trading_window_label(
    start_hour: Optional[int] = None,
    start_minute: Optional[int] = None,
    end_hour: Optional[int] = None,
    end_minute: Optional[int] = None,
) -> str:
    """Human-readable local window, e.g. 12:30–15:00."""
    sh = settings.trading_window_start_hour if start_hour is None else start_hour
    sm = settings.trading_window_start_minute if start_minute is None else start_minute
    eh = settings.trading_window_end_hour if end_hour is None else end_hour
    em = settings.trading_window_end_minute if end_minute is None else end_minute
    return f"{_format_local_time(sh, sm)}–{_format_local_time(eh, em)}"


def trading_window_bounds_utc(
    event_date_str: str,
    tz_name: str,
    *,
    start_hour: Optional[int] = None,
    start_minute: Optional[int] = None,
    end_hour: Optional[int] = None,
    end_minute: Optional[int] = None,
) -> Optional[tuple[datetime, datetime]]:
    """Return (window_start_utc, window_end_utc) for the event date in city local time."""
    sh = settings.trading_window_start_hour if start_hour is None else start_hour
    sm = settings.trading_window_start_minute if start_minute is None else start_minute
    eh = settings.trading_window_end_hour if end_hour is None else end_hour
    em = settings.trading_window_end_minute if end_minute is None else end_minute
    try:
        event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
        tz = ZoneInfo(tz_name)
        local_start = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            sh,
            sm,
            0,
            tzinfo=tz,
        )
        if eh == 24:
            local_end = datetime(
                event_date.year,
                event_date.month,
                event_date.day,
                0,
                0,
                0,
                tzinfo=tz,
            ) + timedelta(days=1)
        else:
            local_end = datetime(
                event_date.year,
                event_date.month,
                event_date.day,
                eh,
                em,
                0,
                tzinfo=tz,
            )
        return (
            local_start.astimezone(timezone.utc),
            local_end.astimezone(timezone.utc),
        )
    except (ValueError, KeyError):
        return None


def compute_city_noon_utc(event_date_str: str, tz_name: str) -> Optional[str]:
    """UTC ISO time when the trading window opens."""
    bounds = trading_window_bounds_utc(event_date_str, tz_name)
    if bounds:
        return bounds[0].isoformat()
    return None


def is_in_trading_window(city_noon_utc: str, now_utc: Optional[datetime] = None) -> bool:
    """True when now is within the configured trading window starting at city_noon_utc."""
    if not city_noon_utc:
        return False
    now = now_utc or datetime.now(timezone.utc)
    try:
        start = datetime.fromisoformat(city_noon_utc.replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        end = start + trading_window_duration()
        return start <= now < end
    except ValueError:
        return False


def any_city_in_trading_window(
    timezones: Iterable[str],
    event_dates: Iterable[Union[date, str]],
    now_utc: Optional[datetime] = None,
) -> bool:
    """True when now falls inside the trading window for any timezone and event date."""
    now = now_utc or datetime.now(timezone.utc)
    seen_tz: set[str] = set()
    for tz_name in timezones:
        if not tz_name or tz_name in seen_tz:
            continue
        seen_tz.add(tz_name)
        for raw_date in event_dates:
            event_date_str = raw_date.isoformat() if isinstance(raw_date, date) else str(raw_date)
            bounds = trading_window_bounds_utc(event_date_str, tz_name)
            if not bounds:
                continue
            start, end = bounds
            if start <= now < end:
                return True
    return False


def is_event_in_trading_window(event: dict, now_utc: Optional[datetime] = None) -> bool:
    """True when now is within the trading window for this event's city and date."""
    event_date = event.get("event_date")
    tz_name = event.get("timezone")
    if event_date and tz_name:
        bounds = trading_window_bounds_utc(event_date, tz_name)
        if bounds:
            start, end = bounds
            now = now_utc or datetime.now(timezone.utc)
            return start <= now < end
    return is_in_trading_window(event.get("city_noon_utc", ""), now_utc)


def next_trading_window_hint(events: list[dict], now_utc: Optional[datetime] = None) -> str:
    """Human-readable hint for the nearest upcoming city trading window."""
    now = now_utc or datetime.now(timezone.utc)
    upcoming: list[tuple[datetime, datetime, str]] = []
    for event in events:
        event_date = event.get("event_date")
        tz_name = event.get("timezone")
        if event_date and tz_name:
            bounds = trading_window_bounds_utc(event_date, tz_name)
            if not bounds:
                continue
            start, end = bounds
        else:
            raw = event.get("city_noon_utc")
            if not raw:
                continue
            try:
                start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                end = start + trading_window_duration()
            except ValueError:
                continue
        if start > now:
            upcoming.append((start, end, str(event.get("city", "?"))))

    if not upcoming:
        return "All city trading windows for this date have passed."

    upcoming.sort()
    start, end, city = upcoming[0]
    return (
        f"Next window: {city} at {start.strftime('%H:%M')}–{end.strftime('%H:%M')} UTC "
        f"(local {trading_window_label()})."
    )
