"""Tests for market price parsing and current-probability ranking."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.trade.strategies.highest_yes import HighestYesStrategy
from src.utils.market_parser import get_order_price, get_selection_price, get_yes_price, parse_float


def _austin_event() -> dict:
    """Two-bucket event where stale lastTradePrice would pick the wrong market."""
    return {
        "id": "585545",
        "city": "Austin",
        "markets": [
            {
                "id": "2512768",
                "groupItemTitle": "92-93°F",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.54", "0.46"]',
                "lastTradePrice": 0.54,
                "bestBid": 0.51,
                "bestAsk": 0.57,
                "midpoint": 0.44,
                "clobTokenIds": '["37829671649456783086006498888190174876860954148606454621189935360267741292821"]',
                "negRisk": True,
                "orderMinSize": 5,
            },
            {
                "id": "2512769",
                "groupItemTitle": "94-95°F",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.43", "0.57"]',
                "lastTradePrice": 0.58,
                "bestBid": 0.39,
                "bestAsk": 0.47,
                "midpoint": 0.43,
                "clobTokenIds": '["80872150123917055559003952599669184888919561184894745056180550880113160624861"]',
                "negRisk": True,
                "orderMinSize": 5,
            },
        ],
    }


def test_get_yes_price_prefers_outcome_prices_over_stale_last_trade():
    event = _austin_event()
    leader, runner_up = event["markets"]

    assert get_yes_price(leader) == 0.54
    assert get_yes_price(runner_up) == 0.43
    assert get_yes_price(runner_up) != runner_up["lastTradePrice"]


def test_highest_yes_picks_when_clob_and_gamma_agree():
    strategy = HighestYesStrategy(yes_price_max=0.60, share_count=10)
    sel = strategy.select_market(_austin_event())

    assert sel is not None
    assert sel.group_item_title == "92-93°F"
    assert sel.yes_price == 0.44


def test_highest_yes_skips_when_clob_and_gamma_disagree():
    """Buenos Aires-style: wide mid on cooler bucket vs higher Gamma on warmer."""
    event = {
        "id": "704717",
        "city": "Buenos Aires",
        "markets": [
            {
                "id": "2929401",
                "groupItemTitle": "22°C",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.275", "0.725"]',
                "bestBid": 0.28,
                "bestAsk": 0.52,
                "midpoint": 0.40,
                "clobTokenIds": '["token_22_yes", "token_22_no"]',
                "negRisk": True,
                "orderMinSize": 5,
            },
            {
                "id": "2929402",
                "groupItemTitle": "23°C",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.44", "0.56"]',
                "bestBid": 0.42,
                "bestAsk": 0.48,
                "midpoint": 0.35,
                "clobTokenIds": '["token_23_yes", "token_23_no"]',
                "negRisk": True,
                "orderMinSize": 5,
            },
        ],
    }
    strategy = HighestYesStrategy(yes_price_max=0.60, share_count=10)
    assert strategy.select_market(event) is None


def test_get_yes_price_falls_back_to_book_midpoint():
    market = {
        "outcomes": '["Yes", "No"]',
        "bestBid": 0.40,
        "bestAsk": 0.50,
    }
    assert get_yes_price(market) == 0.45


def test_get_yes_price_ignores_empty_outcome_prices():
    market = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["", "0.52"]',
        "bestBid": 0.40,
        "bestAsk": 0.50,
    }
    assert get_yes_price(market) == 0.45


def test_get_order_price_midpoint_and_ask():
    market = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.48", "0.52"]',
        "bestBid": 0.33,
        "bestAsk": 0.49,
        "clobBuyPrice": 0.49,
        "midpoint": 0.41,
    }
    assert get_order_price(market, "midpoint") == 0.41
    assert get_order_price(market, "buy_price") == 0.49
    assert get_order_price(market, "yes_price") == 0.48


def test_get_selection_price_uses_book_not_gamma():
    market = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.60", "0.40"]',
        "bestBid": 0.36,
        "bestAsk": 0.50,
        "midpoint": 0.43,
    }
    assert get_selection_price(market, "midpoint") == 0.43
    assert get_yes_price(market) == 0.60


def test_parse_float_treats_blank_strings_as_missing():
    assert parse_float("") is None
    assert parse_float("  ") is None
    assert parse_float("0.48") == 0.48


if __name__ == "__main__":
    test_parse_float_treats_blank_strings_as_missing()
    test_get_yes_price_prefers_outcome_prices_over_stale_last_trade()
    test_highest_yes_picks_when_clob_and_gamma_agree()
    test_highest_yes_skips_when_clob_and_gamma_disagree()
    test_get_yes_price_falls_back_to_book_midpoint()
    test_get_yes_price_ignores_empty_outcome_prices()
    test_get_order_price_midpoint_and_ask()
    test_get_selection_price_uses_book_not_gamma()
    print("All tests passed.")
