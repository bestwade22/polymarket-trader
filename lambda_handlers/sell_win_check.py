"""Lambda handler: sell-win check every hour, commit sell-order audit to git."""

import logging
import os
import subprocess
import sys
from typing import Any, Dict, List

from lambda_handlers.git_sync import clone_or_update, commit_and_push, git_settings_from_env
from lambda_handlers.logging_util import configure_logging
from lambda_handlers.secrets import apply_secrets

logger = logging.getLogger(__name__)


def count_live_positions() -> int:
    """Lightweight pre-clone gate: return number of open positions."""
    from config.settings import settings
    from src.api.data_client import fetch_user_positions

    if not settings.deposit_wallet_address:
        return 0
    try:
        return len(fetch_user_positions(settings.deposit_wallet_address))
    except Exception as exc:
        logger.warning("Pre-clone position count failed: %s", exc)
        return -1


def run_check_sell_win(workspace: str, live: bool) -> None:
    cmd = [sys.executable, "-m", "src.main", "check-sell-win"]
    if live:
        cmd.append("--live")
    result = subprocess.run(cmd, cwd=workspace, text=True, capture_output=True)
    if result.stdout:
        logger.info(result.stdout)
    if result.stderr:
        logger.warning(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"check-sell-win failed (exit {result.returncode}): {result.stderr or result.stdout}"
        )


def collect_commit_paths(workspace: str) -> List[str]:
    return ["data/positions/sold_events.json"]


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    configure_logging()
    apply_secrets()

    force = bool((event or {}).get("force"))
    position_count = count_live_positions()
    if position_count == 0 and not force:
        return {
            "status": "skipped",
            "job": "check-sell-win",
            "reason": "no_positions",
            "position_count": position_count,
        }

    git_repo, branch, github_pat = git_settings_from_env()
    workspace = clone_or_update(github_pat, git_repo, branch)

    sell_win_dry_run = os.environ.get("SELL_WIN_DRY_RUN", "false")
    live = str(sell_win_dry_run).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    live = not live
    logger.info(
        "sell-win mode: SELL_WIN_DRY_RUN=%s live=%s",
        sell_win_dry_run,
        live,
    )
    if (event or {}).get("live") is True:
        live = True

    run_check_sell_win(workspace, live=live)
    commit_paths = collect_commit_paths(workspace)
    committed = commit_and_push(
        commit_paths,
        "chore(data): sell-win check",
        github_pat=github_pat,
        git_repo=git_repo,
        branch=branch,
    )

    return {
        "status": "ok",
        "job": "check-sell-win",
        "live": live,
        "position_count": position_count,
        "committed": committed,
        "paths": commit_paths,
    }
