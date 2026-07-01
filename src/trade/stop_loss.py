"""Stop-loss eligibility and threshold helpers."""

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
