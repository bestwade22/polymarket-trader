"""Lambda handler: hourly trade with global window gate, commit selections to git."""

import glob
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from lambda_handlers.git_sync import WORKSPACE, clone_or_update, commit_and_push, git_settings_from_env
from lambda_handlers.secrets import apply_secrets

logger = logging.getLogger(__name__)


def resolve_trade_date(event: Dict[str, Any]) -> str:
    raw = event.get("date")
    if raw:
        return str(raw).strip()
    return datetime.now(timezone.utc).date().isoformat()


def should_run(force: bool, data_dir: Optional[Path] = None) -> bool:
    if force:
        return True
    from scripts.should_run_trade import should_run_trade

    return should_run_trade(data_dir=data_dir)


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
    logging.basicConfig(level=logging.INFO)
    apply_secrets()

    payload = event or {}
    force = bool(payload.get("force", False))
    gate_dir = gate_data_dir(force)
    if not should_run(force, data_dir=gate_dir):
        logger.info("No tradable events in gate check; skipping trade-hourly")
        return {
            "status": "skipped",
            "job": "trade-hourly",
            "reason": "outside_trading_window",
        }

    git_repo, branch, github_pat = git_settings_from_env()
    workspace = clone_or_update(github_pat, git_repo, branch)

    now_utc = datetime.now(timezone.utc)
    explicit_date = payload.get("date")
    if explicit_date:
        dates_to_trade = [str(explicit_date).strip()]
    else:
        dates_to_trade = tradable_dates_for_run(workspace, now_utc)
        if not dates_to_trade:
            logger.info("No tradable events after clone; skipping trade-hourly")
            return {
                "status": "skipped",
                "job": "trade-hourly",
                "reason": "no_tradable_events",
            }

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

    return {
        "status": "ok",
        "job": "trade-hourly",
        "dates": dates_to_trade,
        "committed": committed,
        "selection_files": paths,
        "force": force,
    }
