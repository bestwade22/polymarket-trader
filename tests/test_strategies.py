"""Tests using sample event data from poly weather/event.json."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.trade.strategies.forecast_match import ForecastMatchStrategy
from src.trade.strategies.highest_yes import HighestYesStrategy
from src.utils.city_parser import parse_city_from_title, parse_event_date_from_title
from src.utils.market_parser import get_selection_price, match_temp_to_market
from src.utils.time_window import compute_city_noon_utc, is_in_trading_window

SAMPLE_EVENT_PATH = PROJECT_ROOT.parent / "poly weather" / "event.json"


def load_sample_event() -> dict:
    events = json.loads(SAMPLE_EVENT_PATH.read_text())
    event = events[0]
    event["city"] = parse_city_from_title(event["title"])
    event["event_date"] = parse_event_date_from_title(event["title"]).isoformat()
    event["timezone"] = "America/Los_Angeles"
    event["city_noon_utc"] = compute_city_noon_utc(event["event_date"], event["timezone"])
    return event


def test_parse_city():
    assert parse_city_from_title("Highest temperature in Seattle on January 25?") == "Seattle"


def test_highest_yes_selects_max_below_threshold():
    event = load_sample_event()
    strategy = HighestYesStrategy(yes_price_max=0.60, share_count=10)
    sel = strategy.select_market(event)
    assert sel is not None
    assert sel.group_item_title == "42-43°F"
    assert sel.yes_price == get_selection_price(sel.market)


def test_highest_yes_rejects_at_threshold_after_live_refresh():
    event = load_sample_event()
    strategy = HighestYesStrategy(yes_price_max=0.45, share_count=10)
    sel = strategy.select_market(event)
    assert sel is not None
    sel.yes_price = 0.50
    filtered, skipped = strategy.filter_by_yes_price_max([sel])
    assert filtered == []
    assert skipped[0]["reason"] == "yes_price_max"


def test_filter_by_spread_max_skips_wide_spread():
    from src.trade.selector import filter_by_spread_max
    from src.trade.strategies.base import MarketSelection

    wide = MarketSelection(
        event_id="1",
        city="Test",
        market_id="m1",
        group_item_title="22°C",
        yes_price=0.40,
        yes_token_id="tok",
        buy_price=0.52,
        share_count=10,
        neg_risk=True,
        tick_size=0.01,
        order_min_size=5,
        strategy="highest_yes",
        market={"bestBid": 0.28, "bestAsk": 0.52},
    )
    ok = MarketSelection(
        event_id="2",
        city="Test",
        market_id="m2",
        group_item_title="23°C",
        yes_price=0.44,
        yes_token_id="tok2",
        buy_price=0.48,
        share_count=10,
        neg_risk=True,
        tick_size=0.01,
        order_min_size=5,
        strategy="highest_yes",
        market={"bestBid": 0.42, "bestAsk": 0.48},
    )
    kept, skipped = filter_by_spread_max([wide, ok], spread_max=0.15)
    assert [s.market_id for s in kept] == ["m2"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "spread_max"
    assert skipped[0]["spread"] == 0.24


def test_filter_by_spread_max_allows_at_threshold():
    from src.trade.selector import filter_by_spread_max
    from src.trade.strategies.base import MarketSelection

    sel = MarketSelection(
        event_id="1",
        city="Test",
        market_id="m1",
        group_item_title="22°C",
        yes_price=0.40,
        yes_token_id="tok",
        buy_price=0.50,
        share_count=10,
        neg_risk=True,
        tick_size=0.01,
        order_min_size=5,
        strategy="highest_yes",
        market={"bestBid": 0.35, "bestAsk": 0.50},
    )
    kept, skipped = filter_by_spread_max([sel], spread_max=0.15)
    assert kept == [sel]
    assert skipped == []


def test_match_temp_to_bucket():
    event = load_sample_event()
    market = match_temp_to_market(event["markets"], 46)
    assert market is not None
    assert market["groupItemTitle"] == "46-47°F"


def test_trading_window():
    event = load_sample_event()
    noon = datetime.fromisoformat(event["city_noon_utc"].replace("Z", "+00:00"))
    assert is_in_trading_window(event["city_noon_utc"], noon + timedelta(minutes=30))
    assert not is_in_trading_window(event["city_noon_utc"], noon + timedelta(hours=3))


def test_forecast_match_with_mock():
    event = load_sample_event()
    strategy = ForecastMatchStrategy(share_count=10)
    with patch.object(strategy.weather_client, "fetch_forecast_max_temp_f", return_value=46):
        sel = strategy.select_market(event)
    assert sel is not None
    assert sel.group_item_title == "46-47°F"
    assert sel.forecast_temp_f == 46


if __name__ == "__main__":
    test_parse_city()
    test_highest_yes_selects_max_below_threshold()
    test_highest_yes_rejects_at_threshold_after_live_refresh()
    test_match_temp_to_bucket()
    test_trading_window()
    test_forecast_match_with_mock()
    print("All tests passed.")
