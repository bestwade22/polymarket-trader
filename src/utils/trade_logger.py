import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import LOGS_DIR, TRADES_LOG_DIR, ensure_dirs
from src.utils.hk_time import HKTFormatter


def setup_app_logging() -> None:
    ensure_dirs()
    log_file = LOGS_DIR / "app.log"
    formatter = HKTFormatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, stream_handler],
        force=True,
    )


class TradeStepLogger:
    def __init__(self, event_id: str, city: str = ""):
        self.event_id = event_id
        self.city = city or ""
        self.steps: list[dict[str, Any]] = []
        self.logger = logging.getLogger(__name__)

    def log_step(self, step: str, **data: Any) -> None:
        entry = {"step": step, "at": datetime.now(timezone.utc).isoformat(), **data}
        self.steps.append(entry)
        if self.city:
            self.logger.info(
                "event=%s city=%s step=%s %s", self.event_id, self.city, step, data
            )
        else:
            self.logger.info("event=%s step=%s %s", self.event_id, step, data)

    def save(self) -> Path:
        ensure_dirs()
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = TRADES_LOG_DIR / f"{ts}_{self.event_id}.json"
        path.write_text(json.dumps({"event_id": self.event_id, "steps": self.steps}, indent=2))
        return path
