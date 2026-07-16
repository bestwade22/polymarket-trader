"""Tests for cool-edge detection."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.edge import cooler_markets, is_on_edge


def _market(title: str, yes: float) -> dict:
    return {
        "groupItemTitle": title,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{yes}", "{1 - yes}"]',
        "clobTokenIds": f'["tok_{title}", "tok_no_{title}"]',
    }


def test_is_on_edge_when_all_cooler_below_one_pct():
    markets = [
        _market("20°C", 0.005),
        _market("21°C", 0.008),
        _market("22°C", 0.40),
        _market("23°C", 0.45),
    ]
    assert is_on_edge(markets, "22°C") is True
    assert is_on_edge(markets, "23°C") is False  # 22°C is cooler and >= 1%


def test_is_on_edge_false_when_cooler_still_alive():
    markets = [
        _market("21°C", 0.05),
        _market("22°C", 0.40),
        _market("23°C", 0.45),
    ]
    assert is_on_edge(markets, "22°C") is False


def test_lowest_bucket_is_on_edge():
    markets = [
        _market("16°C or below", 0.30),
        _market("17°C", 0.40),
    ]
    assert cooler_markets(markets, "16°C or below") == []
    assert is_on_edge(markets, "16°C or below") is True
