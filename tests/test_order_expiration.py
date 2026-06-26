"""Tests for order expiration helpers."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.trade.executor import compute_order_expiration


def test_compute_order_expiration_gtd_default_two_hours():
    now = 1_700_000_000
    expiration, order_type = compute_order_expiration(2, now_ts=now)
    assert order_type == "GTD"
    assert expiration == now + 7200


def test_compute_order_expiration_gtc_when_zero():
    expiration, order_type = compute_order_expiration(0, now_ts=1_700_000_000)
    assert order_type == "GTC"
    assert expiration == 0


def test_compute_order_expiration_custom_hours():
    now = 1_700_000_000
    expiration, order_type = compute_order_expiration(4.5, now_ts=now)
    assert order_type == "GTD"
    assert expiration == now + int(4.5 * 3600)
