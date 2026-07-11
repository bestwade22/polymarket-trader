"""Sell-win tier selection, pricing, and expiry helpers."""

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from config.settings import settings

SELL_WIN_MIN_POSITION_PRICE = 0.1


@dataclass(frozen=True)
class SellWinTier:
    name: str
    start_local: time
    floor_price: float
    expire_before_local: time


def _local_time_from_settings(hour: int, minute: int) -> time:
    return time(hour=hour, minute=minute)


def build_sell_win_tiers() -> list[SellWinTier]:
    return [
        SellWinTier(
            name="tier1",
            start_local=_local_time_from_settings(
                settings.sold_win_tier1_start_hour,
                settings.sold_win_tier1_start_minute,
            ),
            floor_price=settings.sold_win_tier1_price,
            expire_before_local=_local_time_from_settings(
                settings.sold_win_tier1_expire_before_hour,
                settings.sold_win_tier1_expire_before_minute,
            ),
        ),
        SellWinTier(
            name="tier2",
            start_local=_local_time_from_settings(
                settings.sold_win_tier2_start_hour,
                settings.sold_win_tier2_start_minute,
            ),
            floor_price=settings.sold_win_tier2_price,
            expire_before_local=_local_time_from_settings(
                settings.sold_win_tier2_expire_before_hour,
                settings.sold_win_tier2_expire_before_minute,
            ),
        ),
        SellWinTier(
            name="tier3",
            start_local=_local_time_from_settings(
                settings.sold_win_tier3_start_hour,
                settings.sold_win_tier3_start_minute,
            ),
            floor_price=settings.sold_win_tier3_price,
            expire_before_local=_local_time_from_settings(
                settings.sold_win_tier3_expire_before_hour,
                settings.sold_win_tier3_expire_before_minute,
            ),
        ),
    ]


def is_sell_win_eligible_event(
    *,
    event_slug: str = "",
    title: str = "",
    slug: str = "",
) -> bool:
    """True when slug/title contains the configured highest-temperature marker."""
    marker = settings.sold_win_event_slug_marker.lower()
    haystacks = (event_slug, title, slug)
    return any(marker in (h or "").lower() for h in haystacks)


def _event_local_now(event: dict, now_utc: datetime) -> tuple[Optional[datetime], Optional[ZoneInfo], str]:
    event_date = event.get("event_date")
    tz_name = event.get("timezone")
    if not event_date or not tz_name:
        return None, None, "missing_event_timezone"
    try:
        event_day = datetime.strptime(str(event_date), "%Y-%m-%d").date()
        tz = ZoneInfo(str(tz_name))
        local_now = now_utc.astimezone(tz)
        if local_now.date() != event_day:
            return local_now, tz, "wrong_event_date"
        return local_now, tz, "ok"
    except (ValueError, KeyError):
        return None, None, "invalid_event_timezone"


def _time_to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _local_now_minutes(local_now: datetime) -> int:
    return local_now.hour * 60 + local_now.minute


def active_sell_win_tier(
    event: dict,
    now_utc: Optional[datetime] = None,
) -> tuple[Optional[SellWinTier], str]:
    """Return the active tier for city local time, or (None, reason)."""
    now = now_utc or datetime.now(timezone.utc)
    local_now, tz, reason = _event_local_now(event, now)
    if local_now is None or tz is None:
        return None, reason

    window_start = _time_to_minutes(
        _local_time_from_settings(
            settings.sold_win_window_start_hour,
            settings.sold_win_window_start_minute,
        )
    )
    window_end = _time_to_minutes(
        _local_time_from_settings(
            settings.sold_win_window_end_hour,
            settings.sold_win_window_end_minute,
        )
    )
    local_mins = _local_now_minutes(local_now)

    if local_mins < window_start:
        return None, "before_sell_win_window"
    if local_mins >= window_end:
        return None, "after_sell_win_window"

    active: Optional[SellWinTier] = None
    for tier in build_sell_win_tiers():
        if local_mins >= _time_to_minutes(tier.start_local):
            active = tier

    if active is None:
        return None, "before_first_tier"
    return active, "ok"


def sell_win_order_price(floor_price: float, current_price: float) -> float:
    """Return max(tier floor, current sell price)."""
    return max(floor_price, current_price)


def is_sell_win_price_eligible(current_price: float) -> bool:
    """Return False when position price is too low to place a sell-win order."""
    return current_price > SELL_WIN_MIN_POSITION_PRICE


def sell_win_expiration_utc(
    event: dict,
    tier: SellWinTier,
    now_utc: Optional[datetime] = None,
) -> Optional[int]:
    """Return GTD unix expiration for tier cutoff in city TZ, or None if past expiry."""
    now = now_utc or datetime.now(timezone.utc)
    local_now, tz, reason = _event_local_now(event, now)
    if local_now is None or tz is None:
        return None

    event_day = local_now.date()
    expire_local = datetime(
        event_day.year,
        event_day.month,
        event_day.day,
        tier.expire_before_local.hour,
        tier.expire_before_local.minute,
        0,
        tzinfo=tz,
    )
    if now >= expire_local.astimezone(timezone.utc):
        return None
    return int(expire_local.timestamp())
