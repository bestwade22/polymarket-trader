"""Clone the trading repo into /tmp and push data commits back to GitHub."""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WORKSPACE = Path("/tmp/repo")
GIT_USER_NAME = "polymarket-lambda[bot]"
GIT_USER_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def _run(cmd: list[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True)


def _remote_url(github_pat: str, git_repo: str) -> str:
    owner, repo = git_repo.split("/", 1)
    return f"https://x-access-token:{github_pat}@github.com/{owner}/{repo}.git"


def clone_or_update(github_pat: str, git_repo: str, branch: str) -> Path:
    url = _remote_url(github_pat, git_repo)
    if WORKSPACE.exists():
        _run(["git", "fetch", "origin", branch, "--depth", "1"], cwd=WORKSPACE)
        _run(["git", "checkout", branch], cwd=WORKSPACE)
        _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=WORKSPACE)
    else:
        WORKSPACE.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--depth", "1", "-b", branch, url, str(WORKSPACE)])

    _run(["git", "config", "user.name", GIT_USER_NAME], cwd=WORKSPACE)
    _run(["git", "config", "user.email", GIT_USER_EMAIL], cwd=WORKSPACE)
    return WORKSPACE


def pull_rebase(github_pat: str, git_repo: str, branch: str) -> None:
    url = _remote_url(github_pat, git_repo)
    _run(["git", "pull", "--rebase", url, branch], cwd=WORKSPACE)


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

    for path in existing:
        _run(["git", "add", "-f", path], cwd=WORKSPACE)

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
    pull_rebase(github_pat, git_repo, branch)
    url = _remote_url(github_pat, git_repo)
    _run(["git", "push", url, f"HEAD:{branch}"], cwd=WORKSPACE)
    logger.info("Committed and pushed: %s", existing)
    return True


def git_settings_from_env() -> tuple[str, str, str]:
    git_repo = os.environ.get("GIT_REPO", "").strip()
    branch = os.environ.get("GIT_BRANCH", "main").strip() or "main"
    github_pat = os.environ.get("GITHUB_PAT", "").strip()
    if not git_repo:
        raise RuntimeError("GIT_REPO environment variable is required")
    if not github_pat:
        raise RuntimeError("GITHUB_PAT is required (from Secrets Manager)")
    return git_repo, branch, github_pat
