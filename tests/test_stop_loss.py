"""Tests for stop-loss helpers."""

from datetime import datetime, timezone

import pytest

from src.trade.stop_loss import (
    is_stop_loss_eligible_event,
    is_stop_loss_local_time_eligible,
    should_stop_loss,
    value_percentage,
)


class TestValuePercentage:
    def test_half_value(self):
        assert value_percentage(0.30, 0.60) == pytest.approx(50.0)

    def test_requires_positive_avg(self):
        with pytest.raises(ValueError):
            value_percentage(0.5, 0.0)


class TestIsStopLossEligibleEvent:
    def test_matches_event_slug(self):
        assert is_stop_loss_eligible_event(
            event_slug="highest-temperature-in-nyc-on-june-28"
        )

    def test_rejects_politics_slug(self):
        assert not is_stop_loss_eligible_event(event_slug="will-candidate-x-win")

    def test_case_insensitive(self):
        assert is_stop_loss_eligible_event(title="Highest-Temperature-In-Seattle")


class TestShouldStopLoss:
    def test_at_threshold_skips(self):
        trigger, reason, pct = should_stop_loss(0.60, 0.30, 50.0)
        assert trigger is False
        assert reason == "above_threshold"
        assert pct == pytest.approx(50.0)

    def test_above_threshold_skips(self):
        trigger, reason, pct = should_stop_loss(0.60, 0.31, 50.0)
        assert trigger is False
        assert reason == "above_threshold"
        assert pct == pytest.approx(51.666, rel=1e-3)

    def test_below_floor_skips(self):
        trigger, reason, pct = should_stop_loss(0.60, 0.06, 50.0)
        assert trigger is False
        assert reason == "below_floor"
        assert pct == pytest.approx(10.0)

    def test_within_band_sells(self):
        trigger, reason, pct = should_stop_loss(0.60, 0.20, 50.0)
        assert trigger is True
        assert reason == "within_band"
        assert pct == pytest.approx(33.333, rel=1e-3)


class TestIsStopLossLocalTimeEligible:
    def _event(self) -> dict:
        return {
            "event_date": "2026-06-28",
            "timezone": "America/New_York",
        }

    def test_before_cutoff_skips(self):
        ok, reason = is_stop_loss_local_time_eligible(
            self._event(),
            now_utc=datetime(2026, 6, 28, 18, 0, tzinfo=timezone.utc),  # 14:00 ET
        )
        assert ok is False
        assert reason == "before_min_local_time"

    def test_at_cutoff_allows(self):
        ok, reason = is_stop_loss_local_time_eligible(
            self._event(),
            now_utc=datetime(2026, 6, 28, 20, 30, tzinfo=timezone.utc),  # 16:30 ET
        )
        assert ok is True
        assert reason == "ok"

    def test_after_cutoff_allows(self):
        ok, reason = is_stop_loss_local_time_eligible(
            self._event(),
            now_utc=datetime(2026, 6, 28, 21, 0, tzinfo=timezone.utc),  # 17:00 ET
        )
        assert ok is True
        assert reason == "ok"

    def test_missing_timezone(self):
        ok, reason = is_stop_loss_local_time_eligible({"event_date": "2026-06-28"})
        assert ok is False
        assert reason == "missing_event_timezone"
