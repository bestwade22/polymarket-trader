"""Lambda handler: hourly trade with global window gate, commit selections to git."""

import glob
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from lambda_handlers.git_sync import WORKSPACE, clone_or_update, commit_and_push, git_settings_from_env
from lambda_handlers.logging_util import configure_logging
from lambda_handlers.secrets import apply_secrets

logger = logging.getLogger(__name__)


def resolve_trade_date(event: Dict[str, Any]) -> str:
    raw = event.get("date")
    if raw:
        return str(raw).strip()
    return datetime.now(timezone.utc).date().isoformat()


def gate_data_dir(force: bool) -> Optional[Path]:
    """Fetch today/yesterday events from GitHub for an accurate pre-clone gate."""
    if force:
        return None
    from lambda_handlers.gate_data import fetch_gate_data_from_env

    return fetch_gate_data_from_env()


def tradable_dates_for_run(workspace: Path, now_utc: datetime) -> list[str]:
    from scripts.should_run_trade import tradable_event_file_dates

    return tradable_event_file_dates(now_utc=now_utc, data_dir=workspace / "data")


def run_trade_hourly(workspace: Path, event_date: str) -> None:
    cmd = [sys.executable, "-m", "src.main", "trade-hourly", "--date", event_date]
    dry_run = os.environ.get("DRY_RUN", "true").strip().lower()
    if dry_run in ("0", "false", "no", "off"):
        cmd.append("--live")

    result = subprocess.run(cmd, cwd=workspace, text=True, capture_output=True)
    if result.stdout:
        logger.info(result.stdout)
    if result.stderr:
        logger.warning(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"trade-hourly failed (exit {result.returncode}): {result.stderr or result.stdout}"
        )


def selection_paths(event_date: str) -> List[str]:
    pattern = str(WORKSPACE / f"data/selections/markets_yes_{event_date}_*.json")
    return [os.path.relpath(path, WORKSPACE) for path in sorted(glob.glob(pattern))]


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    configure_logging()
    apply_secrets()

    from config.settings import settings
    from scripts.should_run_trade import evaluate_trade_gate

    payload = event or {}
    force = bool(payload.get("force", False))
    now_utc = datetime.now(timezone.utc)

    logger.info(
        "trade-hourly start force=%s now_utc=%s window=%s ticks=%s",
        force,
        now_utc.isoformat(),
        f"{settings.trading_window_start_hour:02d}:{settings.trading_window_start_minute:02d}"
        f"-{settings.trading_window_end_hour:02d}:{settings.trading_window_end_minute:02d}",
        os.environ.get("TRADING_WINDOW_START_HOUR", ""),
    )

    gate_dir = gate_data_dir(force)
    gate = evaluate_trade_gate(now_utc=now_utc, data_dir=gate_dir)

    if not force and gate["status"] == "skip":
        message = (
            f"Gate skip: {gate['reason']}; window={gate['window']}; ticks={gate['ticks']}; "
            f"events_loaded={gate['events_loaded']}; now_utc={gate['now_utc']}"
        )
        logger.info(message)
        print(message)
        return {
            "status": "skipped",
            "job": "trade-hourly",
            "reason": gate["reason"],
            "gate": gate,
        }

    if gate["status"] == "no_data":
        logger.info(
            "Gate has no events files (%s); continuing to clone repo",
            gate["reason"],
        )
    elif gate["status"] == "go":
        logger.info(
            "Gate passed for cities: %s",
            ", ".join(gate["tradable_cities"]),
        )

    git_repo, branch, github_pat = git_settings_from_env()
    workspace = clone_or_update(github_pat, git_repo, branch)

    explicit_date = payload.get("date")
    if explicit_date:
        dates_to_trade = [str(explicit_date).strip()]
    else:
        dates_to_trade = tradable_dates_for_run(workspace, now_utc)
        if not dates_to_trade:
            message = (
                f"No tradable events after clone; window={gate['window']}; "
                f"ticks={gate['ticks']}; now_utc={now_utc.isoformat()}"
            )
            logger.info(message)
            print(message)
            return {
                "status": "skipped",
                "job": "trade-hourly",
                "reason": "no_tradable_events",
                "gate": gate,
            }

    logger.info("Running trade-hourly for dates: %s", dates_to_trade)

    all_paths: List[str] = []
    for event_date in dates_to_trade:
        run_trade_hourly(workspace, event_date)
        all_paths.extend(selection_paths(event_date))

    bought = WORKSPACE / "data/positions/bought_events.json"
    if bought.exists():
        all_paths.append("data/positions/bought_events.json")

    paths = sorted(set(all_paths))
    committed = False
    if paths:
        label = dates_to_trade[0] if len(dates_to_trade) == 1 else "multi"
        committed = commit_and_push(
            paths,
            f"chore(data): trade selections {label}",
            github_pat=github_pat,
            git_repo=git_repo,
            branch=branch,
        )

    result = {
        "status": "ok",
        "job": "trade-hourly",
        "dates": dates_to_trade,
        "committed": committed,
        "selection_files": paths,
        "force": force,
        "gate": gate,
    }
    logger.info("trade-hourly complete: %s", json.dumps(result))
    return result
