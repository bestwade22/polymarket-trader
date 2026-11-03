"""Polymarket Data API v2 client for wallet positions, activity, and closed positions.

Data API v1 was retired on 2026-11-02 and now answers `410 Gone`. The v2
contract differs from v1 in four ways that matter here:

* Routes are prefixed with ``/v2`` and the response payload is wrapped in a
  ``data`` envelope (plus a ``pagination`` object for paginated routes).
* Pagination uses an opaque ``cursor`` instead of ``limit``/``offset``. The
  cursor keeps a consistent view of the feed while new rows arrive.
* Field casing is ``snake_case`` (``token_id``, ``condition_id``,
  ``event_slug``, ``realized_pnl``) instead of v1's ``camelCase``.
* The position lifecycle is folded into a single ``/v2/positions`` route with a
  ``status`` filter (``OPEN``, ``REDEEMABLE``, ``CLOSED``, ...), replacing the
  separate ``/closed-positions`` route.

Rows are normalized back to the v1-style keys the rest of the codebase already
consumes (``asset``, ``avgPrice``, ``curPrice``, ``realizedPnl``,
``conditionId``, ``eventSlug``, ``transactionHash``), so callers stay
unchanged.
"""

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
CLOSED_POSITIONS_PAGE_SIZE = 500
POSITIONS_PAGE_SIZE = 500

# v2 rejects any limit above 1000 with HTTP 400.
MAX_PAGE_SIZE = 1000

# v1 `/positions` sorted by the v1-only `current` key, which v2 rejects with
# HTTP 400. Map the v1 value onto a v2 sort key.
POSITION_SORT_BY = {
    "current": "CURRENT_VALUE",
    "value": "CURRENT_VALUE",
    "price": "PRICE",
    "timestamp": "TIMESTAMP",
    "realized_pnl": "REALIZED_PNL",
}

# Positions that are settled and awaiting a redeem still carry a non-zero
# size, but their shares are worth nothing. v1 dropped them via the
# `redeemable` flag; v2 keeps the same flag, so the same filter applies.
_REDEEMABLE_TRUE = ("1", "true", "yes")


def is_highest_temp_slug(slug: str) -> bool:
    return str(slug or "").startswith(HIGHEST_TEMP_SLUG_PREFIX)


def _is_redeemable(row: dict[str, Any]) -> bool:
    raw = row.get("redeemable")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in _REDEEMABLE_TRUE
    return False


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    """Return the first present (non-None) value among ``keys``."""
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        if _is_redeemable(row):
            return None

        token_id = str(row.get("token_id") or row.get("asset") or "").strip()
        if not token_id:
            return None

        # v2 reports the live holding as `current_size`; `size` is kept for
        # backwards compatibility with v1-shaped rows.
        size = parse_float(_first_value(row, "current_size", "size"))
        avg_price = parse_float(_first_value(row, "avg_price", "avgPrice"))
        if size is None or avg_price is None:
            return None
        if size < MIN_POSITION_SHARES:
            return None

        # Careful: numeric 0 is falsy, so don't use `or` chaining.
        current_value = parse_float(
            _first_value(
                row,
                "current_value",
                "currentValue",
                "value",
                "position_value",
                "positionValue",
            )
        )
        cur_price = parse_float(_first_value(row, "current_price", "curPrice"))
        if current_value is not None and current_value <= 0:
            return None
        if current_value is None and cur_price is not None and cur_price <= 0:
            return None

        condition_id = str(
            _first_value(row, "condition_id", "conditionId") or ""
        ).strip()
        # v2 has no per-market id on positions; `condition_id` is the closest
        # stable identifier and is what the ledger stores.
        market_id = str(_first_value(row, "market_id", "marketId") or condition_id)
        return cls(
            token_id=token_id,
            market_id=market_id,
            size=float(size),
            avg_price=float(avg_price),
            title=str(row.get("title") or ""),
            event_slug=str(_first_value(row, "event_slug", "eventSlug") or ""),
            event_id=str(_first_value(row, "event_id", "eventId") or ""),
            condition_id=condition_id or market_id,
            cur_price=cur_price,
        )


class DataClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.data_api_base).rstrip("/")
        self.session = requests.Session()

    # ------------------------------------------------------------------
    # Low-level cursor paging
    # ------------------------------------------------------------------
    def _fetch_page(
        self,
        path: str,
        params: dict[str, Any],
        *,
        timeout: int = 60,
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        """Fetch one v2 page and return ``(rows, next_cursor)``.

        v2 wraps the payload in a ``data`` envelope and reports the cursor to
        follow in ``pagination.next_cursor``. A documented miss is
        ``data: null`` (not an error), so an empty page simply yields no rows.
        """
        url = f"{self.base_url}/v2{path}"
        resp = self.session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()

        if isinstance(payload, list):
            # Tolerate a bare array in case the base URL points at a proxy.
            return [row for row in payload if isinstance(row, dict)], None
        if not isinstance(payload, dict):
            return [], None

        rows = payload.get("data")
        if rows is None:
            return [], None
        rows = [row for row in rows if isinstance(row, dict)]

        pagination = payload.get("pagination") or {}
        next_cursor = pagination.get("next_cursor")
        if not next_cursor:
            return rows, None
        return rows, str(next_cursor)

    def _fetch_all_pages(
        self,
        path: str,
        params: dict[str, Any],
        *,
        page_size: int,
        timeout: int = 60,
    ) -> list[dict[str, Any]]:
        """Walk every page of a v2 paginated route via opaque cursors."""
        limit = max(1, min(int(page_size), MAX_PAGE_SIZE))
        all_rows: list[dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            page_params = dict(params)
            page_params["limit"] = limit
            if cursor:
                page_params["cursor"] = cursor

            rows, next_cursor = self._fetch_page(path, page_params, timeout=timeout)
            if not rows:
                break
            all_rows.extend(rows)

            if not next_cursor:
                break
            cursor = next_cursor
            time.sleep(0.1)

        return all_rows

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------
    def fetch_user_positions(self, wallet_address: str, *, sort_by: str = "current") -> list[LivePosition]:
        if not wallet_address:
            raise ValueError("DEPOSIT_WALLET_ADDRESS not configured")

        # v2 folds the position lifecycle into one route. `status=OPEN` maps
        # to the old v1 `/positions` behaviour (open positions only). The
        # `redeemable` flag is filtered client-side because v2 ignores the
        # request parameter.
        params: dict[str, Any] = {"user": wallet_address, "status": "OPEN"}
        if sort_by:
            # v1 accepted `current`; v2 rejects it with HTTP 400 and only
            # accepts its own sort keys.
            v2_sort = POSITION_SORT_BY.get(sort_by, sort_by)
            if v2_sort:
                params["sortBy"] = v2_sort

        rows = self._fetch_all_pages(
            "/positions",
            params,
            page_size=POSITIONS_PAGE_SIZE,
            timeout=30,
        )

        positions: list[LivePosition] = []
        for row in rows:
            pos = LivePosition.from_api_row(row)
            if pos is not None:
                positions.append(pos)

        logger.info("Fetched %d live positions for wallet %s", len(positions), wallet_address[:10])
        return positions

    # ------------------------------------------------------------------
    # Activity
    # ------------------------------------------------------------------
    def fetch_user_activity_page(
        self,
        wallet_address: str,
        *,
        types: Optional[list[str]] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: int = ACTIVITY_PAGE_SIZE,
        sort_direction: str = "ASC",
    ) -> list[dict[str, Any]]:
        """Fetch a single page of wallet activity (v2 ``/v2/activity``)."""
        if not wallet_address:
            raise ValueError("wallet address required")

        params: dict[str, Any] = {
            "user": wallet_address,
            "limit": max(1, min(int(limit), MAX_PAGE_SIZE)),
            "sortBy": "TIMESTAMP",
            "sortDirection": sort_direction,
        }
        if types:
            params["type"] = ",".join(types)
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if cursor:
            params["cursor"] = cursor

        rows, _ = self._fetch_page("/activity", params, timeout=60)
        return [self._normalize_activity_row(row) for row in rows]

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
        params: dict[str, Any] = {
            "user": wallet_address,
            "sortBy": "TIMESTAMP",
            "sortDirection": sort_direction,
        }
        if types:
            params["type"] = ",".join(types)
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end

        raw_rows = self._fetch_all_pages(
            "/activity",
            params,
            page_size=ACTIVITY_PAGE_SIZE,
            timeout=60,
        )

        all_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            normalized = self._normalize_activity_row(row)
            if highest_temp_only and not is_highest_temp_slug(
                str(normalized.get("eventSlug") or normalized.get("event_slug") or "")
            ):
                continue
            all_rows.append(normalized)

        logger.info(
            "Fetched %d highest-temp activity rows for wallet %s",
            len(all_rows),
            wallet_address[:10],
        )
        return all_rows

    # ------------------------------------------------------------------
    # Closed positions
    # ------------------------------------------------------------------
    def fetch_closed_positions_page(
        self,
        wallet_address: str,
        *,
        cursor: Optional[str] = None,
        limit: int = CLOSED_POSITIONS_PAGE_SIZE,
        sort_by: str = "TIMESTAMP",
        sort_direction: str = "DESC",
    ) -> list[dict[str, Any]]:
        """Fetch a single page of closed positions (v2 ``/v2/positions``)."""
        if not wallet_address:
            raise ValueError("wallet address required")

        params: dict[str, Any] = {
            "user": wallet_address,
            "status": "CLOSED",
            "limit": max(1, min(int(limit), MAX_PAGE_SIZE)),
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        }
        if cursor:
            params["cursor"] = cursor

        rows, _ = self._fetch_page("/positions", params, timeout=60)
        return [self._normalize_position_row(row) for row in rows]

    def fetch_all_closed_positions(
        self,
        wallet_address: str,
        *,
        highest_temp_only: bool = True,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "user": wallet_address,
            "status": "CLOSED",
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        raw_rows = self._fetch_all_pages(
            "/positions",
            params,
            page_size=CLOSED_POSITIONS_PAGE_SIZE,
            timeout=60,
        )

        all_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            normalized = self._normalize_position_row(row)
            if highest_temp_only and not is_highest_temp_slug(
                str(normalized.get("eventSlug") or normalized.get("event_slug") or "")
            ):
                continue
            all_rows.append(normalized)

        logger.info(
            "Fetched %d highest-temp closed positions for wallet %s",
            len(all_rows),
            wallet_address[:10],
        )
        return all_rows

    # ------------------------------------------------------------------
    # Normalization: v2 snake_case -> the v1-shaped keys the codebase uses
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_activity_row(row: dict[str, Any]) -> dict[str, Any]:
        """Map a v2 activity row onto the v1 field names used downstream."""
        out = dict(row)
        token_id = _first_value(row, "token_id", "asset")
        if token_id is not None:
            out["asset"] = str(token_id)
        condition_id = _first_value(row, "condition_id", "conditionId")
        if condition_id is not None:
            out["conditionId"] = str(condition_id)
        event_slug = _first_value(row, "event_slug", "eventSlug")
        if event_slug is not None:
            out["eventSlug"] = str(event_slug)
        event_id = _first_value(row, "event_id", "eventId")
        if event_id is not None:
            out["eventId"] = str(event_id)
        tx_hash = _first_value(row, "transaction_hash", "transactionHash")
        if tx_hash is not None:
            out["transactionHash"] = str(tx_hash)
        usdc_size = _first_value(row, "usdc_size", "usdcSize")
        if usdc_size is not None:
            out["usdcSize"] = usdc_size
        return out

    @staticmethod
    def _normalize_position_row(row: dict[str, Any]) -> dict[str, Any]:
        """Map a v2 position row onto the v1 field names used downstream."""
        out = dict(row)
        token_id = _first_value(row, "token_id", "asset")
        if token_id is not None:
            out["asset"] = str(token_id)
        condition_id = _first_value(row, "condition_id", "conditionId")
        if condition_id is not None:
            out["conditionId"] = str(condition_id)
        event_slug = _first_value(row, "event_slug", "eventSlug")
        if event_slug is not None:
            out["eventSlug"] = str(event_slug)
        event_id = _first_value(row, "event_id", "eventId")
        if event_id is not None:
            out["eventId"] = str(event_id)
        cur_price = _first_value(row, "current_price", "curPrice")
        if cur_price is not None:
            out["curPrice"] = cur_price
        current_value = _first_value(row, "current_value", "currentValue")
        if current_value is not None:
            out["currentValue"] = current_value
        realized_pnl = _first_value(row, "realized_pnl", "realizedPnl")
        if realized_pnl is not None:
            out["realizedPnl"] = realized_pnl
        size = _first_value(row, "current_size", "size")
        if size is not None:
            out["size"] = size
        return out


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
