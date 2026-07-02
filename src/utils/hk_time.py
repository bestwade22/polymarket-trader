"""Hong Kong time helpers for human-readable logs."""

import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

HK_TZ = ZoneInfo("Asia/Hong_Kong")
LOG_TZ_LABEL = "HKT"


def to_hk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(HK_TZ)


def format_hk(dt: datetime, *, with_date: bool = True) -> str:
    """Format a datetime in Hong Kong time for logs."""
    local = to_hk(dt)
    if with_date:
        return local.strftime(f"%Y-%m-%d %H:%M:%S {LOG_TZ_LABEL}")
    return local.strftime(f"%H:%M {LOG_TZ_LABEL}")


def format_hk_range(start: datetime, end: datetime) -> str:
    """Format a time range in Hong Kong time, e.g. 23:30–01:30 HKT."""
    s = to_hk(start)
    e = to_hk(end)
    return f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')} {LOG_TZ_LABEL}"


def utc_clock_label(hour: int, minute: int = 0) -> str:
    """Convert a UTC clock time to an HKT label for scheduler logs."""
    dt = datetime.now(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return format_hk(dt, with_date=False)


class HKTFormatter(logging.Formatter):
    """Log timestamps in Hong Kong time."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=HK_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def apply_hkt_log_formatter(logger: Optional[logging.Logger] = None) -> None:
    """Attach HKT timestamps to all handlers on the given logger (default: root)."""
    target = logger or logging.getLogger()
    formatter = HKTFormatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    for handler in target.handlers:
        handler.setFormatter(formatter)
