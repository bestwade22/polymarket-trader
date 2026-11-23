"""Tests for the shared sell-order context helper."""

from __future__ import annotations

from src.trade.sell_order_context import highest_yes_status, sell_row_context


def _market(market_id: str, token_id: str, midpoint: float) -> dict:
    return {
        "id": market_id,
        "clobTokenIds": f'["{token_id}"]',
        "midpoint": midpoint,
        "bestBid": midpoint - 0.01,
        "bestAsk": midpoint + 0.01,
    }


def _event(markets: list[dict]) -> dict:
    return {"id": "ev1", "markets": markets}


def test_held_market_is_top_yes():
    event = _event([_market("m1", "tok1", 0.70), _market("m2", "tok2", 0.20)])
    assert highest_yes_status(event, "tok1", market_id="m1") is True


def test_held_market_is_not_top_yes():
    event = _event([_market("m1", "tok1", 0.20), _market("m2", "tok2", 0.70)])
    assert highest_yes_status(event, "tok1", market_id="m1") is False


def test_matched_by_market_id_when_token_missing():
    event = _event([_market("m1", "tok1", 0.70), _market("m2", "tok2", 0.20)])
    assert highest_yes_status(event, "", market_id="m1") is True
    assert highest_yes_status(event, "", market_id="m2") is False


def test_held_market_missing_from_event_is_not_top():
    event = _event([_market("m2", "tok2", 0.70)])
    assert highest_yes_status(event, "tok1", market_id="m1") is False


def test_unknown_when_event_has_no_markets():
    assert highest_yes_status({"id": "ev1", "markets": []}, "tok1") is None
    assert highest_yes_status(None, "tok1") is None


def test_unknown_when_no_market_has_a_price():
    event = _event([{"id": "m1", "clobTokenIds": '["tok1"]'}])
    assert highest_yes_status(event, "tok1", market_id="m1") is None


def test_sell_row_context_reports_mid_and_top_flag():
    event = _event([_market("m1", "tok1", 0.70), _market("m2", "tok2", 0.20)])
    market = _market("m1", "tok1", 0.70)
    ctx = sell_row_context(event, market, token_id="tok1", market_id="m1")
    assert ctx["current_mid"] == 0.70
    assert ctx["is_highest_yes"] is True
