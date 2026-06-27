"""Lambda handler: hourly trade with global window gate, commit selections to git."""

import glob
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from lambda_handlers.git_sync import WORKSPACE, clone_or_update, commit_and_push, git_settings_from_env
from lambda_handlers.secrets import apply_secrets

logger = logging.getLogger(__name__)


def resolve_trade_date(event: Dict[str, Any]) -> str:
    raw = event.get("date")
    if raw:
        return str(raw).strip()
    return datetime.now(timezone.utc).date().isoformat()


def should_run(force: bool) -> bool:
    if force:
        return True
    from scripts.should_run_trade import should_run_trade

    return should_run_trade()


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
    if not should_run(force):
        logger.info("Outside global trading window; skipping trade-hourly")
        return {
            "status": "skipped",
            "job": "trade-hourly",
            "reason": "outside_trading_window",
        }

    event_date = resolve_trade_date(payload)
    git_repo, branch, github_pat = git_settings_from_env()
    workspace = clone_or_update(github_pat, git_repo, branch)

    run_trade_hourly(workspace, event_date)

    paths = selection_paths(event_date)
    bought = WORKSPACE / "data/positions/bought_events.json"
    if bought.exists():
        paths.append("data/positions/bought_events.json")

    committed = False
    if paths:
        committed = commit_and_push(
            paths,
            f"chore(data): trade selections {event_date}",
            github_pat=github_pat,
            git_repo=git_repo,
            branch=branch,
        )

    return {
        "status": "ok",
        "job": "trade-hourly",
        "date": event_date,
        "committed": committed,
        "selection_files": paths,
        "force": force,
    }
