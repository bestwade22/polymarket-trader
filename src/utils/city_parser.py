import re
from datetime import date
from typing import Optional

TITLE_PATTERN = re.compile(
    r"Highest temperature in ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*) on "
    r"(January|February|March|April|May|June|July|August|September|October|November|December) "
    r"(\d{1,2})\??",
    re.IGNORECASE,
)

MONTH_MAP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_city_from_title(title: str) -> Optional[str]:
    match = TITLE_PATTERN.search(title or "")
    if not match:
        return None
    return match.group(1).strip()


def parse_event_date_from_title(title: str) -> Optional[date]:
    match = TITLE_PATTERN.search(title or "")
    if not match:
        return None
    month = MONTH_MAP[match.group(2).lower()]
    day = int(match.group(3))
    year = date.today().year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def is_highest_temperature_event(title: str) -> bool:
    return parse_city_from_title(title) is not None
