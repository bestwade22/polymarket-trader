import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SELECTIONS_DIR = DATA_DIR / "selections"
POSITIONS_DIR = DATA_DIR / "positions"
LOGS_DIR = PROJECT_ROOT / "logs"
TRADES_LOG_DIR = LOGS_DIR / "trades"
BOUGHT_EVENTS_FILE = POSITIONS_DIR / "bought_events.json"
SOLD_EVENTS_FILE = POSITIONS_DIR / "sold_events.json"
CITY_COORDS_FILE = DATA_DIR / "city_coords.json"

load_dotenv(PROJECT_ROOT / ".env")

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
API_NINJAS_BASE = "https://api.api-ninjas.com/v1/timezone"

ORDER_PRICE_SOURCES = ("yes_price", "buy_price", "best_bid", "best_ask", "midpoint")
SELECTION_PRICE_SOURCES = ORDER_PRICE_SOURCES


def _normalize_price_source(raw: str, *, allowed: tuple[str, ...], env_name: str) -> str:
    key = raw.strip().lower()
    if key not in allowed:
        raise ValueError(f"Invalid {env_name}={raw!r}. Choose from {list(allowed)}")
    return key


def _normalize_order_price_source(raw: str) -> str:
    return _normalize_price_source(raw, allowed=ORDER_PRICE_SOURCES, env_name="ORDER_PRICE_SOURCE")


def _normalize_selection_price_source(raw: str) -> str:
    return _normalize_price_source(
        raw, allowed=SELECTION_PRICE_SOURCES, env_name="SELECTION_PRICE_SOURCE"
    )


def events_file_for_date(target: Optional[date] = None) -> Path:
    """Path to dated events cache, e.g. data/events_2026-06-14.json."""
    d = target or date.today()
    return DATA_DIR / f"events_{d.isoformat()}.json"


def parse_event_date(value: Optional[str] = None) -> date:
    """Parse YYYY-MM-DD from CLI/env, defaulting to today."""
    raw = value or os.getenv("EVENT_DATE") or os.getenv("FETCH_DATE")
    if raw:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    return date.today()


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _parse_trading_window_time(raw: str, env_name: str, *, allow_hour_24: bool) -> tuple[int, int]:
    """Parse local time as (hour, minute). Accepts 12, 12:30, or 1230."""
    text = raw.strip()
    if ":" in text:
        parts = text.split(":", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(
                f"Invalid {env_name}={raw!r}. Use hour (12), HH:MM (12:30), or HHMM (1230)"
            )
        hour, minute = int(parts[0]), int(parts[1])
    elif text.isdigit() and len(text) == 4:
        hour, minute = int(text[:2]), int(text[2:])
    elif text.isdigit() and len(text) <= 2:
        hour, minute = int(text), 0
    else:
        raise ValueError(
            f"Invalid {env_name}={raw!r}. Use hour (12), HH:MM (12:30), or HHMM (1230)"
        )

    max_hour = 24 if allow_hour_24 else 23
    if not (0 <= hour <= max_hour):
        raise ValueError(f"{env_name} hour must be 0–{max_hour}, got {hour}")
    if not (0 <= minute <= 59):
        raise ValueError(f"{env_name} minute must be 0–59, got {minute}")
    if hour == 24 and minute != 0:
        raise ValueError(f"{env_name}=24:00 is the latest valid end time")
    return hour, minute


def _minutes_since_midnight(hour: int, minute: int) -> int:
    return hour * 60 + minute


def _parse_trading_window_bounds() -> tuple[int, int, int, int]:
    start_h, start_m = _parse_trading_window_time(
        os.getenv("TRADING_WINDOW_START_HOUR", "13:30"),
        "TRADING_WINDOW_START_HOUR",
        allow_hour_24=False,
    )
    end_h, end_m = _parse_trading_window_time(
        os.getenv("TRADING_WINDOW_END_HOUR", "15:30"),
        "TRADING_WINDOW_END_HOUR",
        allow_hour_24=True,
    )
    if _minutes_since_midnight(end_h, end_m) <= _minutes_since_midnight(start_h, start_m):
        raise ValueError(
            f"TRADING_WINDOW_END ({end_h:02d}:{end_m:02d}) must be after "
            f"TRADING_WINDOW_START ({start_h:02d}:{start_m:02d})"
        )
    return start_h, start_m, end_h, end_m


_trading_window_start_h, _trading_window_start_m, _trading_window_end_h, _trading_window_end_m = (
    _parse_trading_window_bounds()
)


def _parse_stop_loss_min_local_time() -> tuple[int, int]:
    hour, minute = _parse_trading_window_time(
        os.getenv("STOP_LOSS_MIN_LOCAL_TIME", "15:30"),
        "STOP_LOSS_MIN_LOCAL_TIME",
        allow_hour_24=False,
    )
    return hour, minute


_stop_loss_min_local_h, _stop_loss_min_local_m = _parse_stop_loss_min_local_time()


def _parse_order_expiry_hours() -> float:
    mins = os.getenv("ORDER_EXPIRY_MINUTES")
    if mins is not None and mins.strip():
        return float(mins.strip()) / 60.0
    hours = os.getenv("ORDER_EXPIRY_HOURS")
    if hours is not None and hours.strip():
        return float(hours.strip())
    return 55.0 / 60.0


def _parse_stop_loss_order_expiry_hours() -> float:
    mins = os.getenv("STOP_LOSS_ORDER_EXPIRY_MINUTES")
    if mins is not None and mins.strip():
        return float(mins.strip()) / 60.0
    return 13.0 / 60.0


def _parse_optional_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw.strip())


class Settings:
    api_ninjas_key: str = os.getenv("API_NINJAS_KEY", "")
    private_key: str = os.getenv("PRIVATE_KEY", "")
    deposit_wallet_address: str = os.getenv("DEPOSIT_WALLET_ADDRESS", "")
    signature_type: int = int(os.getenv("SIGNATURE_TYPE", "1"))
    strategy: str = os.getenv("STRATEGY", "highest_yes")
    share_count: int = int(os.getenv("SHARE_COUNT", "15"))
    yes_price_max: float = float(os.getenv("YES_PRICE_MAX", "0.60"))
    selection_price_source: str = _normalize_selection_price_source(
        os.getenv("SELECTION_PRICE_SOURCE", "midpoint")
    )
    order_price_source: str = _normalize_order_price_source(
        os.getenv("ORDER_PRICE_SOURCE", "midpoint")
    )
    order_expiry_hours: float = _parse_order_expiry_hours()
    stop_loss_order_expiry_hours: float = _parse_stop_loss_order_expiry_hours()
    trading_window_start_hour: int = _trading_window_start_h
    trading_window_start_minute: int = _trading_window_start_m
    trading_window_end_hour: int = _trading_window_end_h
    trading_window_end_minute: int = _trading_window_end_m
    dry_run: bool = _env_bool("DRY_RUN", True)
    stop_loss_dry_run: bool = _env_bool("STOP_LOSS_DRY_RUN", False)
    daily_fetch_hour_utc: int = int(os.getenv("DAILY_FETCH_HOUR_UTC", "6"))
    event_date: str = os.getenv("EVENT_DATE", "")  # YYYY-MM-DD override; empty = today
    stop_loss_pct: float = float(os.getenv("STOP_LOSS_PCT", "50"))
    stop_loss_pct_floor: float = float(os.getenv("STOP_LOSS_PCT_FLOOR", "10"))
    stop_loss_sell_shares: Optional[int] = _parse_optional_int("STOP_LOSS_SELL_SHARES")
    stop_loss_event_slug_marker: str = os.getenv(
        "STOP_LOSS_EVENT_SLUG_MARKER", "highest-temperature-in-"
    )
    stop_loss_min_local_hour: int = _stop_loss_min_local_h
    stop_loss_min_local_minute: int = _stop_loss_min_local_m
    data_api_base: str = os.getenv("DATA_API_BASE", "https://data-api.polymarket.com")


settings = Settings()


def ensure_dirs() -> None:
    for path in (DATA_DIR, SELECTIONS_DIR, POSITIONS_DIR, LOGS_DIR, TRADES_LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
