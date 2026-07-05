"""Tests for live position event filtering."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.trade.position_checker import (
    LivePositionChecker,
    compute_top_up_shares,
    event_position_holdings,
    filter_events_without_position,
    filter_selections_without_position,
    other_market_position_holding,
    parse_conditional_balance,
)
from src.trade.strategies.base import MarketSelection


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


class _FakeChecker(LivePositionChecker):
    def __init__(self, balances: dict[str, float]):
        super().__init__(executor=object())  # type: ignore[arg-type]
        self._balances = balances

    def get_yes_balance(self, token_id: str):
        if token_id not in self._balances:
            return 0.0
        return self._balances[token_id]


def test_compute_top_up_shares():
    assert compute_top_up_shares(0, 15, 5) == (False, 15, "no_position")
    assert compute_top_up_shares(10, 15, 5) == (False, 5, "partial_top_up")
    assert compute_top_up_shares(15, 15, 5) == (True, 0, "has_full_position")
    assert compute_top_up_shares(10.3, 15, 5) == (False, 5, "partial_top_up")


def test_parse_conditional_balance():
    assert parse_conditional_balance({"balance": "5000000"}) == 5.0
    assert parse_conditional_balance({"balance": ""}) == 0.0


def test_event_has_position_matches_any_market_in_city():
    checker = _FakeChecker({"token_yes_94": 10.0})
    has_position, holdings = checker.event_has_position(_miami_event())
    assert has_position is True
    assert len(holdings) == 1
    assert holdings[0]["market_id"] == "2646663"


def test_filter_events_without_position_keeps_clean_cities():
    checker = _FakeChecker({})
    kept, skipped = filter_events_without_position([_miami_event()], checker)
    assert len(kept) == 1
    assert skipped == []


def test_filter_events_without_position_skips_city_with_full_position():
    checker = _FakeChecker({"token_yes_92": 15.0})
    kept, skipped = filter_events_without_position([_miami_event()], checker)
    assert kept == []
    assert skipped[0]["reason"] == "has_full_position"
    assert skipped[0]["city"] == "Miami"


def test_filter_events_without_position_keeps_city_with_partial_position():
    checker = _FakeChecker({"token_yes_92": 5.0})
    kept, skipped = filter_events_without_position([_miami_event()], checker)
    assert len(kept) == 1
    assert skipped == []


def test_event_has_position_stops_after_first_hit():
    calls: list[str] = []

    class _TrackingChecker(LivePositionChecker):
        def get_yes_balance(self, token_id: str):
            calls.append(token_id)
            if token_id == "token_yes_92":
                return 10.0
            return 0.0

    checker = _TrackingChecker(executor=object())  # type: ignore[arg-type]
    has_position, holdings = checker.event_has_position(_miami_event())
    assert has_position is True
    assert len(holdings) == 1
    assert calls == ["token_yes_92"]


def _miami_selection(market_id: str = "2646663") -> MarketSelection:
    event = _miami_event()
    market = next(m for m in event["markets"] if m["id"] == market_id)
    token_id = "token_yes_94" if market_id == "2646663" else "token_yes_92"
    return MarketSelection(
        event_id="624144",
        city="Miami",
        market_id=market_id,
        group_item_title=market["groupItemTitle"],
        yes_price=0.45,
        yes_token_id=token_id,
        buy_price=0.46,
        share_count=15,
        neg_risk=True,
        tick_size="0.01",
        order_min_size=5,
        strategy="highest_yes",
        event=event,
        market=market,
    )


def test_event_position_holdings_splits_full_and_partial():
    checker = _FakeChecker({"token_yes_92": 10.0, "token_yes_94": 15.0})
    full, partial, unavailable = event_position_holdings(_miami_event(), checker, 15)
    assert unavailable is False
    assert len(full) == 1
    assert full[0]["market_id"] == "2646663"
    assert len(partial) == 1
    assert partial[0]["market_id"] == "2646662"


def test_filter_selections_skips_partial_on_other_market():
    checker = _FakeChecker({"token_yes_92": 10.0})
    kept, skipped = filter_selections_without_position([_miami_selection("2646663")], checker)
    assert kept == []
    assert skipped[0]["reason"] == "partial_on_other_market"
    assert skipped[0]["held_market_id"] == "2646662"


def test_filter_selections_top_up_when_partial_matches_selected_market():
    checker = _FakeChecker({"token_yes_94": 10.0})
    selection = _miami_selection("2646663")
    kept, skipped = filter_selections_without_position([selection], checker)
    assert skipped == []
    assert len(kept) == 1
    assert kept[0].share_count == 5


def test_filter_selections_skips_full_position_on_other_market():
    checker = _FakeChecker({"token_yes_92": 15.0})
    kept, skipped = filter_selections_without_position([_miami_selection("2646663")], checker)
    assert kept == []
    assert skipped[0]["reason"] == "has_full_position_other_market"
    assert skipped[0]["held_market_id"] == "2646662"


def _toronto_selection() -> MarketSelection:
    event = {
        "id": "624139",
        "city": "Toronto",
        "markets": [
            {
                "id": "2646606",
                "groupItemTitle": "24°C",
                "clobTokenIds": '["token_yes_24", "token_no_24"]',
            },
        ],
    }
    return MarketSelection(
        event_id="624139",
        city="Toronto",
        market_id="2646606",
        group_item_title="24°C",
        yes_price=0.425,
        yes_token_id="token_yes_24",
        buy_price=0.51,
        share_count=10,
        neg_risk=True,
        tick_size="0.01",
        order_min_size=5,
        strategy="highest_yes",
        event=event,
        market=event["markets"][0],
    )


def test_filter_selections_without_position_skips_city_with_full_position():
    checker = _FakeChecker({"token_yes_24": 15.0})
    kept, skipped = filter_selections_without_position([_toronto_selection()], checker)
    assert kept == []
    assert skipped[0]["reason"] == "has_full_position"


def test_filter_selections_without_position_top_up_partial_position():
    checker = _FakeChecker({"token_yes_24": 10.0})
    selection = _toronto_selection()
    kept, skipped = filter_selections_without_position([selection], checker)
    assert skipped == []
    assert len(kept) == 1
    assert kept[0].share_count == 5


def test_filter_selections_checks_selected_market_first_for_full_position():
    calls: list[str] = []

    class _TrackingChecker(LivePositionChecker):
        def get_yes_balance(self, token_id: str):
            calls.append(token_id)
            if token_id == "token_yes_24":
                return 15.0
            return 0.0

    checker = _TrackingChecker(executor=object())  # type: ignore[arg-type]
    kept, skipped = filter_selections_without_position([_toronto_selection()], checker)
    assert kept == []
    assert skipped[0]["reason"] == "has_full_position"
    assert calls == ["token_yes_24"]


def test_filter_selections_scans_others_only_when_selected_empty():
    calls: list[str] = []

    class _TrackingChecker(LivePositionChecker):
        def get_yes_balance(self, token_id: str):
            calls.append(token_id)
            if token_id == "token_yes_92":
                return 10.0
            return 0.0

    checker = _TrackingChecker(executor=object())  # type: ignore[arg-type]
    kept, skipped = filter_selections_without_position([_miami_selection("2646663")], checker)
    assert kept == []
    assert skipped[0]["reason"] == "partial_on_other_market"
    assert calls == ["token_yes_94", "token_yes_92"]


def test_other_market_position_holding_excludes_selected():
    checker = _FakeChecker({"token_yes_92": 15.0, "token_yes_94": 5.0})
    full, partial, unavailable = other_market_position_holding(
        _miami_event(),
        checker,
        15,
        exclude_token_id="token_yes_94",
    )
    assert unavailable is False
    assert full is not None
    assert full["market_id"] == "2646662"
    assert partial is None


if __name__ == "__main__":
    test_compute_top_up_shares()
    test_parse_conditional_balance()
    test_event_has_position_matches_any_market_in_city()
    test_event_has_position_stops_after_first_hit()
    test_filter_events_without_position_keeps_clean_cities()
    test_filter_events_without_position_skips_city_with_full_position()
    test_filter_events_without_position_keeps_city_with_partial_position()
    test_event_position_holdings_splits_full_and_partial()
    test_filter_selections_skips_partial_on_other_market()
    test_filter_selections_top_up_when_partial_matches_selected_market()
    test_filter_selections_skips_full_position_on_other_market()
    test_filter_selections_without_position_skips_city_with_full_position()
    test_filter_selections_without_position_top_up_partial_position()
    test_filter_selections_checks_selected_market_first_for_full_position()
    test_filter_selections_scans_others_only_when_selected_empty()
    test_other_market_position_holding_excludes_selected()
    print("All tests passed.")
