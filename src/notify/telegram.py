"""Telegram digests for buy skips/orders and sell-win / stop-loss runs."""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from config.settings import DATA_DIR, settings

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LEN = 4096
TELEGRAM_PARSE_MODE = "HTML"

STOP_LOSS_IDLE_SKIP_REASONS = frozenset(
    {
        "above_threshold",
        "below_floor",
        "before_min_local_time",
        "not_temp_market",
    }
)

SELL_WIN_IDLE_SKIP_REASONS = frozenset(
    {
        "before_sell_win_window",
        "after_sell_win_window",
        "price_too_low",
        "not_temp_market",
        "past_tier_expiry",
    }
)


def telegram_configured() -> bool:
    if not settings.telegram_notify_enabled:
        return False
    return bool(settings.telegram_bot_token.strip() and str(settings.telegram_chat_id).strip())


def chunk_message(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LEN) -> list[str]:
    text = text.rstrip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    lines = text.split("\n")
    current: list[str] = []
    current_len = 0
    for line in lines:
        # +1 for the newline when joining (except first line in chunk)
        add_len = len(line) + (1 if current else 0)
        if current and current_len + add_len > max_len:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        elif not current and len(line) > max_len:
            # Hard-split oversized single line
            for i in range(0, len(line), max_len):
                chunks.append(line[i : i + max_len])
            current = []
            current_len = 0
        else:
            current.append(line)
            current_len += add_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_telegram_message(text: str) -> bool:
    """POST a message to Telegram. Soft-fails; never raises into callers."""
    if not telegram_configured():
        return False
    token = settings.telegram_bot_token.strip()
    chat_id = str(settings.telegram_chat_id).strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": TELEGRAM_PARSE_MODE,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            if resp.status >= 400:
                logger.warning("Telegram send failed status=%s body=%s", resp.status, body[:200])
                return False
            return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def _send_chunks(text: str) -> None:
    for part in chunk_message(text):
        send_telegram_message(part)


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _bold(value: Any) -> str:
    return f"<b>{_esc(value)}</b>"


def _code(value: Any) -> str:
    return f"<code>{_esc(value)}</code>"


def _city_from_slug(event_slug: Optional[str]) -> str:
    if not event_slug:
        return "?"
    # highest-temperature-in-london-on-september-27-2026
    slug = str(event_slug)
    marker = "highest-temperature-in-"
    if marker in slug:
        rest = slug.split(marker, 1)[1]
        city = rest.split("-on-", 1)[0].replace("-", " ")
        return city.title() if city else slug
    return slug


def _fmt_price(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value) if value is not None else "?"


def _fmt_order_id(order_id: Any) -> str:
    if not order_id:
        return "—"
    s = str(order_id)
    if len(s) > 12:
        return s[:10] + "…"
    return s


@lru_cache(maxsize=1)
def _city_timezones() -> dict[str, str]:
    path = DATA_DIR / "city_timezones.json"
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _local_hhmm_for_city(city: Optional[str], now_utc: Optional[datetime] = None) -> str:
    if not city:
        return ""
    tz_name = _city_timezones().get(str(city))
    if not tz_name:
        return ""
    now = now_utc or datetime.now(timezone.utc)
    try:
        return now.astimezone(ZoneInfo(str(tz_name))).strftime("%H:%M")
    except (ValueError, KeyError):
        return ""


def _local_hhmm_from_event(event: Optional[dict], now_utc: Optional[datetime] = None) -> str:
    if not event:
        return ""
    tz_name = event.get("timezone")
    if not tz_name:
        return _local_hhmm_for_city(event.get("city"), now_utc=now_utc)
    now = now_utc or datetime.now(timezone.utc)
    try:
        return now.astimezone(ZoneInfo(str(tz_name))).strftime("%H:%M")
    except (ValueError, KeyError):
        return _local_hhmm_for_city(event.get("city"), now_utc=now_utc)


def _yes_price_from_row(row: dict[str, Any]) -> Optional[float]:
    for key in ("selection_price", "yes_price", "gamma_yes_price"):
        val = row.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _top_yes_price_from_event(event: Optional[dict]) -> Optional[float]:
    if not event:
        return None
    markets = event.get("markets") or []
    if not markets:
        return None
    try:
        from src.utils.market_parser import get_selection_price, get_gamma_yes_price
    except ImportError:
        return None
    best: Optional[float] = None
    for market in markets:
        price = get_selection_price(market)
        if price is None:
            price = get_gamma_yes_price(market)
        if price is None:
            continue
        if best is None or float(price) > best:
            best = float(price)
    return best


def _resolve_skip_local_time(
    row: dict[str, Any],
    *,
    events_by_id: dict[str, dict],
    now_utc: Optional[datetime] = None,
) -> str:
    if row.get("local_time"):
        return str(row["local_time"])
    eid = str(row.get("event_id") or "")
    event = events_by_id.get(eid)
    local = _local_hhmm_from_event(event, now_utc=now_utc)
    if local:
        return local
    return _local_hhmm_for_city(row.get("city"), now_utc=now_utc)


def _resolve_skip_yes_price(
    row: dict[str, Any],
    *,
    events_by_id: dict[str, dict],
) -> Optional[float]:
    price = _yes_price_from_row(row)
    if price is not None:
        return price
    eid = str(row.get("event_id") or "")
    return _top_yes_price_from_event(events_by_id.get(eid))


def _format_buy_skip(
    row: dict[str, Any],
    *,
    events_by_id: Optional[dict[str, dict]] = None,
    now_utc: Optional[datetime] = None,
) -> str:
    events_by_id = events_by_id or {}
    city = row.get("city") or _city_from_slug(row.get("event_slug"))
    reason = row.get("reason") or "unknown"
    parts = [f"⏭ {_bold(city)} — {_code(reason)}"]
    if row.get("buy_price") is not None:
        parts.append(f"buy {_code(_fmt_price(row['buy_price']))}")
    yes = _resolve_skip_yes_price(row, events_by_id=events_by_id)
    if yes is not None:
        parts.append(f"@{_code(_fmt_price(yes))}")
    local = _resolve_skip_local_time(row, events_by_id=events_by_id, now_utc=now_utc)
    if local:
        parts.append(f"🕒 {_code(local)}")
    return " — ".join(parts)


def _selection_label(sel: Any, event_id: Any) -> str:
    city = getattr(sel, "city", None) or "?"
    title = getattr(sel, "group_item_title", None) or ""
    if title:
        return f"{city} {title}"
    return str(city)


def _format_buy_order(
    result: dict[str, Any],
    selections_by_event: dict[str, Any],
    *,
    now_utc: Optional[datetime] = None,
) -> str:
    event_id = str(result.get("event_id") or "")
    sel = selections_by_event.get(event_id)
    label = _selection_label(sel, event_id) if sel is not None else (event_id or "?")
    local = ""
    if sel is not None:
        local = _local_hhmm_from_event(getattr(sel, "event", None), now_utc=now_utc)
        if not local:
            local = _local_hhmm_for_city(getattr(sel, "city", None), now_utc=now_utc)
    if result.get("error"):
        line = f"❌ {_bold(label)} — {_bold('ERROR')}: {_esc(result['error'])}"
        if local:
            line += f" — 🕒 {_code(local)}"
        return line
    status = result.get("status") or ("simulated" if result.get("dry_run") else "?")
    price = result.get("price")
    size = result.get("size")
    if size is None and sel is not None:
        size = getattr(sel, "share_count", None)
    oid = _fmt_order_id(result.get("order_id"))
    size_s = f"{size:g}" if isinstance(size, (int, float)) else (str(size) if size else "?")
    status_l = str(status).lower()
    if status_l in ("live", "matched", "filled") or (
        not result.get("dry_run") and status_l not in ("simulated", "?")
    ):
        icon = "✅"
    elif result.get("dry_run") or status_l == "simulated":
        icon = "🧪"
    else:
        icon = "📤"
    line = (
        f"{icon} {_bold(label)} — {_code(status)} — "
        f"{_code(size_s)} @ {_code(_fmt_price(price))} — id {_code(oid)}"
    )
    if local:
        line += f" — 🕒 {_code(local)}"
    return line


def _events_by_id(
    selections: Optional[Sequence[Any]],
    events: Optional[Sequence[dict]] = None,
) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for event in events or []:
        eid = str(event.get("id") or "")
        if eid:
            by_id[eid] = event
    for sel in selections or []:
        event = getattr(sel, "event", None)
        if isinstance(event, dict):
            eid = str(event.get("id") or getattr(sel, "event_id", "") or "")
            if eid:
                by_id[eid] = event
    return by_id


def format_buy_run_message(
    event_date: date | str,
    skipped_bought: Sequence[dict[str, Any]],
    order_results: Sequence[dict[str, Any]],
    selections: Optional[Sequence[Any]] = None,
    events: Optional[Sequence[dict]] = None,
    now_utc: Optional[datetime] = None,
) -> str:
    date_s = event_date.isoformat() if isinstance(event_date, date) else str(event_date)
    skips = list(skipped_bought or [])
    orders = list(order_results or [])
    by_event: dict[str, Any] = {}
    for sel in selections or []:
        eid = str(getattr(sel, "event_id", "") or "")
        if eid:
            by_event[eid] = sel
    events_map = _events_by_id(selections, events)
    err_n = sum(1 for r in orders if r.get("error"))
    ok_n = len(orders) - err_n

    lines = [
        f"🛒 {_bold(f'BUY {date_s}')}",
        f"⏭ Skipped {_bold(len(skips))} | ✅ Orders {_bold(ok_n)}"
        + (f" | ❌ Errors {_bold(err_n)}" if err_n else ""),
        "",
    ]
    if skips:
        lines.append(f"⏭ {_bold('SKIP')}")
        for row in skips:
            lines.append(
                _format_buy_skip(row, events_by_id=events_map, now_utc=now_utc)
            )
        lines.append("")
    if orders:
        lines.append(f"📤 {_bold('ORDER')}")
        for row in orders:
            lines.append(_format_buy_order(row, by_event, now_utc=now_utc))
    return "\n".join(lines).rstrip() + "\n"


def notify_buy_run(
    event_date: date | str,
    skipped_bought: Sequence[dict[str, Any]],
    order_results: Sequence[dict[str, Any]],
    selections: Optional[Sequence[Any]] = None,
    events: Optional[Sequence[dict]] = None,
) -> None:
    if not telegram_configured():
        return
    skips = list(skipped_bought or [])
    orders = list(order_results or [])
    if not skips and not orders:
        return
    try:
        _send_chunks(
            format_buy_run_message(
                event_date,
                skips,
                orders,
                selections=selections,
                events=events,
            )
        )
    except Exception:
        logger.exception("Telegram buy notify failed")


def _interesting_skips(
    skipped: Sequence[dict[str, Any]],
    idle_reasons: frozenset[str],
) -> list[dict[str, Any]]:
    return [s for s in skipped if str(s.get("reason") or "") not in idle_reasons]


def _format_sell_skip(row: dict[str, Any]) -> str:
    city = _city_from_slug(row.get("event_slug"))
    reason = row.get("reason") or "unknown"
    parts = [f"⏭ {_bold(city)} — {_code(reason)}"]
    if row.get("tier"):
        parts.append(f"tier {_code(row['tier'])}")
    if row.get("value_pct") is not None:
        try:
            pct = f"{float(row['value_pct']):.1f}%"
            parts.append(f"value_pct={_code(pct)}")
        except (TypeError, ValueError):
            parts.append(f"value_pct={_code(row['value_pct'])}")
    if row.get("current_mid") is not None:
        parts.append(f"mid {_code(_fmt_price(row['current_mid']))}")
    if row.get("open_order_count") is not None:
        parts.append(f"open={_code(row['open_order_count'])}")
    return " — ".join(parts)


def _format_sell_order_row(row: dict[str, Any], *, kind: str) -> str:
    city = _city_from_slug(row.get("event_slug"))
    order = row.get("order") or {}
    if isinstance(order, dict) and order.get("error"):
        return f"❌ {_bold(city)} — {_bold('ERROR')}: {_esc(order['error'])}"
    status = "?"
    price = None
    size = None
    oid = None
    dry_run = False
    if isinstance(order, dict):
        dry_run = bool(order.get("dry_run"))
        status = order.get("status") or ("simulated" if dry_run else "?")
        price = order.get("price")
        size = order.get("size")
        oid = order.get("order_id")
    extras: list[str] = []
    if row.get("tier"):
        extras.append(_code(row["tier"]))
    if row.get("value_pct") is not None:
        try:
            pct_label = f"value_pct={float(row['value_pct']):.1f}%"
            extras.append(_code(pct_label))
        except (TypeError, ValueError):
            extras.append(_code(f"value_pct={row['value_pct']}"))
    size_s = f"{size:g}" if isinstance(size, (int, float)) else (str(size) if size else "?")
    status_l = str(status).lower()
    if status_l in ("live", "matched", "filled") or (not dry_run and status_l not in ("simulated", "?")):
        icon = "✅"
    elif dry_run or status_l == "simulated":
        icon = "🧪"
    else:
        icon = "📤"
    head = f"{icon} {_bold(city)}"
    if extras:
        head += " — " + " — ".join(extras)
    return (
        f"{head} — {_code(status)} — {_code(size_s)} @ {_code(_fmt_price(price))} "
        f"— id {_code(_fmt_order_id(oid))}"
    )


def _format_error_row(row: dict[str, Any]) -> str:
    city = _city_from_slug(row.get("event_slug"))
    mid = row.get("market_id") or row.get("token_id") or "?"
    label = city if city != "?" else str(mid)[:16]
    return f"❌ {_bold(label)} — {_esc(row.get('error') or 'unknown')}"


def format_sell_win_message(result: dict[str, Any]) -> str:
    placed = list(result.get("placed") or [])
    errors = list(result.get("errors") or [])
    interesting = _interesting_skips(result.get("skipped") or [], SELL_WIN_IDLE_SKIP_REASONS)
    lines = [
        f"🏆 {_bold('SELL-WIN')}",
        (
            f"✅ Placed {_bold(len(placed))} | ⏭ Skip {_bold(len(interesting))} | "
            f"❌ Errors {_bold(len(errors))}"
        ),
        "",
    ]
    for row in placed:
        lines.append(_format_sell_order_row(row, kind="sell_win"))
    for row in interesting:
        lines.append(_format_sell_skip(row))
    for row in errors:
        lines.append(_format_error_row(row))
    return "\n".join(lines).rstrip() + "\n"


def format_stop_loss_message(result: dict[str, Any]) -> str:
    sold = list(result.get("sold") or [])
    errors = list(result.get("errors") or [])
    interesting = _interesting_skips(result.get("skipped") or [], STOP_LOSS_IDLE_SKIP_REASONS)
    lines = [
        f"🛑 {_bold('STOP-LOSS')}",
        (
            f"✅ Sold {_bold(len(sold))} | ⏭ Skip {_bold(len(interesting))} | "
            f"❌ Errors {_bold(len(errors))}"
        ),
        "",
    ]
    for row in sold:
        lines.append(_format_sell_order_row(row, kind="stop_loss"))
    for row in interesting:
        lines.append(_format_sell_skip(row))
    for row in errors:
        lines.append(_format_error_row(row))
    return "\n".join(lines).rstrip() + "\n"


def notify_sell_win_run(result: dict[str, Any]) -> None:
    if not telegram_configured():
        return
    if not isinstance(result, dict):
        return
    if result.get("status") == "error":
        try:
            reason = result.get("reason") or "error"
            _send_chunks(f"🏆 {_bold('SELL-WIN')}\n❌ {_bold('ERROR')} — {_esc(reason)}\n")
        except Exception:
            logger.exception("Telegram sell-win error notify failed")
        return
    placed = list(result.get("placed") or [])
    errors = list(result.get("errors") or [])
    interesting = _interesting_skips(result.get("skipped") or [], SELL_WIN_IDLE_SKIP_REASONS)
    if not placed and not errors and not interesting:
        return
    try:
        _send_chunks(format_sell_win_message(result))
    except Exception:
        logger.exception("Telegram sell-win notify failed")


def notify_stop_loss_run(result: dict[str, Any]) -> None:
    if not telegram_configured():
        return
    if not isinstance(result, dict):
        return
    if result.get("status") == "error":
        try:
            reason = result.get("reason") or "error"
            _send_chunks(f"🛑 {_bold('STOP-LOSS')}\n❌ {_bold('ERROR')} — {_esc(reason)}\n")
        except Exception:
            logger.exception("Telegram stop-loss error notify failed")
        return
    sold = list(result.get("sold") or [])
    errors = list(result.get("errors") or [])
    interesting = _interesting_skips(result.get("skipped") or [], STOP_LOSS_IDLE_SKIP_REASONS)
    if not sold and not errors and not interesting:
        return
    try:
        _send_chunks(format_stop_loss_message(result))
    except Exception:
        logger.exception("Telegram stop-loss notify failed")
