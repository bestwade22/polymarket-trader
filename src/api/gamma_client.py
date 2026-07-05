import logging
import time
from typing import Any, Optional

import requests

from config.settings import GAMMA_API_BASE
from src.utils.city_parser import is_highest_temperature_event
from src.utils.market_parser import get_outcome_prices, parse_json_field

logger = logging.getLogger(__name__)


class GammaClient:
    def __init__(self, base_url: str = GAMMA_API_BASE, page_size: int = 100):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.session = requests.Session()

    def fetch_events_page(self, offset: int = 0, **params: Any) -> list[dict]:
        query = {
            "active": "true",
            "closed": "false",
            "limit": self.page_size,
            "offset": offset,
            **params,
        }
        url = f"{self.base_url}/events"
        resp = self.session.get(url, params=query, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("data", data.get("events", []))

    def fetch_all_active_events(self, max_pages: int = 100, **params: Any) -> list[dict]:
        all_events: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            page = self.fetch_events_page(offset=offset, **params)
            if not page:
                break
            all_events.extend(page)
            if len(page) < self.page_size:
                break
            offset += self.page_size
            time.sleep(0.15)
        logger.info("Fetched %d events (offset up to %d)", len(all_events), offset)
        return all_events

    def fetch_highest_temperature_events(self) -> list[dict]:
        """Fetch all active highest-temperature events via weather tag pagination."""
        raw = self.fetch_all_active_events(tag_slug="weather")
        filtered = [e for e in raw if is_highest_temperature_event(e.get("title", ""))]

        if len(filtered) < 10:
            logger.info("Weather tag returned few events; scanning all active events")
            raw_all = self.fetch_all_active_events()
            for event in raw_all:
                if is_highest_temperature_event(event.get("title", "")):
                    filtered.append(event)

        seen: dict[str, dict] = {}
        for event in filtered:
            eid = str(event.get("id", ""))
            if eid:
                seen[eid] = event

        result = list(seen.values())
        logger.info("Found %d unique highest-temperature events", len(result))
        return result

    def fetch_event_by_id(self, event_id: str) -> Optional[dict]:
        url = f"{self.base_url}/events/{event_id}"
        resp = self.session.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
        return None

    def fetch_market_by_id(self, market_id: str) -> Optional[dict]:
        url = f"{self.base_url}/markets/{market_id}"
        resp = self.session.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
        return None

    def search_events(self, query: str, limit: int = 100) -> list[dict]:
        url = f"{self.base_url}/public-search"
        resp = self.session.get(
            url,
            params={"q": query, "limit": limit, "events_status": "active"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("events", data) if isinstance(data, dict) else data

    def fetch_event_by_slug(self, slug: str) -> Optional[dict]:
        """Fetch event by slug (active or closed)."""
        url = f"{self.base_url}/events"
        resp = self.session.get(url, params={"slug": slug}, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            events = data.get("data", data.get("events", []))
            if isinstance(events, list) and events:
                return events[0]
            return data
        return None


def get_winning_market(event: dict) -> Optional[dict]:
    """Return the market whose Yes outcome resolved to ~1.0."""
    markets = event.get("markets") or []
    for market in markets:
        if not market.get("closed"):
            continue
        prices = get_outcome_prices(market)
        if not prices:
            continue
        try:
            outcomes = parse_json_field(market.get("outcomes", []))
        except (TypeError, ValueError):
            outcomes = []
        yes_index = 0
        for i, outcome in enumerate(outcomes):
            if str(outcome).lower() == "yes":
                yes_index = i
                break
        yes_price = prices[yes_index] if yes_index < len(prices) else prices[0]
        if yes_price is not None and yes_price >= 0.99:
            return market
    return None


def winning_temp_label(event: dict) -> Optional[str]:
    market = get_winning_market(event)
    if not market:
        return None
    return str(market.get("groupItemTitle") or market.get("title") or "").strip() or None
