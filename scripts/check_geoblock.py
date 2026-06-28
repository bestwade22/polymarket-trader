#!/usr/bin/env python3
"""Check Polymarket geographic restrictions for the current egress IP."""

import json
import sys
from typing import Any, Dict

import requests

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
TIMEOUT_SECONDS = 15


def fetch_geoblock() -> Dict[str, Any]:
    response = requests.get(GEOBLOCK_URL, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected geoblock response: {data!r}")
    return data


def is_trading_blocked(data: Dict[str, Any]) -> bool:
    return bool(data.get("blocked"))


def main() -> int:
    try:
        data = fetch_geoblock()
    except requests.RequestException as exc:
        print(f"geoblock check failed: {exc}", file=sys.stderr)
        return 2

    blocked = is_trading_blocked(data)
    print(json.dumps(data, indent=2))
    if blocked:
        country = data.get("country", "?")
        region = data.get("region", "?")
        print(
            f"Trading blocked for {country}/{region}. "
            "See https://docs.polymarket.com/api-reference/geoblock",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
