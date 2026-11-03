"""Tests for Polymarket Data API v2 client."""

from typing import Any

import pytest

from src.api.data_client import DataClient, LivePosition


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


def _client_with_pages(pages: list[dict[str, Any]]) -> DataClient:
    """Return a DataClient whose session replays ``pages`` in order."""
    client = DataClient()
    calls: list[dict[str, Any]] = []
    pages_left = list(pages)

    def fake_get(url, params=None, timeout=None, **kwargs):
        calls.append({"url": url, "params": dict(params or {})})
        payload = pages_left.pop(0) if pages_left else {"data": [], "pagination": {}}
        return _FakeResponse(payload)

    client.session.get = fake_get  # type: ignore[method-assign]
    client.calls = calls  # type: ignore[attr-defined]
    return client


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


class TestLivePositionFromV2Row:
    """v2 renamed the fields to snake_case."""

    def test_parses_v2_position(self):
        row = {
            "token_id": "token123",
            "condition_id": "cond456",
            "current_size": 10,
            "avg_price": 0.55,
            "title": "72-73°F",
            "event_slug": "highest-temperature-in-nyc-on-june-28",
            "current_price": 0.40,
            "current_value": 9.98,
            "event_id": "1234",
        }
        pos = LivePosition.from_api_row(row)
        assert pos is not None
        assert pos.token_id == "token123"
        assert pos.condition_id == "cond456"
        assert pos.market_id == "cond456"
        assert pos.size == 10.0
        assert pos.avg_price == 0.55
        assert pos.event_slug == "highest-temperature-in-nyc-on-june-28"
        assert pos.event_id == "1234"
        assert pos.cur_price == 0.40

    def test_skips_redeemable_v2(self):
        row = {
            "token_id": "token123",
            "current_size": 10,
            "avg_price": 0.55,
            "redeemable": True,
            "current_value": 0,
        }
        assert LivePosition.from_api_row(row) is None

    def test_skips_zero_value_v2(self):
        row = {
            "token_id": "token123",
            "current_size": 10,
            "avg_price": 0.55,
            "current_value": 0,
        }
        assert LivePosition.from_api_row(row) is None


class TestV2Routes:
    def test_uses_v2_prefix(self):
        client = _client_with_pages([{"data": [], "pagination": {}}])
        client.fetch_user_positions("0xwallet")
        assert client.calls[0]["url"].endswith("/v2/positions")

    def test_positions_requests_open_status(self):
        client = _client_with_pages([{"data": [], "pagination": {}}])
        client.fetch_user_positions("0xwallet")
        assert client.calls[0]["params"]["status"] == "OPEN"

    def test_maps_v1_current_sort_to_v2_key(self):
        """v1 `sortBy=current` is rejected by v2 with HTTP 400."""
        client = _client_with_pages([{"data": [], "pagination": {}}])
        client.fetch_user_positions("0xwallet")
        assert client.calls[0]["params"]["sortBy"] == "CURRENT_VALUE"

    def test_activity_uses_v2_route_and_no_offset(self):
        client = _client_with_pages([{"data": [], "pagination": {}}])
        client.fetch_all_user_activity("0xwallet")
        assert client.calls[0]["url"].endswith("/v2/activity")
        assert "offset" not in client.calls[0]["params"]

    def test_closed_positions_use_status_closed(self):
        client = _client_with_pages([{"data": [], "pagination": {}}])
        client.fetch_all_closed_positions("0xwallet")
        assert client.calls[0]["url"].endswith("/v2/positions")
        assert client.calls[0]["params"]["status"] == "CLOSED"


class TestV2Pagination:
    def test_follows_cursor(self):
        pages = [
            {
                "data": [{"token_id": "a", "event_slug": "x"}],
                "pagination": {"next_cursor": "cur1", "has_more": True},
            },
            {
                "data": [{"token_id": "b", "event_slug": "y"}],
                "pagination": {"next_cursor": None, "has_more": False},
            },
        ]
        client = _client_with_pages(pages)
        rows = client.fetch_all_user_activity("0xwallet", highest_temp_only=False)
        assert len(rows) == 2
        assert client.calls[0]["params"].get("cursor") is None
        assert client.calls[1]["params"]["cursor"] == "cur1"

    def test_stops_when_cursor_is_null(self):
        pages = [
            {
                "data": [{"token_id": "a", "event_slug": "x"}],
                "pagination": {"next_cursor": None, "has_more": False},
            },
        ]
        client = _client_with_pages(pages)
        rows = client.fetch_all_user_activity("0xwallet", highest_temp_only=False)
        assert len(rows) == 1
        assert len(client.calls) == 1

    def test_data_null_is_empty_not_an_error(self):
        client = _client_with_pages([{"data": None, "pagination": {}}])
        rows = client.fetch_all_user_activity("0xwallet", highest_temp_only=False)
        assert rows == []

    def test_limit_is_capped_at_v2_max(self):
        client = _client_with_pages([{"data": [], "pagination": {}}])
        client._fetch_all_pages("/activity", {"user": "0xwallet"}, page_size=5000)
        assert client.calls[0]["params"]["limit"] <= 1000


class TestV2Normalization:
    def test_activity_row_exposes_v1_field_names(self):
        client = _client_with_pages(
            [
                {
                    "data": [
                        {
                            "token_id": "tok",
                            "condition_id": "cond",
                            "event_slug": "highest-temperature-in-nyc-on-june-28",
                            "transaction_hash": "0xtx",
                        }
                    ],
                    "pagination": {},
                }
            ]
        )
        rows = client.fetch_all_user_activity("0xwallet", highest_temp_only=False)
        row = rows[0]
        assert row["asset"] == "tok"
        assert row["conditionId"] == "cond"
        assert row["eventSlug"] == "highest-temperature-in-nyc-on-june-28"
        assert row["transactionHash"] == "0xtx"

    def test_closed_row_exposes_v1_field_names(self):
        client = _client_with_pages(
            [
                {
                    "data": [
                        {
                            "token_id": "tok",
                            "current_price": 1.0,
                            "realized_pnl": 4.5,
                            "event_slug": "highest-temperature-in-nyc-on-june-28",
                        }
                    ],
                    "pagination": {},
                }
            ]
        )
        rows = client.fetch_all_closed_positions("0xwallet")
        row = rows[0]
        assert row["asset"] == "tok"
        assert row["curPrice"] == 1.0
        assert row["realizedPnl"] == 4.5


class TestHighestTempFilter:
    def test_filters_non_temp_events(self):
        client = _client_with_pages(
            [
                {
                    "data": [
                        {"token_id": "a", "event_slug": "highest-temperature-in-nyc-on-june-28"},
                        {"token_id": "b", "event_slug": "will-ja-morant-be-traded"},
                    ],
                    "pagination": {},
                }
            ]
        )
        rows = client.fetch_all_user_activity("0xwallet")
        assert len(rows) == 1
        assert rows[0]["asset"] == "a"
