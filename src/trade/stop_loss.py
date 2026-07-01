"""Stop-loss eligibility and threshold helpers."""

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from config.settings import settings


def value_percentage(current_price: float, avg_buy_price: float) -> float:
    if avg_buy_price <= 0:
        raise ValueError("avg_buy_price must be positive")
    return (current_price / avg_buy_price) * 100.0


def is_stop_loss_eligible_event(
    *,
    event_slug: str = "",
    title: str = "",
    slug: str = "",
) -> bool:
    """True when slug/title contains the configured highest-temperature marker."""
    marker = settings.stop_loss_event_slug_marker.lower()
    haystacks = (event_slug, title, slug)
    return any(marker in (h or "").lower() for h in haystacks)


def should_stop_loss(
    avg_price: float,
    current_mid: float,
    threshold_pct: float,
) -> tuple[bool, str, float]:
    """Return (trigger_sell, reason, value_pct)."""
    pct = value_percentage(current_mid, avg_price)
    if pct <= threshold_pct:
        return True, "below_threshold", pct
    return False, "above_threshold", pct


def is_stop_loss_local_time_eligible(
    event: dict,
    now_utc: Optional[datetime] = None,
) -> tuple[bool, str]:
    """Return (eligible, reason). Skip when city local time is before the configured cutoff."""
    event_date = event.get("event_date")
    tz_name = event.get("timezone")
    if not event_date or not tz_name:
        return False, "missing_event_timezone"

    now = now_utc or datetime.now(timezone.utc)
    try:
        event_day = datetime.strptime(str(event_date), "%Y-%m-%d").date()
        tz = ZoneInfo(str(tz_name))
        cutoff = datetime(
            event_day.year,
            event_day.month,
            event_day.day,
            settings.stop_loss_min_local_hour,
            settings.stop_loss_min_local_minute,
            0,
            tzinfo=tz,
        )
        local_now = now.astimezone(tz)
        if local_now < cutoff:
            return False, "before_min_local_time"
        return True, "ok"
    except (ValueError, KeyError):
        return False, "invalid_event_timezone"
