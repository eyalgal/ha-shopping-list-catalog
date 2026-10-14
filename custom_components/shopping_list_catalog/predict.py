"""Suggest products from purchase history."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date
from typing import Any

from homeassistant.util import dt as dt_util

# Backtested on real shopping history: suggest a product bought on at least two
# separate days when it was bought in 2 of the last 3 weeks or its usual
# interval has nearly passed, and not again within a few days of buying it.
MIN_DAYS_SINCE = 4
DUE_FRACTION = 0.8
RECENT_WEEKS = 3
RECENT_WEEKS_NEEDED = 2
INTERVALS_USED = 6


def suggest(purchases: list[dict[str, Any]], today: date) -> dict[str, dict[str, Any]]:
    """Return suggested products keyed by name, most overdue first."""
    by_name: dict[str, list[tuple[date, int, str]]] = defaultdict(list)
    for purchase in purchases:
        when = dt_util.parse_datetime(purchase["time"])
        if when is None:
            continue
        by_name[purchase["name"].strip().lower()].append(
            (dt_util.as_local(when).date(), purchase["quantity"], purchase["name"])
        )
    found = []
    for rows in by_name.values():
        dates = sorted({row[0] for row in rows})
        if len(dates) < 2:
            continue
        intervals = [(later - earlier).days for earlier, later in zip(dates, dates[1:], strict=False)]
        every = statistics.median(intervals[-INTERVALS_USED:])
        since = (today - dates[-1]).days
        recent = sum(
            any(week * 7 <= (today - day).days < (week + 1) * 7 for day in dates) for week in range(RECENT_WEEKS)
        )
        if since < MIN_DAYS_SINCE or (recent < RECENT_WEEKS_NEEDED and since < DUE_FRACTION * every):
            continue
        latest = max(rows)
        found.append(
            {
                "name": latest[2],
                "last": dates[-1].isoformat(),
                "since": since,
                "every": max(1, round(every)),
                "quantity": max(1, round(statistics.median(row[1] for row in rows))),
            }
        )
    found.sort(key=lambda item: (-item["since"] / item["every"], item["name"].lower()))
    return {item.pop("name"): item for item in found}
