"""'On This Day': entries from the same month/day in earlier years, plus
one month and one week ago.

Feb 29 handling:
  * viewing Feb 29: leap years show their Feb 29; non-leap years fall back to Feb 28.
  * viewing Feb 28 in a non-leap year: Feb 29 entries from leap years are included
    too (that day doesn't exist this year, so it shows up on the 28th).
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

from daily_vibe.storage import Journal


@dataclass
class Memory:
    label: str       # "2025 · 1 year ago", "One month ago", ...
    date: dt.date
    excerpt: str
    kind: str        # "year" | "month" | "week"


def _same_day_in_year(date: dt.date, year: int) -> list[dt.date]:
    if date.month == 2 and date.day == 29:
        return [dt.date(year, 2, 29)] if calendar.isleap(year) else [dt.date(year, 2, 28)]
    days = [dt.date(year, date.month, date.day)]
    if date.month == 2 and date.day == 28 and not calendar.isleap(date.year) and calendar.isleap(year):
        days.append(dt.date(year, 2, 29))
    return days


def one_month_before(date: dt.date) -> dt.date:
    year, month = (date.year, date.month - 1) if date.month > 1 else (date.year - 1, 12)
    return dt.date(year, month, min(date.day, calendar.monthrange(year, month)[1]))


def on_this_day(journal: Journal, date: dt.date, include_recent: bool = True,
                excerpt_len: int = 140) -> list[Memory]:
    existing = set(journal.list_dates())
    memories: list[Memory] = []
    years = sorted({d.year for d in existing if d.year < date.year}, reverse=True)
    for year in years:
        for d in _same_day_in_year(date, year):
            if d in existing:
                ago = date.year - year
                note = "" if (d.month, d.day) == (date.month, date.day) else f" (Feb {d.day})"
                memories.append(Memory(f"{year} · {ago} year{'s' if ago != 1 else ''} ago{note}",
                                       d, journal.summary(d, excerpt_len), "year"))
    if include_recent:
        for label, d, kind in (("One month ago", one_month_before(date), "month"),
                               ("One week ago", date - dt.timedelta(days=7), "week")):
            if d in existing:
                memories.append(Memory(label, d, journal.summary(d, excerpt_len), kind))
    return memories
