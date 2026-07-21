"""Build synthetic event markets at a historical sample time from Yes-% history."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from src.simulation.price_at_time import PriceHistoryStore
from src.simulation.snapshot_enrichment import SnapshotEnrichment, lookup_enrichment_near
from src.utils.market_parser import get_yes_token_id


def _outcome_prices_for_yes(yes_price: float) -> list[str]:
    yes = max(0.0, min(1.0, yes_price))
    no = max(0.0, min(1.0, 1.0 - yes))
    return [str(round(yes, 4)), str(round(no, 4))]


def build_event_at_time(
    event: dict,
    at: datetime,
    store: PriceHistoryStore,
    *,
    history_start_ts: int,
    history_end_ts: int,
    enrichment_index: Optional[dict[str, list[SnapshotEnrichment]]] = None,
) -> tuple[dict, bool]:
    """Return (event_copy with markets priced at `at`, any_gamma_proxy).

    Midpoint / buy price come from CLOB prices-history.
    Gamma outcomePrices come from nearest markets_yes_* when available; else
    proxied from the same history % (gamma_proxy=True).
    Spread / bid / ask attached only when snapshot enrichment has them.
    """
    target_ts = int(at.timestamp())
    markets_out: list[dict] = []
    any_proxy = False

    for market in event.get("markets") or []:
        if not isinstance(market, dict):
            continue
        token_id = get_yes_token_id(market)
        if not token_id:
            continue
        pct = store.price_near(
            token_id,
            target_ts,
            start_ts=history_start_ts,
            end_ts=history_end_ts,
        )
        if pct is None:
            continue

        enriched = dict(market)
        enriched["midpoint"] = pct
        enriched["clobBuyPrice"] = pct
        enriched["lastTradePrice"] = pct
        # Required for get_gamma_yes_price
        if not enriched.get("outcomes"):
            enriched["outcomes"] = json.dumps(["Yes", "No"])

        snap = lookup_enrichment_near(token_id, at, index=enrichment_index)
        used_proxy = True
        if snap is not None:
            if snap.best_bid is not None:
                enriched["bestBid"] = snap.best_bid
            if snap.best_ask is not None:
                enriched["bestAsk"] = snap.best_ask
            if snap.spread is not None and snap.best_bid is None and snap.best_ask is None:
                half = snap.spread / 2.0
                enriched["bestBid"] = round(max(0.0, pct - half), 4)
                enriched["bestAsk"] = round(min(1.0, pct + half), 4)

            if snap.outcome_prices is not None:
                enriched["outcomePrices"] = snap.outcome_prices
                used_proxy = False
            elif snap.gamma_yes_price is not None:
                enriched["outcomePrices"] = _outcome_prices_for_yes(snap.gamma_yes_price)
                used_proxy = False

        if used_proxy:
            enriched["outcomePrices"] = _outcome_prices_for_yes(pct)
            any_proxy = True

        if isinstance(enriched.get("outcomePrices"), list):
            enriched["outcomePrices"] = json.dumps(enriched["outcomePrices"])

        markets_out.append(enriched)

    event_copy = dict(event)
    event_copy["markets"] = markets_out
    return event_copy, any_proxy
