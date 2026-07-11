"""Tests for sell-win helpers."""

from datetime import datetime, timezone

import pytest

from src.trade.sell_win import (
    active_sell_win_tier,
    is_sell_win_eligible_event,
    is_sell_win_price_eligible,
    sell_win_expiration_utc,
    sell_win_order_price,
)


class TestIsSellWinEligibleEvent:
    def test_matches_event_slug(self):
        assert is_sell_win_eligible_event(
            event_slug="highest-temperature-in-nyc-on-june-28"
        )

    def test_rejects_politics_slug(self):
        assert not is_sell_win_eligible_event(event_slug="will-candidate-x-win")


class TestSellWinOrderPrice:
    def test_uses_floor_when_current_lower(self):
        assert sell_win_order_price(0.91, 0.88) == pytest.approx(0.91)

    def test_uses_current_when_higher(self):
        assert sell_win_order_price(0.91, 0.95) == pytest.approx(0.95)


class TestIsSellWinPriceEligible:
    def test_rejects_at_or_below_threshold(self):
        assert not is_sell_win_price_eligible(0.1)
        assert not is_sell_win_price_eligible(0.05)

    def test_accepts_above_threshold(self):
        assert is_sell_win_price_eligible(0.11)


class TestActiveSellWinTier:
    def _event(self) -> dict:
        return {
            "event_date": "2026-06-28",
            "timezone": "America/New_York",
        }

    def test_before_window_skips(self):
        tier, reason = active_sell_win_tier(
            self._event(),
            now_utc=datetime(2026, 6, 28, 18, 0, tzinfo=timezone.utc),  # 14:00 ET
        )
        assert tier is None
        assert reason == "before_sell_win_window"

    def test_tier1_after_3pm(self):
        tier, reason = active_sell_win_tier(
            self._event(),
            now_utc=datetime(2026, 6, 28, 19, 30, tzinfo=timezone.utc),  # 15:30 ET
        )
        assert tier is not None
        assert tier.name == "tier1"
        assert reason == "ok"

    def test_tier2_after_4pm(self):
        tier, reason = active_sell_win_tier(
            self._event(),
            now_utc=datetime(2026, 6, 28, 20, 30, tzinfo=timezone.utc),  # 16:30 ET
        )
        assert tier is not None
        assert tier.name == "tier2"
        assert reason == "ok"

    def test_tier3_after_5pm(self):
        tier, reason = active_sell_win_tier(
            self._event(),
            now_utc=datetime(2026, 6, 28, 21, 30, tzinfo=timezone.utc),  # 17:30 ET
        )
        assert tier is not None
        assert tier.name == "tier3"
        assert reason == "ok"

    def test_after_window_skips(self):
        tier, reason = active_sell_win_tier(
            self._event(),
            now_utc=datetime(2026, 6, 28, 22, 30, tzinfo=timezone.utc),  # 18:30 ET
        )
        assert tier is None
        assert reason == "after_sell_win_window"


class TestSellWinExpirationUtc:
    def _event(self) -> dict:
        return {
            "event_date": "2026-06-28",
            "timezone": "America/New_York",
        }

    def test_returns_expiry_before_next_tier_hour(self):
        tier, _ = active_sell_win_tier(
            self._event(),
            now_utc=datetime(2026, 6, 28, 19, 30, tzinfo=timezone.utc),  # 15:30 ET
        )
        assert tier is not None
        expiration = sell_win_expiration_utc(
            self._event(),
            tier,
            now_utc=datetime(2026, 6, 28, 19, 30, tzinfo=timezone.utc),
        )
        assert expiration is not None
        expires_at = datetime.fromtimestamp(expiration, tz=timezone.utc)
        assert expires_at.hour == 19
        assert expires_at.minute == 55

    def test_past_expiry_returns_none(self):
        tier, _ = active_sell_win_tier(
            self._event(),
            now_utc=datetime(2026, 6, 28, 19, 56, tzinfo=timezone.utc),  # 15:56 ET
        )
        assert tier is not None
        assert tier.name == "tier1"
        expiration = sell_win_expiration_utc(
            self._event(),
            tier,
            now_utc=datetime(2026, 6, 28, 19, 56, tzinfo=timezone.utc),
        )
        assert expiration is None
