"""Lambda handler: sync trade history every 6 hours, commit analysis JSON to git."""

import logging
import subprocess
import sys
from typing import Any, Dict, Optional

from lambda_handlers.git_sync import clone_or_update, commit_and_push, git_settings_from_env
from lambda_handlers.logging_util import configure_logging
from lambda_handlers.secrets import apply_secrets

logger = logging.getLogger(__name__)

ANALYSIS_PATHS = [
    "data/analysis/trade_history.json",
    "data/analysis/sync_state.json",
    "data/analysis/resolutions_cache.json",
]


def run_sync_trade_history(workspace: str, init_days: Optional[int]) -> None:
    cmd = [sys.executable, "-m", "src.main", "sync-trade-history", "--skip-price-drop"]
    if init_days is not None:
        cmd.extend(["--init-days", str(init_days)])
    result = subprocess.run(cmd, cwd=workspace, text=True, capture_output=True)
    if result.stdout:
        logger.info(result.stdout)
    if result.stderr:
        logger.warning(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"sync-trade-history failed (exit {result.returncode}): "
            f"{result.stderr or result.stdout}"
        )


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    configure_logging()
    apply_secrets()

    init_days = (event or {}).get("init_days")
    git_repo, branch, github_pat = git_settings_from_env()
    workspace = clone_or_update(github_pat, git_repo, branch)

    run_sync_trade_history(workspace, init_days)
    committed = commit_and_push(
        ANALYSIS_PATHS,
        "chore(data): sync trade history",
        github_pat=github_pat,
        git_repo=git_repo,
        branch=branch,
    )

    return {
        "status": "ok",
        "job": "sync-trade-history",
        "init_days": init_days,
        "committed": committed,
        "paths": ANALYSIS_PATHS,
    }
