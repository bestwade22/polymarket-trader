"""Telegram digests for buy skips/orders and sell-win / stop-loss runs."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Optional, Sequence

from config.settings import settings

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LEN = 4096

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
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
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


def _format_buy_skip(row: dict[str, Any]) -> str:
    city = row.get("city") or _city_from_slug(row.get("event_slug"))
    reason = row.get("reason") or "unknown"
    parts = [f"SKIP {city} — {reason}"]
    if row.get("buy_price") is not None:
        parts.append(f"buy {_fmt_price(row['buy_price'])}")
    if row.get("local_time"):
        parts.append(f"local {row['local_time']}")
    if row.get("yes_gap") is not None:
        parts.append(f"gap {_fmt_price(row['yes_gap'])}")
    if row.get("timezone"):
        parts.append(str(row["timezone"]))
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " — " + " ".join(parts[1:])


def _selection_label(sel: Any, event_id: Any) -> str:
    city = getattr(sel, "city", None) or "?"
    title = getattr(sel, "group_item_title", None) or ""
    if title:
        return f"{city} {title}"
    return str(city)


def _format_buy_order(
    result: dict[str, Any],
    selections_by_event: dict[str, Any],
) -> str:
    event_id = str(result.get("event_id") or "")
    sel = selections_by_event.get(event_id)
    label = _selection_label(sel, event_id) if sel is not None else (event_id or "?")
    if result.get("error"):
        return f"ORDER {label} — ERROR: {result['error']}"
    status = result.get("status") or ("simulated" if result.get("dry_run") else "?")
    price = result.get("price")
    size = result.get("size")
    if size is None and sel is not None:
        size = getattr(sel, "share_count", None)
    oid = _fmt_order_id(result.get("order_id"))
    size_s = f"{size:g}" if isinstance(size, (int, float)) else (str(size) if size else "?")
    return f"ORDER {label} — {status} — {size_s} @ {_fmt_price(price)} — id {oid}"


def format_buy_run_message(
    event_date: date | str,
    skipped_bought: Sequence[dict[str, Any]],
    order_results: Sequence[dict[str, Any]],
    selections: Optional[Sequence[Any]] = None,
) -> str:
    date_s = event_date.isoformat() if isinstance(event_date, date) else str(event_date)
    skips = list(skipped_bought or [])
    orders = list(order_results or [])
    by_event: dict[str, Any] = {}
    for sel in selections or []:
        eid = str(getattr(sel, "event_id", "") or "")
        if eid:
            by_event[eid] = sel

    lines = [
        f"BUY {date_s}",
        f"Skipped {len(skips)} | Orders {len(orders)}",
        "",
    ]
    for row in skips:
        lines.append(_format_buy_skip(row))
    for row in orders:
        lines.append(_format_buy_order(row, by_event))
    return "\n".join(lines).rstrip() + "\n"


def notify_buy_run(
    event_date: date | str,
    skipped_bought: Sequence[dict[str, Any]],
    order_results: Sequence[dict[str, Any]],
    selections: Optional[Sequence[Any]] = None,
) -> None:
    if not telegram_configured():
        return
    skips = list(skipped_bought or [])
    orders = list(order_results or [])
    if not skips and not orders:
        return
    try:
        _send_chunks(
            format_buy_run_message(event_date, skips, orders, selections=selections)
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
    parts = [f"SKIP {city} — {reason}"]
    if row.get("tier"):
        parts.append(f"tier {row['tier']}")
    if row.get("value_pct") is not None:
        try:
            parts.append(f"value_pct={float(row['value_pct']):.1f}%")
        except (TypeError, ValueError):
            parts.append(f"value_pct={row['value_pct']}")
    if row.get("current_mid") is not None:
        parts.append(f"mid {_fmt_price(row['current_mid'])}")
    if row.get("open_order_count") is not None:
        parts.append(f"open={row['open_order_count']}")
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " — " + " ".join(parts[1:])


def _format_sell_order_row(row: dict[str, Any], *, kind: str) -> str:
    city = _city_from_slug(row.get("event_slug"))
    order = row.get("order") or {}
    if isinstance(order, dict) and order.get("error"):
        return f"ORDER {city} — ERROR: {order['error']}"
    status = "?"
    price = None
    size = None
    oid = None
    if isinstance(order, dict):
        status = order.get("status") or ("simulated" if order.get("dry_run") else "?")
        price = order.get("price")
        size = order.get("size")
        oid = order.get("order_id")
    extras: list[str] = []
    if row.get("tier"):
        extras.append(str(row["tier"]))
    if row.get("value_pct") is not None:
        try:
            extras.append(f"value_pct={float(row['value_pct']):.1f}%")
        except (TypeError, ValueError):
            extras.append(f"value_pct={row['value_pct']}")
    size_s = f"{size:g}" if isinstance(size, (int, float)) else (str(size) if size else "?")
    head = f"ORDER {city}"
    if extras:
        head += " — " + " — ".join(extras)
    return f"{head} — {status} — {size_s} @ {_fmt_price(price)} — id {_fmt_order_id(oid)}"


def _format_error_row(row: dict[str, Any]) -> str:
    city = _city_from_slug(row.get("event_slug"))
    mid = row.get("market_id") or row.get("token_id") or "?"
    label = city if city != "?" else str(mid)[:16]
    return f"ERROR {label} — {row.get('error') or 'unknown'}"


def format_sell_win_message(result: dict[str, Any]) -> str:
    placed = list(result.get("placed") or [])
    errors = list(result.get("errors") or [])
    interesting = _interesting_skips(result.get("skipped") or [], SELL_WIN_IDLE_SKIP_REASONS)
    lines = [
        "SELL-WIN",
        f"Placed {len(placed)} | Skip {len(interesting)} | Errors {len(errors)}",
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
        "STOP-LOSS",
        f"Sold {len(sold)} | Skip {len(interesting)} | Errors {len(errors)}",
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
            _send_chunks(f"SELL-WIN\nERROR — {reason}\n")
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
            _send_chunks(f"STOP-LOSS\nERROR — {reason}\n")
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
