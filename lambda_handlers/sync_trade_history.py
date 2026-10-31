"""Lambda handler: sync trade history every 1 hour, commit analysis JSON to git."""

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
  # Written by refresh_timezone_skip_denylist during sync (not embedded in trade_history).
    "data/analysis/timezone_skip_denylist.json",
]


def _notify_sync_failure(error: BaseException) -> None:
    """Best-effort Telegram alert so a broken sync is not silent.

    The dashboard reads the committed trade_history.json, so a failing sync
    silently freezes the dashboard. This notification makes the failure visible.
    """
    try:
        from src.notify.telegram import send_telegram_message

        send_telegram_message(
            "🚨 <b>SYNC-TRADE-HISTORY FAILED</b>\n"
            f"❌ {_short_error(error)}\n"
            "The dashboard will keep showing the last successful sync."
        )
    except Exception:
        logger.exception("Telegram sync-failure notify failed")


def _short_error(error: BaseException) -> str:
    text = str(error).strip() or error.__class__.__name__
    # Telegram HTML mode: the message is not escaped, so strip angle brackets.
    return text.replace("<", "(").replace(">", ")")[:800]


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

    try:
        run_sync_trade_history(workspace, init_days)
        committed = commit_and_push(
            ANALYSIS_PATHS,
            "chore(data): sync trade history",
            github_pat=github_pat,
            git_repo=git_repo,
            branch=branch,
        )
    except Exception as exc:
        logger.exception("sync-trade-history failed")
        _notify_sync_failure(exc)
        raise

    return {
        "status": "ok",
        "job": "sync-trade-history",
        "init_days": init_days,
        "committed": committed,
        "paths": ANALYSIS_PATHS,
    }
