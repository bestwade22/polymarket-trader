"""Polymarket Data API client for wallet positions, activity, and closed positions."""

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from config.settings import settings
from src.trade.position_checker import MIN_POSITION_SHARES
from src.utils.market_parser import parse_float

logger = logging.getLogger(__name__)

HIGHEST_TEMP_SLUG_PREFIX = "highest-temperature-in-"
ACTIVITY_PAGE_SIZE = 500
CLOSED_POSITIONS_PAGE_SIZE = 50


def is_highest_temp_slug(slug: str) -> bool:
    return str(slug or "").startswith(HIGHEST_TEMP_SLUG_PREFIX)


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

    def fetch_user_activity_page(
        self,
        wallet_address: str,
        *,
        types: Optional[list[str]] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        offset: int = 0,
        limit: int = ACTIVITY_PAGE_SIZE,
        sort_direction: str = "ASC",
    ) -> list[dict[str, Any]]:
        if not wallet_address:
            raise ValueError("wallet address required")

        params: dict[str, Any] = {
            "user": wallet_address,
            "limit": limit,
            "offset": offset,
            "sortBy": "TIMESTAMP",
            "sortDirection": sort_direction,
        }
        if types:
            params["type"] = ",".join(types)
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end

        url = f"{self.base_url}/activity"
        resp = self.session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def fetch_all_user_activity(
        self,
        wallet_address: str,
        *,
        types: Optional[list[str]] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        sort_direction: str = "ASC",
        highest_temp_only: bool = True,
    ) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.fetch_user_activity_page(
                wallet_address,
                types=types,
                start=start,
                end=end,
                offset=offset,
                sort_direction=sort_direction,
            )
            if not page:
                break
            for row in page:
                if highest_temp_only and not is_highest_temp_slug(
                    str(row.get("eventSlug") or row.get("event_slug") or "")
                ):
                    continue
                all_rows.append(row)
            if len(page) < ACTIVITY_PAGE_SIZE:
                break
            offset += ACTIVITY_PAGE_SIZE
            time.sleep(0.1)
        logger.info(
            "Fetched %d highest-temp activity rows for wallet %s",
            len(all_rows),
            wallet_address[:10],
        )
        return all_rows

    def fetch_closed_positions_page(
        self,
        wallet_address: str,
        *,
        offset: int = 0,
        limit: int = CLOSED_POSITIONS_PAGE_SIZE,
        sort_by: str = "TIMESTAMP",
        sort_direction: str = "DESC",
    ) -> list[dict[str, Any]]:
        if not wallet_address:
            raise ValueError("wallet address required")

        params = {
            "user": wallet_address,
            "limit": limit,
            "offset": offset,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        }
        url = f"{self.base_url}/closed-positions"
        resp = self.session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def fetch_all_closed_positions(
        self,
        wallet_address: str,
        *,
        highest_temp_only: bool = True,
    ) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.fetch_closed_positions_page(wallet_address, offset=offset)
            if not page:
                break
            for row in page:
                if highest_temp_only and not is_highest_temp_slug(
                    str(row.get("eventSlug") or row.get("event_slug") or "")
                ):
                    continue
                all_rows.append(row)
            if len(page) < CLOSED_POSITIONS_PAGE_SIZE:
                break
            offset += CLOSED_POSITIONS_PAGE_SIZE
            time.sleep(0.1)
        logger.info(
            "Fetched %d highest-temp closed positions for wallet %s",
            len(all_rows),
            wallet_address[:10],
        )
        return all_rows


def fetch_user_positions(wallet_address: Optional[str] = None) -> list[LivePosition]:
    wallet = wallet_address or settings.deposit_wallet_address
    return DataClient().fetch_user_positions(wallet)


def fetch_all_user_activity(
    wallet_address: Optional[str] = None,
    *,
    types: Optional[list[str]] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    sort_direction: str = "ASC",
    highest_temp_only: bool = True,
) -> list[dict[str, Any]]:
    wallet = wallet_address or settings.deposit_wallet_address
    return DataClient().fetch_all_user_activity(
        wallet,
        types=types or ["TRADE", "REDEEM"],
        start=start,
        end=end,
        sort_direction=sort_direction,
        highest_temp_only=highest_temp_only,
    )


def fetch_all_closed_positions(
    wallet_address: Optional[str] = None,
    *,
    highest_temp_only: bool = True,
) -> list[dict[str, Any]]:
    wallet = wallet_address or settings.deposit_wallet_address
    return DataClient().fetch_all_closed_positions(wallet, highest_temp_only=highest_temp_only)
