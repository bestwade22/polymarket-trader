"""Build trade records from wallet activity rows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.settings import DATA_DIR, settings
from src.analysis.models import TradeRecord
from src.analysis.resolution import CachedResolution, fetch_resolved_event, resolve_winning_temp
from src.api.clob_client import ClobPriceClient, first_price_below_threshold
from src.api.data_client import fetch_user_positions
from src.utils.city_parser import parse_city_from_slug, parse_city_from_title, parse_date_from_slug
from src.utils.hk_time import format_hk
from src.utils.market_parser import compare_temp_buckets, extract_temp_label, parse_float

logger = logging.getLogger(__name__)

PRICE_DROP_THRESHOLD = 0.01
MIN_POSITION_SHARES = 0.01


@dataclass
class PositionGroup:
    token_id: str
    condition_id: str
    event_slug: str
    title: str
    buy_fills: list[dict[str, Any]] = field(default_factory=list)
    sell_fills: list[dict[str, Any]] = field(default_factory=list)
    redeems: list[dict[str, Any]] = field(default_factory=list)

    @property
    def first_buy_ts(self) -> int:
        return min(int(r.get("timestamp") or 0) for r in self.buy_fills)

    @property
    def shares(self) -> float:
        return sum(parse_float(r.get("size")) or 0.0 for r in self.buy_fills)

    @property
    def sell_shares(self) -> float:
        return sum(parse_float(r.get("size")) or 0.0 for r in self.sell_fills)

    @property
    def buy_price(self) -> float:
        total_size = self.shares
        if total_size <= 0:
            return 0.0
        weighted = sum(
            (parse_float(r.get("size")) or 0.0) * (parse_float(r.get("price")) or 0.0)
            for r in self.buy_fills
        )
        return weighted / total_size

    @property
    def sell_price(self) -> Optional[float]:
        if not self.sell_fills:
            return None
        total_size = sum(parse_float(r.get("size")) or 0.0 for r in self.sell_fills)
        if total_size <= 0:
            return None
        weighted = sum(
            (parse_float(r.get("size")) or 0.0) * (parse_float(r.get("price")) or 0.0)
            for r in self.sell_fills
        )
        return weighted / total_size

    @property
    def sold_at_ts(self) -> Optional[int]:
        if not self.sell_fills:
            return None
        return max(int(r.get("timestamp") or 0) for r in self.sell_fills)

    @property
    def redeemed_at_ts(self) -> Optional[int]:
        if not self.redeems:
            return None
        return max(int(r.get("timestamp") or 0) for r in self.redeems)

    @property
    def transaction_hash(self) -> Optional[str]:
        for row in self.buy_fills:
            tx = row.get("transactionHash")
            if tx:
                return str(tx)
        return None


def _new_position_group(row: dict[str, Any], token_id: str) -> PositionGroup:
    return PositionGroup(
        token_id=token_id,
        condition_id=str(row.get("conditionId") or ""),
        event_slug=str(row.get("eventSlug") or row.get("event_slug") or ""),
        title=str(row.get("title") or ""),
    )


def group_activity_rows(activity: list[dict[str, Any]]) -> list[PositionGroup]:
    """Group wallet activity into position cycles (reset after each sell/redeem)."""
    by_token: dict[str, list[dict[str, Any]]] = {}
    for row in activity:
        token_id = str(row.get("asset") or "").strip()
        if not token_id:
            continue
        by_token.setdefault(token_id, []).append(row)

    groups: list[PositionGroup] = []
    for token_id, rows in by_token.items():
        rows.sort(key=lambda r: int(r.get("timestamp") or 0))
        current: Optional[PositionGroup] = None
        open_shares = 0.0

        for row in rows:
            row_type = str(row.get("type") or "").upper()
            side = str(row.get("side") or "").upper()
            size = parse_float(row.get("size")) or 0.0

            if row_type == "TRADE" and side == "BUY":
                if current is None:
                    current = _new_position_group(row, token_id)
                current.buy_fills.append(row)
                open_shares += size
            elif row_type == "TRADE" and side == "SELL":
                if current is None:
                    continue
                current.sell_fills.append(row)
                open_shares = max(0.0, open_shares - size)
                if open_shares <= MIN_POSITION_SHARES:
                    groups.append(current)
                    current = None
                    open_shares = 0.0
            elif row_type == "REDEEM":
                if current is None:
                    current = _new_position_group(row, token_id)
                current.redeems.append(row)
                groups.append(current)
                current = None
                open_shares = 0.0

        if current is not None and current.buy_fills:
            groups.append(current)

    return groups


def _iso_from_ts(ts: Optional[int]) -> Optional[str]:
    if ts is None or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


_city_tz_cache: Optional[dict[str, str]] = None


def _city_timezones() -> dict[str, str]:
    global _city_tz_cache
    if _city_tz_cache is None:
        path = DATA_DIR / "city_timezones.json"
        if path.exists():
            _city_tz_cache = json.loads(path.read_text())
        else:
            _city_tz_cache = {}
    return _city_tz_cache


def _format_bought_times(ts: int, city: str) -> tuple[str, str]:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    hk = format_hk(dt)
    tz_name = _city_timezones().get(city)
    if tz_name:
        local = dt.astimezone(ZoneInfo(tz_name)).strftime("%H:%M")
    else:
        local = ""
    return hk, local


def _trade_window_label() -> str:
    sh, sm = settings.trading_window_start_hour, settings.trading_window_start_minute
    eh, em = settings.trading_window_end_hour, settings.trading_window_end_minute
    return f"{sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"


def _closed_position_map(
    closed_positions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_asset: dict[str, dict[str, Any]] = {}
    for row in closed_positions:
        asset = str(row.get("asset") or "").strip()
        if asset:
            by_asset[asset] = row
    return by_asset


def _open_token_ids(wallet: str) -> set[str]:
    try:
        positions = fetch_user_positions(wallet)
        return {p.token_id for p in positions}
    except Exception as exc:
        logger.warning("Could not fetch open positions: %s", exc)
        return set()


def _classify_result(
    group: PositionGroup,
    *,
    closed_row: Optional[dict[str, Any]],
    open_tokens: set[str],
    resolution: Optional[CachedResolution],
) -> str:
    if group.redeems:
        return "win"
    if group.sell_fills:
        return "sold"
    if group.token_id in open_tokens:
        return "open"

    if resolution:
        if resolution.winning_token_id:
            if resolution.winning_token_id == group.token_id:
                return "win"
            return "loss"
        if not resolution.closed:
            return "open"

    if closed_row is not None:
        cur = parse_float(closed_row.get("curPrice"))
        if cur is not None and cur >= 0.99:
            return "win"
        if cur is not None and cur <= 0.01:
            return "loss"
        pnl = parse_float(closed_row.get("realizedPnl"))
        if pnl is not None:
            return "win" if pnl > 0 else "loss"

    return "open"


def _record_pnl(
    result: str,
    shares: float,
    buy_price: float,
    sell_price: Optional[float],
    realized_pnl: Optional[float],
) -> Optional[float]:
    if realized_pnl is not None:
        return round(realized_pnl, 4)
    cost = shares * buy_price
    if result == "win":
        return round(shares * 1.0 - cost, 4)
    if result == "loss":
        return round(-cost, 4)
    if result == "sold" and sell_price is not None:
        return round(shares * sell_price - cost, 4)
    return None


def _position_shares(group: PositionGroup, result: str) -> float:
    if result == "sold" and group.sell_shares > 0:
        return group.sell_shares
    return group.shares


def _held_hours(
    bought_ts: int,
    sold_ts: Optional[int],
    redeemed_ts: Optional[int],
) -> Optional[float]:
    end_ts = sold_ts or redeemed_ts
    if end_ts is None or end_ts <= bought_ts:
        return None
    return round((end_ts - bought_ts) / 3600.0, 2)


def build_trade_record(
    group: PositionGroup,
    *,
    closed_positions: list[dict[str, Any]],
    open_tokens: set[str],
    clob_client: Optional[ClobPriceClient] = None,
    fetch_price_drop: bool = True,
) -> TradeRecord:
    closed_map = _closed_position_map(closed_positions)
    closed_row = closed_map.get(group.token_id)
    resolution = fetch_resolved_event(group.event_slug)
    result = _classify_result(
        group, closed_row=closed_row, open_tokens=open_tokens, resolution=resolution
    )

    winning_temp = resolve_winning_temp(group.event_slug)
    bought_temp = extract_temp_label(group.title)
    win_temp_vs_bought = (
        compare_temp_buckets(bought_temp, winning_temp)
        if winning_temp
        else "unknown"
    )

    shares = _position_shares(group, result)
    buy_price = round(group.buy_price, 4)
    sell_price = group.sell_price
    if sell_price is not None:
        sell_price = round(sell_price, 4)
    cost_basis = round(shares * buy_price, 4)

    realized_pnl = parse_float(closed_row.get("realizedPnl")) if closed_row else None
    final_value = _record_pnl(result, shares, buy_price, sell_price, realized_pnl)
    sold_but_would_have_won = (
        result == "sold"
        and final_value is not None
        and final_value < 0
        and win_temp_vs_bought == "same"
    )
    roi_pct = (
        round((final_value / cost_basis) * 100, 2)
        if final_value is not None and cost_basis > 0
        else None
    )
    sell_value_pct = (
        round((sell_price / buy_price) * 100, 2)
        if sell_price is not None and buy_price > 0
        else None
    )

    bought_ts = group.first_buy_ts
    sold_ts = group.sold_at_ts
    redeemed_ts = group.redeemed_at_ts

    price_drop_at: Optional[str] = None
    if fetch_price_drop and result in ("loss", "sold") and clob_client is not None:
        history = clob_client.get_prices_history(
            group.token_id,
            start_ts=bought_ts,
            end_ts=sold_ts or int(datetime.now(timezone.utc).timestamp()),
        )
        drop_ts = first_price_below_threshold(
            history, threshold=PRICE_DROP_THRESHOLD, after_ts=bought_ts
        )
        price_drop_at = _iso_from_ts(drop_ts)

    event_date = parse_date_from_slug(group.event_slug)
    date_str = event_date.isoformat() if event_date else ""
    city = parse_city_from_slug(group.event_slug) or ""
    if not city and resolution:
        city = parse_city_from_title(resolution.title) or ""

    bought_at_hk, bought_at_local = _format_bought_times(bought_ts, city)
    sold_at_hk = ""
    if sold_ts:
        sold_at_hk = format_hk(datetime.fromtimestamp(sold_ts, tz=timezone.utc))
    price_drop_at_hk = ""
    if price_drop_at:
        try:
            drop_dt = datetime.fromisoformat(price_drop_at)
            price_drop_at_hk = format_hk(drop_dt)
        except ValueError:
            price_drop_at_hk = ""

    return TradeRecord(
        date=date_str,
        city=city,
        bought_temp=bought_temp,
        trade_window=_trade_window_label(),
        bought_at=_iso_from_ts(bought_ts) or "",
        bought_at_hk=bought_at_hk,
        bought_at_local=bought_at_local,
        sold_at=_iso_from_ts(sold_ts),
        sold_at_hk=sold_at_hk,
        redeemed_at=_iso_from_ts(redeemed_ts),
        shares=round(shares, 4),
        share_count_target=settings.share_count,
        shares_over_target=round(group.shares, 4) > float(settings.share_count) + 0.5,
        result=result,
        final_value_usd=final_value,
        winning_temp=winning_temp,
        win_temp_vs_bought=win_temp_vs_bought,
        price_drop_below_threshold_at=price_drop_at,
        price_drop_below_threshold_at_hk=price_drop_at_hk,
        sold_but_would_have_won=sold_but_would_have_won,
        buy_price=buy_price,
        sell_price=sell_price,
        cost_basis_usd=cost_basis,
        realized_pnl_usd=round(realized_pnl, 4) if realized_pnl is not None else None,
        roi_pct=roi_pct,
        sell_value_pct=sell_value_pct,
        held_hours=_held_hours(bought_ts, sold_ts, redeemed_ts),
        event_slug=group.event_slug,
        token_id=group.token_id,
        condition_id=group.condition_id,
        transaction_hash=group.transaction_hash,
    )


def build_records_from_activity(
    activity: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
    *,
    wallet: str,
    fetch_price_drop: bool = True,
) -> list[TradeRecord]:
    groups = group_activity_rows(activity)
    open_tokens = _open_token_ids(wallet)
    clob = ClobPriceClient() if fetch_price_drop else None
    records = [
        build_trade_record(
            group,
            closed_positions=closed_positions,
            open_tokens=open_tokens,
            clob_client=clob,
            fetch_price_drop=fetch_price_drop,
        )
        for group in groups
    ]
    records.sort(key=lambda r: r.bought_at, reverse=True)
    return records
