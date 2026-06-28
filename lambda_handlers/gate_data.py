"""Fetch dated events JSON from GitHub for the trade gate (no full repo clone)."""

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

GATE_DATA_DIR = Path("/tmp/gate_data")
FETCH_TIMEOUT_SECONDS = 30


def event_file_dates_to_check(now_utc: datetime) -> list[date]:
    today = now_utc.date()
    return [today, today - timedelta(days=1)]


def fetch_events_for_gate(
    github_pat: str,
    git_repo: str,
    branch: str,
    dates: Iterable[str],
) -> Path:
    """Download events_YYYY-MM-DD.json files into GATE_DATA_DIR. Missing files are skipped."""
    owner, repo = git_repo.split("/", 1)
    GATE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {github_pat}"} if github_pat else {}

    for date_str in dates:
        url = (
            f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
            f"/data/events_{date_str}.json"
        )
        dest = GATE_DATA_DIR / f"events_{date_str}.json"
        try:
            response = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logger.warning("Gate fetch failed for %s: %s", date_str, exc)
            continue
        if response.status_code == 404:
            logger.info("No events file on GitHub for %s", date_str)
            continue
        response.raise_for_status()
        dest.write_text(response.text)
        logger.info("Fetched gate events: %s", dest.name)

    return GATE_DATA_DIR


def fetch_gate_data_from_env(now_utc: Optional[datetime] = None) -> Optional[Path]:
    """Fetch today/yesterday events files using GIT_* env vars. Returns None when unset."""
    import os

    git_repo = os.environ.get("GIT_REPO", "").strip()
    branch = os.environ.get("GIT_BRANCH", "main").strip() or "main"
    github_pat = os.environ.get("GITHUB_PAT", "").strip()
    if not git_repo or not github_pat:
        return None

    now = now_utc or datetime.now(timezone.utc)
    dates = [d.isoformat() for d in event_file_dates_to_check(now)]
    return fetch_events_for_gate(github_pat, git_repo, branch, dates)
