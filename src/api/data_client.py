"""Polymarket Data API client for live wallet positions."""

import logging
from dataclasses import dataclass
from typing import Any, Optional

import requests

from config.settings import settings
from src.trade.position_checker import MIN_POSITION_SHARES
from src.utils.market_parser import parse_float

logger = logging.getLogger(__name__)


@dataclass
class LivePosition:
    token_id: str
    market_id: str
    size: float
    avg_price: float
    title: str
    event_slug: str
    event_id: str
    condition_id: str
    cur_price: Optional[float] = None

    @classmethod
    def from_api_row(cls, row: dict[str, Any]) -> Optional["LivePosition"]:
        # Skip resolved/redeemable or zero-value positions. The Data API can return
        # historical/dust rows that still have a non-zero `size` but no tradable value.
        redeemable_raw = row.get("redeemable")
        if isinstance(redeemable_raw, bool) and redeemable_raw:
            return None
        if isinstance(redeemable_raw, str) and redeemable_raw.strip().lower() in ("1", "true", "yes"):
            return None

        token_id = str(row.get("asset") or row.get("token_id") or "").strip()
        if not token_id:
            return None

        size = parse_float(row.get("size"))
        avg_price = parse_float(row.get("avgPrice") or row.get("avg_price"))
        if size is None or avg_price is None:
            return None
        if size < MIN_POSITION_SHARES:
            return None

        # Careful: numeric 0 is falsy, so don't use `or` chaining.
        current_value_raw = None
        for key in ("currentValue", "current_value", "value", "positionValue", "position_value"):
            if key in row:
                current_value_raw = row.get(key)
                break
        current_value = parse_float(current_value_raw)
        cur_price = parse_float(row.get("curPrice") or row.get("cur_price"))
        if current_value is not None and current_value <= 0:
            return None
        if current_value is None and cur_price is not None and cur_price <= 0:
            return None

        market_id = str(
            row.get("marketId")
            or row.get("market_id")
            or row.get("conditionId")
            or row.get("condition_id")
            or ""
        )
        return cls(
            token_id=token_id,
            market_id=market_id,
            size=float(size),
            avg_price=float(avg_price),
            title=str(row.get("title") or ""),
            event_slug=str(row.get("eventSlug") or row.get("event_slug") or ""),
            event_id=str(row.get("eventId") or row.get("event_id") or ""),
            condition_id=str(row.get("conditionId") or row.get("condition_id") or market_id),
            cur_price=cur_price,
        )


class DataClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.data_api_base).rstrip("/")
        self.session = requests.Session()

    def fetch_user_positions(self, wallet_address: str, *, sort_by: str = "current") -> list[LivePosition]:
        if not wallet_address:
            raise ValueError("DEPOSIT_WALLET_ADDRESS not configured")

        url = f"{self.base_url}/positions"
        params = {"user": wallet_address}
        if sort_by:
            params["sortBy"] = sort_by
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        rows: list[dict[str, Any]]
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("positions", data.get("data", []))
        else:
            rows = []

        positions: list[LivePosition] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            pos = LivePosition.from_api_row(row)
            if pos is not None:
                positions.append(pos)

        logger.info("Fetched %d live positions for wallet %s", len(positions), wallet_address[:10])
        return positions


def fetch_user_positions(wallet_address: Optional[str] = None) -> list[LivePosition]:
    wallet = wallet_address or settings.deposit_wallet_address
    return DataClient().fetch_user_positions(wallet)
