"""Lambda handler: daily event fetch at 00:01 HKT, commit events JSON to git."""

import logging
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict
from zoneinfo import ZoneInfo

from lambda_handlers.git_sync import clone_or_update, commit_and_push, git_settings_from_env
from lambda_handlers.secrets import apply_secrets

logger = logging.getLogger(__name__)


def resolve_fetch_date(event: Dict[str, Any]) -> str:
    raw = event.get("date")
    if raw:
        return str(raw).strip()
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat()


def run_fetch_daily(workspace, event_date: str) -> None:
    cmd = [sys.executable, "-m", "src.main", "fetch-daily", "--date", event_date]
    result = subprocess.run(cmd, cwd=workspace, text=True, capture_output=True)
    if result.stdout:
        logger.info(result.stdout)
    if result.stderr:
        logger.warning(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"fetch-daily failed (exit {result.returncode}): {result.stderr or result.stdout}"
        )


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logging.basicConfig(level=logging.INFO)
    apply_secrets()

    event_date = resolve_fetch_date(event or {})
    git_repo, branch, github_pat = git_settings_from_env()
    workspace = clone_or_update(github_pat, git_repo, branch)

    run_fetch_daily(workspace, event_date)
    events_path = f"data/events_{event_date}.json"
    committed = commit_and_push(
        [events_path],
        f"chore(data): fetch events {event_date}",
        github_pat=github_pat,
        git_repo=git_repo,
        branch=branch,
    )

    return {
        "status": "ok",
        "job": "fetch-daily",
        "date": event_date,
        "committed": committed,
        "events_file": events_path,
    }
