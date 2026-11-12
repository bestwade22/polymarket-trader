"""Clone the trading repo into /tmp and push data commits back to GitHub."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WORKSPACE = Path("/tmp/repo")
GATE_DATA_DIR = Path("/tmp/gate_data")
GIT_USER_NAME = "polymarket-lambda[bot]"
GIT_USER_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
PUSH_MAX_ATTEMPTS = 3
FETCH_DEPTH = 30

# The repo tracks a large amount of bulk market data (hundreds of MB of dated
# events/selections snapshots). A full checkout does not fit in the Lambda
# /tmp filesystem (512 MB by default) and the clone fails with
# "OSError: [Errno 28] No space left on device".
#
# The Lambda runtime only needs the code plus the current data files. The old
# dated events snapshots are read by the offline analysis scripts and the
# dashboard, never by the handlers, so they are excluded from the checkout.
#
# What the handlers actually touch at runtime:
#   - data/events_<date>.json for today and yesterday (trade gate + hourly run)
#   - data/selections/       (written by the hourly runner, committed back)
#   - data/positions/        (bought/sold events)
#   - data/analysis/         (trade history, sync state, skip denylist)
#   - data/stop_loss/        (stop loss snapshots)
#   - data/simulation/ is offline-only and is excluded.
SPARSE_INCLUDE_DIRS = (
    "/src/",
    "/config/",
    "/scripts/",
    "/lambda_handlers/",
    "/infrastructure/",
    "/data/analysis/",
    "/data/positions/",
    "/data/selections/",
    "/data/stop_loss/",
)

# Number of days of dated events files to keep in the checkout. The handlers
# read today and yesterday; the extra days give a safety margin around
# midnight (HKT) and clock skew between Lambda and GitHub.
EVENT_HISTORY_DAYS = 7

# Extra days kept on the future side of the window, because fetch-daily writes
# the Hong Kong date while the trade gate looks up the UTC date.
EVENT_HISTORY_PADDING_DAYS = 2

_SECRET_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"ghp_[A-Za-z0-9_]+"),
    re.compile(r"x-access-token:[^@\s/]+"),
)


def _redact(text: str) -> str:
    out = text or ""
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("***", out)
    return out


def _run(
    cmd: list[str],
    *,
    cwd: Optional[Path] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    safe = " ".join(_redact(part) for part in cmd)
    logger.debug("Running: %s", safe)
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        err = _redact((result.stderr or result.stdout or "").strip())
        logger.error("Command failed (%s): %s | %s", result.returncode, safe, err)
        raise RuntimeError(f"Command failed ({result.returncode}): {safe}: {err}")
    return result


def _remote_url(github_pat: str, git_repo: str) -> str:
    owner, repo = git_repo.split("/", 1)
    return f"https://x-access-token:{github_pat}@github.com/{owner}/{repo}.git"


def _configure_identity() -> None:
    _run(["git", "config", "user.name", GIT_USER_NAME], cwd=WORKSPACE)
    _run(["git", "config", "user.email", GIT_USER_EMAIL], cwd=WORKSPACE)


def _configure_remote(github_pat: str, git_repo: str) -> str:
    url = _remote_url(github_pat, git_repo)
    remotes = {
        line.strip()
        for line in _run(["git", "remote"], cwd=WORKSPACE, check=False).stdout.splitlines()
        if line.strip()
    }
    if "origin" in remotes:
        _run(["git", "remote", "set-url", "origin", url], cwd=WORKSPACE)
    else:
        _run(["git", "remote", "add", "origin", url], cwd=WORKSPACE)
    return url


def _cleanup_git_state() -> None:
    """Clear leftover rebase/merge locks from a previous warm Lambda invoke."""
    for abort_cmd in (
        ["git", "rebase", "--abort"],
        ["git", "merge", "--abort"],
        ["git", "am", "--abort"],
    ):
        subprocess.run(abort_cmd, cwd=WORKSPACE, text=True, capture_output=True)
    lock = WORKSPACE / ".git" / "index.lock"
    if lock.exists():
        try:
            lock.unlink()
        except OSError:
            logger.warning("Could not remove stale index.lock")


def _cleanup_stale_temp_files() -> None:
    """Remove leftover .tmp files from earlier failed writes.

    A Lambda container is reused across invocations. When a write fails part
    way through (for example because the volume was full) the .tmp file is
    left behind, and the leftovers pile up until every write fails.
    """
    for directory in (WORKSPACE / "data", GATE_DATA_DIR):
        if not directory.is_dir():
            continue
        for stale in directory.glob("*.tmp"):
            try:
                stale.unlink()
                logger.info("Removed stale temp file %s", stale)
            except OSError:
                logger.warning("Could not remove stale temp file %s", stale)


def _read_file_bytes(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _restore_files(snapshots: dict[str, bytes]) -> None:
    for rel, content in snapshots.items():
        dest = WORKSPACE / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)


def _sparse_patterns(now: Optional[datetime] = None) -> str:
    """Build the sparse-checkout pattern list for the current date.

    Keeps the code, the data directories the handlers use, and only the most
    recent dated events files. Everything older is left out of the checkout so
    the clone fits inside the Lambda /tmp volume.

    Pattern order matters in git's non-cone mode: later patterns win, so the
    bulk events files are excluded first and the few dates that are kept are
    re-included afterwards.
    """
    reference = now or datetime.now(timezone.utc)
    patterns = ["/*", "!/*/"]
    patterns.extend(SPARSE_INCLUDE_DIRS)

    # Keep the data/ directory itself so individual files can be re-included.
    patterns.append("/data/")

    # Keep the small always-current data files in data/.
    patterns.append("/data/*.json")

    # Drop the bulk dated events files, then re-include only the recent ones.
    #
    # The window is centred on UTC but padded on the future side, because
    # fetch-daily writes the Hong Kong date while the trade gate looks up the
    # UTC date, so around midnight the two can differ by a day.
    patterns.append("!/data/events_*.json")
    for offset in range(-EVENT_HISTORY_PADDING_DAYS, EVENT_HISTORY_DAYS + 1):
        day = (reference + timedelta(days=offset)).date().isoformat()
        patterns.append(f"/data/events_{day}.json")

    return "\n".join(patterns) + "\n"


def _apply_sparse_checkout() -> None:
    """Restrict the worktree to the paths the runtime actually needs.

    Without this the checkout holds ~500 MB of dated market snapshots, which
    overflows the Lambda /tmp volume before the handler can even start.
    """
    patterns = _sparse_patterns()
    _run(["git", "sparse-checkout", "init", "--no-cone"], cwd=WORKSPACE)
    _run(["git", "config", "core.sparseCheckout", "true"], cwd=WORKSPACE)
    # Write the pattern file directly: `git sparse-checkout set` needs the
    # patterns on stdin and the git version in the Lambda base image does not
    # accept them that way.
    info_dir = WORKSPACE / ".git" / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "sparse-checkout").write_text(patterns)
    _run(["git", "read-tree", "-mu", "HEAD"], cwd=WORKSPACE)


def clone_or_update(github_pat: str, git_repo: str, branch: str) -> Path:
    url = _remote_url(github_pat, git_repo)

    _cleanup_stale_temp_files()

    if WORKSPACE.exists() and (WORKSPACE / ".git").is_dir():
        try:
            _cleanup_git_state()
            _configure_remote(github_pat, git_repo)
            _run(
                ["git", "fetch", "origin", branch, "--depth", str(FETCH_DEPTH)],
                cwd=WORKSPACE,
            )
            _run(["git", "checkout", "-B", branch, f"origin/{branch}"], cwd=WORKSPACE)
            _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=WORKSPACE)
            _run(["git", "clean", "-fd"], cwd=WORKSPACE)
            _configure_identity()
            _apply_sparse_checkout()
            return WORKSPACE
        except Exception as exc:
            logger.warning(
                "Reusing %s failed (%s); recloning",
                WORKSPACE,
                _redact(str(exc)),
            )
            shutil.rmtree(WORKSPACE, ignore_errors=True)

    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE, ignore_errors=True)

    WORKSPACE.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "git",
            "clone",
            "--depth",
            str(FETCH_DEPTH),
            "--no-checkout",
            "-b",
            branch,
            url,
            str(WORKSPACE),
        ]
    )
    _configure_identity()
    _apply_sparse_checkout()
    return WORKSPACE


def _sync_worktree_to_head() -> None:
    """Drop unstaged/untracked files so rebase can run (target paths are already committed)."""
    _run(["git", "reset", "--hard", "HEAD"], cwd=WORKSPACE)
    _run(["git", "clean", "-fd"], cwd=WORKSPACE)


def _rebase_onto_origin(branch: str) -> None:
    _sync_worktree_to_head()
    result = _run(
        ["git", "rebase", f"origin/{branch}"],
        cwd=WORKSPACE,
        check=False,
    )
    if result.returncode == 0:
        return
    err = _redact((result.stderr or result.stdout or "").strip())
    _cleanup_git_state()
    raise RuntimeError(f"rebase onto origin/{branch} failed: {err}")


def _reapply_paths_on_origin(paths: list[str], message: str, branch: str) -> None:
    """Last-writer-wins for the given paths after concurrent remote updates."""
    snapshots = {}
    for path in paths:
        content = _read_file_bytes(WORKSPACE / path)
        if content is not None:
            snapshots[path] = content
    if not snapshots:
        raise RuntimeError(f"No local files to reapply from paths: {paths}")

    _cleanup_git_state()
    _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=WORKSPACE)
    _restore_files(snapshots)
    for path in snapshots:
        _run(["git", "add", "-f", "--sparse", path], cwd=WORKSPACE)

    diff = subprocess.run(
        ["git", "diff", "--staged", "--quiet"],
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
    )
    if diff.returncode == 0:
        logger.info("Remote already has the same content for %s", list(snapshots))
        return
    _run(["git", "commit", "-m", message], cwd=WORKSPACE)
    _sync_worktree_to_head()


def commit_and_push(
    paths: list[str],
    message: str,
    *,
    github_pat: str,
    git_repo: str,
    branch: str,
) -> bool:
    existing = [path for path in paths if (WORKSPACE / path).exists()]
    if not existing:
        logger.info("No files to commit from paths: %s", paths)
        return False

    _cleanup_git_state()
    for path in existing:
        _run(["git", "add", "-f", "--sparse", path], cwd=WORKSPACE)

    diff = subprocess.run(
        ["git", "diff", "--staged", "--quiet"],
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
    )
    if diff.returncode == 0:
        logger.info("No staged changes to commit")
        return False

    _run(["git", "commit", "-m", message], cwd=WORKSPACE)
    _sync_worktree_to_head()

    last_error: Optional[Exception] = None
    for attempt in range(1, PUSH_MAX_ATTEMPTS + 1):
        try:
            _configure_remote(github_pat, git_repo)
            _run(
                ["git", "fetch", "origin", branch, "--depth", str(FETCH_DEPTH)],
                cwd=WORKSPACE,
            )
            try:
                _rebase_onto_origin(branch)
            except RuntimeError as rebase_exc:
                logger.warning(
                    "Rebase failed on attempt %s/%s (%s); reapplying paths",
                    attempt,
                    PUSH_MAX_ATTEMPTS,
                    rebase_exc,
                )
                _reapply_paths_on_origin(existing, message, branch)

            push = _run(
                ["git", "push", "origin", f"HEAD:{branch}"],
                cwd=WORKSPACE,
                check=False,
            )
            if push.returncode == 0:
                logger.info("Committed and pushed: %s", existing)
                return True

            err = _redact((push.stderr or push.stdout or "").strip())
            last_error = RuntimeError(
                f"git push failed ({push.returncode}): {err}"
            )
            logger.warning(
                "Push rejected on attempt %s/%s: %s",
                attempt,
                PUSH_MAX_ATTEMPTS,
                err,
            )
            _cleanup_git_state()
            time.sleep(min(2 * attempt, 5))
        except Exception as exc:
            last_error = exc
            logger.warning(
                "commit_and_push attempt %s/%s failed: %s",
                attempt,
                PUSH_MAX_ATTEMPTS,
                _redact(str(exc)),
            )
            _cleanup_git_state()
            time.sleep(min(2 * attempt, 5))

    raise RuntimeError(
        f"commit_and_push failed after {PUSH_MAX_ATTEMPTS} attempts: "
        f"{_redact(str(last_error) if last_error else 'unknown error')}"
    )


def git_settings_from_env() -> tuple[str, str, str]:
    git_repo = os.environ.get("GIT_REPO", "").strip()
    branch = os.environ.get("GIT_BRANCH", "main").strip() or "main"
    github_pat = os.environ.get("GITHUB_PAT", "").strip()
    if not git_repo:
        raise RuntimeError("GIT_REPO environment variable is required")
    if not github_pat:
        raise RuntimeError("GITHUB_PAT is required (from Secrets Manager)")
    return git_repo, branch, github_pat
