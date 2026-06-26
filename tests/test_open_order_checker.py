"""Tests for open-order event filtering."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.trade.open_order_checker import (
    LiveOpenOrderChecker,
    collect_event_yes_token_ids,
    filter_events_without_open_orders,
)


def _miami_event() -> dict:
    return {
        "id": "624144",
        "city": "Miami",
        "markets": [
            {
                "id": "2646662",
                "groupItemTitle": "92-93°F",
                "clobTokenIds": '["token_yes_92", "token_no_92"]',
            },
            {
                "id": "2646663",
                "groupItemTitle": "94-95°F",
                "clobTokenIds": '["token_yes_94", "token_no_94"]',
            },
        ],
    }


class _FakeChecker(LiveOpenOrderChecker):
    def __init__(self, orders: list[dict]):
        super().__init__(executor=object())  # type: ignore[arg-type]
        self._orders = orders

    def get_open_orders(self) -> list[dict]:
        return self._orders


def test_collect_event_yes_token_ids():
    assert collect_event_yes_token_ids(_miami_event()) == {"token_yes_92", "token_yes_94"}


def test_event_has_open_order_matches_any_market_in_city():
    checker = _FakeChecker(
        [{"asset_id": "token_yes_94", "side": "BUY", "id": "0xabc"}]
    )
    has_open, orders = checker.event_has_open_order(_miami_event())
    assert has_open is True
    assert len(orders) == 1


def test_filter_events_without_open_orders_keeps_clean_cities():
    checker = _FakeChecker([])
    kept, skipped = filter_events_without_open_orders([_miami_event()], checker)
    assert len(kept) == 1
    assert skipped == []


def test_filter_events_without_open_orders_skips_city_with_open_order():
    checker = _FakeChecker(
        [{"asset_id": "token_yes_92", "side": "BUY", "id": "0xdef"}]
    )
    kept, skipped = filter_events_without_open_orders([_miami_event()], checker)
    assert kept == []
    assert skipped[0]["reason"] == "open_order"
    assert skipped[0]["city"] == "Miami"


if __name__ == "__main__":
    test_collect_event_yes_token_ids()
    test_event_has_open_order_matches_any_market_in_city()
    test_filter_events_without_open_orders_keeps_clean_cities()
    test_filter_events_without_open_orders_skips_city_with_open_order()
    print("All tests passed.")
