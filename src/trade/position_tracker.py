import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import BOUGHT_EVENTS_FILE, SOLD_EVENTS_FILE, ensure_dirs

logger = logging.getLogger(__name__)


class PositionTracker:
    def __init__(self, path: Path = BOUGHT_EVENTS_FILE):
        self.path = path
        ensure_dirs()
        if not self.path.exists():
            self.path.write_text("[]")

    def load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save(self, records: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(records, indent=2))

    def is_event_bought(self, event_id: str) -> bool:
        return any(str(r.get("event_id")) == str(event_id) for r in self.load())

    def record_buy(
        self,
        event_id: str,
        market_id: str,
        strategy: str,
        order_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        records = self.load()
        records.append(
            {
                "event_id": str(event_id),
                "market_id": str(market_id),
                "strategy": strategy,
                "order_id": order_id,
                "bought_at": datetime.now(timezone.utc).isoformat(),
                **(extra or {}),
            }
        )
        self._save(records)
        logger.info("Recorded buy event=%s market=%s", event_id, market_id)


class SoldPositionTracker:
    def __init__(self, path: Path = SOLD_EVENTS_FILE):
        self.path = path
        ensure_dirs()
        if not self.path.exists():
            self.path.write_text("[]")

    def load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save(self, records: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(records, indent=2))

    def is_market_sold(self, market_id: str) -> bool:
        return any(str(r.get("market_id")) == str(market_id) for r in self.load())

    def record_sell(
        self,
        market_id: str,
        token_id: str,
        order_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        records = self.load()
        records.append(
            {
                "market_id": str(market_id),
                "token_id": str(token_id),
                "order_id": order_id,
                "sold_at": datetime.now(timezone.utc).isoformat(),
                **(extra or {}),
            }
        )
        self._save(records)
        logger.info("Recorded sell market=%s token=%s", market_id, token_id)
