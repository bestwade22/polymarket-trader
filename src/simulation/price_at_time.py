"""CLOB Yes-% history lookup with in-memory session cache; disk only for bought tokens."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from config.settings import SIM_PRICE_CACHE_DIR, ensure_dirs
from src.api.clob_client import ClobPriceClient
from src.utils.market_parser import parse_float

logger = logging.getLogger(__name__)


class PriceHistoryStore:
    """Fetch /prices-history; keep session cache; persist only when mark_bought()."""

    def __init__(
        self,
        clob: Optional[ClobPriceClient] = None,
        *,
        cache_dir: Optional[Path] = None,
        fidelity: int = 1,
    ):
        self.clob = clob or ClobPriceClient()
        self.cache_dir = cache_dir or SIM_PRICE_CACHE_DIR
        self.fidelity = fidelity
        self._session: dict[str, list[dict[str, Any]]] = {}
        self._bought_tokens: set[str] = set()

    def get_history(
        self,
        token_id: str,
        *,
        start_ts: int,
        end_ts: int,
    ) -> list[dict[str, Any]]:
        token_id = str(token_id)
        if token_id in self._session:
            return self._session[token_id]

        disk = self._load_disk(token_id)
        if disk is not None:
            self._session[token_id] = disk
            return disk

        history = self.clob.get_prices_history(
            token_id,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=self.fidelity,
        )
        self._session[token_id] = history
        return history

    def price_near(
        self,
        token_id: str,
        target_ts: int,
        *,
        start_ts: int,
        end_ts: int,
        max_delta_seconds: int = 3600,
    ) -> Optional[float]:
        history = self.get_history(token_id, start_ts=start_ts, end_ts=end_ts)
        best: Optional[tuple[float, float]] = None
        for point in history:
            t = point.get("t")
            p = parse_float(point.get("p"))
            if t is None or p is None:
                continue
            try:
                ts = int(t)
            except (TypeError, ValueError):
                continue
            delta = abs(ts - target_ts)
            if delta > max_delta_seconds:
                continue
            if best is None or delta < best[0]:
                best = (float(delta), p)
        return best[1] if best else None

    def mark_bought(self, token_id: str) -> None:
        """Persist this token's session history to disk (bought markets only)."""
        token_id = str(token_id)
        self._bought_tokens.add(token_id)
        history = self._session.get(token_id)
        if history is None:
            return
        ensure_dirs()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{token_id}.json"
        try:
            path.write_text(json.dumps({"token_id": token_id, "history": history}, indent=2))
            logger.debug("Saved price cache for bought token %s (%d points)", token_id, len(history))
        except OSError as exc:
            logger.warning("Failed to write price cache for %s: %s", token_id, exc)

    def _load_disk(self, token_id: str) -> Optional[list[dict[str, Any]]]:
        path = self.cache_dir / f"{token_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        history = data.get("history") if isinstance(data, dict) else data
        if not isinstance(history, list):
            return None
        return [p for p in history if isinstance(p, dict)]
