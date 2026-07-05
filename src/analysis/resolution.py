"""Gamma event resolution cache for trade history."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from config.settings import RESOLUTIONS_CACHE_FILE, ensure_dirs
from src.api.gamma_client import GammaClient, get_winning_market, winning_temp_label
from src.utils.market_parser import get_yes_token_id

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
_LEGACY_RESOLUTIONS_DIR = RESOLUTIONS_CACHE_FILE.parent / "resolutions"


@dataclass
class CachedResolution:
    closed: bool
    title: str
    winning_temp: Optional[str]
    winning_token_id: Optional[str]

    @classmethod
    def from_gamma_event(cls, event: dict[str, Any]) -> "CachedResolution":
        winning = get_winning_market(event)
        return cls(
            closed=bool(event.get("closed")),
            title=str(event.get("title") or "").strip(),
            winning_temp=winning_temp_label(event),
            winning_token_id=get_yes_token_id(winning) if winning else None,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CachedResolution":
        return cls(
            closed=bool(data.get("closed")),
            title=str(data.get("title") or "").strip(),
            winning_temp=data.get("winning_temp") or None,
            winning_token_id=data.get("winning_token_id") or None,
        )


def _load_cache_file() -> dict[str, Any]:
    if not RESOLUTIONS_CACHE_FILE.exists():
        return {"version": CACHE_VERSION, "events": {}}
    try:
        data = json.loads(RESOLUTIONS_CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": CACHE_VERSION, "events": {}}
    if not isinstance(data, dict):
        return {"version": CACHE_VERSION, "events": {}}
    events = data.get("events")
    if not isinstance(events, dict):
        data["events"] = {}
    return data


def _save_cache_file(cache: dict[str, Any]) -> None:
    ensure_dirs()
    cache["version"] = CACHE_VERSION
    RESOLUTIONS_CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _migrate_legacy_per_file_cache(cache: dict[str, Any]) -> bool:
    """Import old data/analysis/resolutions/*.json into the unified cache."""
    if not _LEGACY_RESOLUTIONS_DIR.is_dir():
        return False

    events: dict[str, Any] = cache.setdefault("events", {})
    imported = 0
    for path in sorted(_LEGACY_RESOLUTIONS_DIR.glob("*.json")):
        slug = path.stem
        if slug in events:
            continue
        try:
            event = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(event, dict):
            continue
        events[slug] = asdict(CachedResolution.from_gamma_event(event))
        imported += 1

    if imported:
        logger.info("Migrated %d legacy resolution files into %s", imported, RESOLUTIONS_CACHE_FILE.name)
        _save_cache_file(cache)
    return imported > 0


def load_cached_resolution(event_slug: str) -> Optional[CachedResolution]:
    cache = _load_cache_file()
    if not cache.get("events") and _migrate_legacy_per_file_cache(cache):
        cache = _load_cache_file()

    row = cache.get("events", {}).get(event_slug)
    if not isinstance(row, dict):
        return None
    return CachedResolution.from_dict(row)


def save_cached_resolution(event_slug: str, resolution: CachedResolution) -> None:
    cache = _load_cache_file()
    events = cache.setdefault("events", {})
    events[event_slug] = asdict(resolution)
    _save_cache_file(cache)


def fetch_resolved_event(
    event_slug: str,
    client: Optional[GammaClient] = None,
    *,
    use_cache: bool = True,
) -> Optional[CachedResolution]:
    if use_cache:
        cached = load_cached_resolution(event_slug)
        if cached is not None:
            return cached

    gamma = client or GammaClient()
    event = gamma.fetch_event_by_slug(event_slug)
    if event is None:
        return None

    resolution = CachedResolution.from_gamma_event(event)
    if resolution.closed or resolution.winning_temp:
        save_cached_resolution(event_slug, resolution)
    return resolution


def resolve_winning_temp(
    event_slug: str,
    client: Optional[GammaClient] = None,
) -> Optional[str]:
    resolution = fetch_resolved_event(event_slug, client=client)
    if not resolution:
        return None
    return resolution.winning_temp
