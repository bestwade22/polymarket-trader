"""Tests for selection-snapshot spread lookup."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.spread_lookup import (
    compute_spread,
    load_selection_spreads_by_token,
    lookup_spread_for_buy,
)


def test_compute_spread():
    assert compute_spread(0.28, 0.52) == 0.24
    assert compute_spread(None, 0.52) is None
    assert compute_spread(0.6, 0.5) is None


def test_lookup_spread_closest_to_bought_at(tmp_path: Path):
    earlier = {
        "run_at": "2026-07-16T12:00:00+00:00",
        "selections": [
            {
                "yes_token_id": "tokA",
                "best_bid": 0.40,
                "best_ask": 0.50,
            }
        ],
    }
    later = {
        "run_at": "2026-07-16T13:00:00+00:00",
        "selections": [
            {
                "yes_token_id": "tokA",
                "best_bid": 0.30,
                "best_ask": 0.60,
            }
        ],
    }
    (tmp_path / "markets_yes_2026-07-16_1200.json").write_text(json.dumps(earlier))
    (tmp_path / "markets_yes_2026-07-16_1300.json").write_text(json.dumps(later))

    index = load_selection_spreads_by_token(tmp_path)
    # Closer to 13:00 snapshot
    assert lookup_spread_for_buy("tokA", "2026-07-16T12:55:00+00:00", index=index) == 0.3
    # Closer to 12:00 snapshot
    assert lookup_spread_for_buy("tokA", "2026-07-16T12:05:00+00:00", index=index) == 0.1
