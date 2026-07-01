"""Tests for Polymarket Data API client."""

from src.api.data_client import LivePosition


class TestLivePositionFromApiRow:
    def test_parses_position(self):
        row = {
            "asset": "token123",
            "conditionId": "cond456",
            "size": 10,
            "avgPrice": 0.55,
            "title": "72-73°F",
            "eventSlug": "highest-temperature-in-nyc-on-june-28",
            "curPrice": 0.40,
            "currentValue": 9.98,
        }
        pos = LivePosition.from_api_row(row)
        assert pos is not None
        assert pos.token_id == "token123"
        assert pos.market_id == "cond456"
        assert pos.size == 10.0
        assert pos.avg_price == 0.55
        assert pos.event_slug == "highest-temperature-in-nyc-on-june-28"
        assert pos.cur_price == 0.40

    def test_skips_tiny_balance(self):
        row = {"asset": "t", "size": 0.001, "avgPrice": 0.5}
        assert LivePosition.from_api_row(row) is None

    def test_skips_missing_token(self):
        assert LivePosition.from_api_row({"size": 10, "avgPrice": 0.5}) is None

    def test_skips_redeemable(self):
        row = {
            "asset": "token123",
            "conditionId": "cond456",
            "size": 10,
            "avgPrice": 0.55,
            "redeemable": True,
            "currentValue": 0,
        }
        assert LivePosition.from_api_row(row) is None

    def test_skips_zero_value(self):
        row = {
            "asset": "token123",
            "conditionId": "cond456",
            "size": 10,
            "avgPrice": 0.55,
            "currentValue": 0,
        }
        assert LivePosition.from_api_row(row) is None
