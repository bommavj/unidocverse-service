"""Tax rules loaded from tax_rules.yaml — rates, wage bases, and deadlines as data."""
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml

_RULES_PATH = Path(__file__).with_name("tax_rules.yaml")


@lru_cache(maxsize=1)
def rules() -> Dict:
    return yaml.safe_load(_RULES_PATH.read_text())


def by_year(value, year: int):
    """Resolve a per-year value; falls back to the latest known year (flagged by caller)."""
    if not isinstance(value, dict):
        return value, year
    if year in value:
        return value[year], year
    latest = max(y for y in value if y <= year) if any(y <= year for y in value) else min(value)
    return value[latest], latest


def next_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def upcoming_deadlines(today: date, topics: Optional[List[str]] = None, months: int = 12) -> List[Dict]:
    end = date(today.year + (today.month + months - 1) // 12, (today.month + months - 1) % 12 + 1, 1)
    out = []
    for item in rules()["calendar"]:
        if topics and not set(topics) & set(item["topics"]):
            continue
        for year in (today.year, today.year + 1):
            try:
                due = next_business_day(date(year, item["month"], item["day"]))
            except ValueError:
                continue
            if today <= due < end:
                out.append({**item, "due": due, "tax_year": year + item.get("tax_year_offset", 0)})
    return sorted(out, key=lambda x: x["due"])
