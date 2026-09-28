"""Tests for Telegram skip/order digests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.notify import telegram as tg


@pytest.fixture
def tg_creds(monkeypatch):
    monkeypatch.setattr(tg.settings, "telegram_bot_token", "123:ABC")
    monkeypatch.setattr(tg.settings, "telegram_chat_id", "999")
    monkeypatch.setattr(tg.settings, "telegram_notify_enabled", True)


def test_telegram_configured_requires_creds(monkeypatch):
    monkeypatch.setattr(tg.settings, "telegram_bot_token", "")
    monkeypatch.setattr(tg.settings, "telegram_chat_id", "1")
    monkeypatch.setattr(tg.settings, "telegram_notify_enabled", True)
    assert tg.telegram_configured() is False

    monkeypatch.setattr(tg.settings, "telegram_bot_token", "tok")
    monkeypatch.setattr(tg.settings, "telegram_chat_id", "")
    assert tg.telegram_configured() is False

    monkeypatch.setattr(tg.settings, "telegram_chat_id", "42")
    monkeypatch.setattr(tg.settings, "telegram_notify_enabled", False)
    assert tg.telegram_configured() is False

    monkeypatch.setattr(tg.settings, "telegram_notify_enabled", True)
    assert tg.telegram_configured() is True


def test_format_buy_run_message_includes_skips_and_orders():
    sel = SimpleNamespace(
        event_id="e1",
        city="Berlin",
        group_item_title="24°C",
        share_count=15,
    )
    text = tg.format_buy_run_message(
        "2026-09-27",
        [
            {
                "city": "London",
                "reason": "buy_band_low_local_time",
                "buy_price": 0.47,
                "local_time": "14:15",
            },
            {
                "city": "Paris",
                "reason": "low_win_summary_timezone",
                "timezone": "Central EU",
            },
        ],
        [
            {
                "event_id": "e1",
                "status": "live",
                "price": 0.55,
                "size": 15,
                "order_id": "0xd2cb915fabcdef",
            },
            {"event_id": "e2", "error": "boom"},
        ],
        selections=[sel],
    )
    assert text.startswith("BUY 2026-09-27")
    assert "Skipped 2 | Orders 2" in text
    assert "SKIP London — buy_band_low_local_time — buy 0.47 local 14:15" in text
    assert "SKIP Paris — low_win_summary_timezone" in text
    assert "ORDER Berlin 24°C — live — 15 @ 0.55 — id 0xd2cb915f…" in text
    assert "ORDER e2 — ERROR: boom" in text


def test_chunk_message_splits_long_text():
    lines = [f"line-{i}-{'x' * 50}" for i in range(120)]
    text = "\n".join(lines)
    chunks = tg.chunk_message(text, max_len=400)
    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)
    assert "\n".join(chunks) == text


def test_notify_buy_run_noop_without_creds(monkeypatch):
    monkeypatch.setattr(tg.settings, "telegram_bot_token", "")
    monkeypatch.setattr(tg.settings, "telegram_chat_id", "")
    monkeypatch.setattr(tg.settings, "telegram_notify_enabled", True)
    with patch.object(tg, "send_telegram_message") as send:
        tg.notify_buy_run("2026-09-27", [{"city": "X", "reason": "y"}], [])
        send.assert_not_called()


def test_notify_buy_run_sends(tg_creds):
    with patch.object(tg, "send_telegram_message", return_value=True) as send:
        tg.notify_buy_run(
            "2026-09-27",
            [{"city": "London", "reason": "spread_max"}],
            [{"event_id": "e1", "status": "simulated", "price": 0.5, "dry_run": True}],
        )
        assert send.call_count == 1
        body = send.call_args[0][0]
        assert "BUY 2026-09-27" in body
        assert "SKIP London" in body


def test_notify_buy_run_idle_noop(tg_creds):
    with patch.object(tg, "send_telegram_message") as send:
        tg.notify_buy_run("2026-09-27", [], [])
        send.assert_not_called()


def test_sell_win_idle_skips_do_not_notify(tg_creds):
    result = {
        "status": "ok",
        "skipped": [
            {"event_slug": "highest-temperature-in-paris-on-july-4-2026", "reason": "price_too_low"},
            {
                "event_slug": "highest-temperature-in-london-on-july-4-2026",
                "reason": "before_sell_win_window",
            },
            {"event_slug": "some-other", "reason": "not_temp_market"},
        ],
        "placed": [],
        "errors": [],
    }
    with patch.object(tg, "send_telegram_message") as send:
        tg.notify_sell_win_run(result)
        send.assert_not_called()


def test_sell_win_notifies_interesting_skip_and_placed(tg_creds):
    result = {
        "status": "ok",
        "skipped": [
            {
                "event_slug": "highest-temperature-in-paris-on-july-4-2026",
                "reason": "open_sell_order",
                "open_order_count": 1,
            },
            {
                "event_slug": "highest-temperature-in-london-on-july-4-2026",
                "reason": "price_too_low",
            },
        ],
        "placed": [
            {
                "event_slug": "highest-temperature-in-ankara-on-july-4-2026",
                "tier": "tier2",
                "order": {
                    "status": "live",
                    "price": 0.96,
                    "size": 15,
                    "order_id": "0xabc",
                },
            }
        ],
        "errors": [],
    }
    text = tg.format_sell_win_message(result)
    assert "SELL-WIN" in text
    assert "Placed 1 | Skip 1 | Errors 0" in text
    assert "ORDER Ankara — tier2 — live — 15 @ 0.96" in text
    assert "SKIP Paris — open_sell_order" in text
    assert "price_too_low" not in text

    with patch.object(tg, "send_telegram_message", return_value=True) as send:
        tg.notify_sell_win_run(result)
        assert send.call_count == 1


def test_stop_loss_idle_skips_do_not_notify(tg_creds):
    result = {
        "status": "ok",
        "skipped": [
            {"event_slug": "highest-temperature-in-madrid-on-july-4-2026", "reason": "above_threshold"},
            {"event_slug": "highest-temperature-in-nyc-on-july-4-2026", "reason": "before_min_local_time"},
            {"event_slug": "highest-temperature-in-helsinki-on-july-4-2026", "reason": "below_floor"},
        ],
        "sold": [],
        "errors": [],
    }
    with patch.object(tg, "send_telegram_message") as send:
        tg.notify_stop_loss_run(result)
        send.assert_not_called()


def test_stop_loss_notifies_sold_and_interesting(tg_creds):
    result = {
        "status": "ok",
        "skipped": [
            {
                "event_slug": "highest-temperature-in-paris-on-july-4-2026",
                "reason": "open_sell_order",
            },
            {
                "event_slug": "highest-temperature-in-madrid-on-july-4-2026",
                "reason": "above_threshold",
            },
        ],
        "sold": [
            {
                "event_slug": "highest-temperature-in-madrid-on-july-4-2026",
                "value_pct": 42.1,
                "order": {
                    "status": "live",
                    "price": 0.22,
                    "size": 15,
                    "order_id": "0xdeadbeef",
                },
            }
        ],
        "errors": [{"market_id": "m1", "error": "timeout"}],
    }
    text = tg.format_stop_loss_message(result)
    assert "STOP-LOSS" in text
    assert "Sold 1 | Skip 1 | Errors 1" in text
    assert "value_pct=42.1%" in text
    assert "SKIP Paris — open_sell_order" in text
    assert "above_threshold" not in text
    assert "ERROR" in text

    with patch.object(tg, "send_telegram_message", return_value=True) as send:
        tg.notify_stop_loss_run(result)
        assert send.call_count == 1


def test_notify_sell_win_error_status(tg_creds):
    with patch.object(tg, "send_telegram_message", return_value=True) as send:
        tg.notify_sell_win_run({"status": "error", "reason": "missing_wallet"})
        assert send.call_count == 1
        assert "missing_wallet" in send.call_args[0][0]


def test_send_telegram_message_posts_json(tg_creds):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok":true}'
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch.object(tg.urllib.request, "urlopen", return_value=mock_resp) as urlopen:
        ok = tg.send_telegram_message("hello")
        assert ok is True
        req = urlopen.call_args[0][0]
        assert req.full_url.endswith("/bot123:ABC/sendMessage")
        body = json_loads_request(req)
        assert body["chat_id"] == "999"
        assert body["text"] == "hello"


def json_loads_request(req):
    import json

    return json.loads(req.data.decode("utf-8"))
