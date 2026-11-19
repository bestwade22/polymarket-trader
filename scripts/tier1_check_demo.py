"""Offline walk-through of the Tier-1 trade-checking chain.

Runs the real filter functions (no network, no orders) over a cached events file
and prints, for each event, exactly which guard rejected it and why.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import src.analysis first: src.analysis.__init__ pulls in filter_sweep, which
# imports src.trade.city_skip. Importing city_skip first trips a circular import.
import src.analysis  # noqa: E402,F401

from config.settings import settings  # noqa: E402
from src.trade.city_skip import (  # noqa: E402
    filter_events_by_skip_timezones,
    resolve_skip_timezones,
)
from src.trade.selector import (  # noqa: E402
    filter_by_buy_band_high_yes_gap,
    filter_by_buy_band_low_local_time,
    filter_by_open_interest_min,
    filter_by_spread_max,
    filter_by_yes_gap_min,
    select_markets_for_events,
)
from src.trade.strategies.highest_yes import HighestYesStrategy  # noqa: E402
from src.utils.market_parser import get_spread, parse_float  # noqa: E402


def main() -> None:
    events_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if events_path is None:
        events_path = sorted(Path("data").glob("events_2026-*.json"))[-1]
    events = json.loads(events_path.read_text())

    print("=" * 78)
    print("Tier-1 trade check —", events_path)
    print("=" * 78)
    print(f"YES_PRICE_MIN        = {settings.yes_price_min}")
    print(f"YES_PRICE_MAX        = {settings.yes_price_max}")
    print(f"OPEN_INTEREST_MIN    = {settings.open_interest_min}")
    print(f"SPREAD_MAX           = {settings.spread_max} (0 = disabled)")
    print(f"YES_GAP_MIN          = {settings.yes_gap_min} (0 = disabled)")
    print(f"BUY_BAND_HIGH_GAP    = {settings.buy_band_high_yes_gap_min} (0 = disabled)")
    print(
        f"BUY_BAND_LOW         = [{settings.buy_band_low_min}, {settings.buy_band_low_max}]"
        " (disabled when max < min)"
    )
    print()

    # --- Step 1: timezone denylist -------------------------------------
    skip_tz = resolve_skip_timezones()
    eligible, skipped_tz = filter_events_by_skip_timezones(events, skip_tz)
    print(f"[1] skip_bottom7_tz          : {len(events)} -> {len(eligible)} events")
    print(f"    denylist = {skip_tz or '(none)'}")
    print()

    # --- Step 2: strategy selection ------------------------------------
    selections, skipped_select = select_markets_for_events(eligible, "highest_yes")
    print(f"[2] strategy select          : {len(eligible)} -> {len(selections)} selections")
    for row in skipped_select[:3]:
        print(f"    skip {row.get('city')}: {row.get('reason')}")
    print()

    strategy = HighestYesStrategy()

    def step(label, fn):
        nonlocal selections
        kept, skipped = fn(selections)
        print(f"[{label}]".ljust(24) + f": {len(selections)} -> {len(kept)} selections")
        for row in skipped[:3]:
            extra = {
                k: v
                for k, v in row.items()
                if k in ("open_interest", "spread", "yes_gap", "buy_price")
            }
            print(f"    skip {row.get('city')} {row.get('group_item_title')}: "
                  f"{row.get('reason')} {extra}")
        selections = kept
        print()

    # --- Step 3: price band --------------------------------------------
    step("3] buy>=0.45 / <0.70", lambda s: strategy.filter_by_yes_price_max(s))
    # --- Step 4: open interest -----------------------------------------
    step("4] oi>=10000", lambda s: filter_by_open_interest_min(s))
    # --- Step 5..7: optional guards (disabled in Tier-1) ---------------
    step("5] spread<max (disabled)", lambda s: filter_by_spread_max(s))
    step("6] yes_gap>min (disabled)", lambda s: filter_by_yes_gap_min(s))
    step("7] buy-band high (disabled)", lambda s: filter_by_buy_band_high_yes_gap(s))
    step("8] buy-band low (disabled)", lambda s: filter_by_buy_band_low_local_time(s))

    print("=" * 78)
    print(f"Final: {len(selections)} tradeable selections")
    for sel in selections:
        oi = parse_float((sel.event or {}).get("openInterest"))
        spread = get_spread(sel.market or {})
        print(
            f"  {sel.city:<16} {sel.group_item_title:<8} "
            f"buy={sel.buy_price:.3f} oi={oi if oi is None else round(oi)} "
            f"spread={spread if spread is None else round(spread, 3)}"
        )
    print("=" * 78)


if __name__ == "__main__":
    main()
