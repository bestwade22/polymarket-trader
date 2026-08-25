"""Backfill missing trade-history forecasts (Weather.com / WU) for analysis cols."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from src.analysis.models import TradeRecord
from src.analysis.pattern_enrichment import forecast_delta_c
from src.api.weather_client import WeatherClient
from src.utils.market_parser import forecast_vs_result_delta_c

logger = logging.getLogger(__name__)

# Align with Weather.com / WU scrape rollout used by forecast compare / bias.
FORECAST_BACKFILL_SINCE = date(2026, 8, 4)


def _parse_date(raw: str) -> Optional[date]:
    text = (raw or "")[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def apply_forecast_vs_win_deltas(rec: TradeRecord) -> list[str]:
    """Set/refresh om_vs_win_delta_c / wu_vs_win_delta_c when forecast + winner exist."""
    filled: list[str] = []
    if not rec.winning_temp:
        return filled

    om = forecast_vs_result_delta_c(
        rec.winning_temp,
        forecast_temp_c=rec.forecast_temp_c,
        forecast_temp_f=rec.forecast_temp_f,
    )
    if om is not None and rec.om_vs_win_delta_c != om:
        rec.om_vs_win_delta_c = om
        filled.append("om_vs_win_delta_c")

    wu = forecast_vs_result_delta_c(
        rec.winning_temp,
        forecast_temp_c=rec.forecast_wu_temp_c,
        forecast_temp_f=rec.forecast_wu_temp_f,
    )
    if wu is not None and rec.wu_vs_win_delta_c != wu:
        rec.wu_vs_win_delta_c = wu
        filled.append("wu_vs_win_delta_c")
    return filled


def backfill_missing_forecasts(
    records: list[TradeRecord],
    *,
    since: date = FORECAST_BACKFILL_SINCE,
    weather_client: Optional[WeatherClient] = None,
    limit: Optional[int] = None,
) -> dict[str, int]:
    """Fetch Weather.com/WU forecast for settled/open trades missing primary forecast.

    Only rows on/after `since`. Fills forecast_* + forecast_delta_c + om/wu vs-win deltas.
    """
    client = weather_client or WeatherClient()
    counts = {
        "attempted": 0,
        "fetched": 0,
        "failed": 0,
        "om_vs_win_delta_c": 0,
        "wu_vs_win_delta_c": 0,
        "forecast_delta_c": 0,
    }

    targets: list[TradeRecord] = []
    for rec in records:
        d = _parse_date(rec.date)
        if d is None or d < since:
            continue
        if rec.forecast_temp_c is not None or rec.forecast_temp_f is not None:
            # Still refresh vs-win deltas when winner is known.
            for field in apply_forecast_vs_win_deltas(rec):
                counts[field] = counts.get(field, 0) + 1
            continue
        targets.append(rec)

    if limit is not None:
        targets = targets[: max(0, int(limit))]

    for rec in targets:
        counts["attempted"] += 1
        try:
            forecast = client.fetch_forecast_max_temp(rec.city, rec.date)
        except Exception as exc:  # noqa: BLE001 — keep enrich resilient
            logger.warning("Forecast backfill failed %s %s: %s", rec.date, rec.city, exc)
            counts["failed"] += 1
            continue
        if forecast is None:
            counts["failed"] += 1
            continue

        counts["fetched"] += 1
        if forecast.temp_c is not None:
            rec.forecast_temp_c = float(forecast.temp_c)
        if forecast.temp_f is not None:
            rec.forecast_temp_f = float(forecast.temp_f)
        if forecast.source:
            rec.forecast_source = str(forecast.source)
        if forecast.wu_temp_c is not None:
            rec.forecast_wu_temp_c = float(forecast.wu_temp_c)
        if forecast.wu_temp_f is not None:
            rec.forecast_wu_temp_f = float(forecast.wu_temp_f)

        if rec.forecast_delta_c is None:
            delta = forecast_delta_c(
                rec.bought_temp,
                forecast_temp_f=rec.forecast_temp_f,
                forecast_temp_c=rec.forecast_temp_c,
            )
            if delta is not None:
                rec.forecast_delta_c = delta
                counts["forecast_delta_c"] += 1

        for field in apply_forecast_vs_win_deltas(rec):
            counts[field] = counts.get(field, 0) + 1

    if counts["attempted"] or counts["om_vs_win_delta_c"] or counts["wu_vs_win_delta_c"]:
        logger.info("Forecast backfill: %s", {k: v for k, v in counts.items() if v})
    return counts
