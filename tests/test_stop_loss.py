"""Tests for stop-loss helpers."""

import pytest

from src.trade.stop_loss import (
    is_stop_loss_eligible_event,
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
    def test_at_threshold(self):
        trigger, reason, pct = should_stop_loss(0.60, 0.30, 50.0)
        assert trigger is True
        assert reason == "below_threshold"
        assert pct == pytest.approx(50.0)

    def test_above_threshold(self):
        trigger, reason, pct = should_stop_loss(0.60, 0.31, 50.0)
        assert trigger is False
        assert reason == "above_threshold"
        assert pct == pytest.approx(51.666, rel=1e-3)

    def test_below_threshold(self):
        trigger, reason, _ = should_stop_loss(0.60, 0.20, 50.0)
        assert trigger is True
        assert reason == "below_threshold"
