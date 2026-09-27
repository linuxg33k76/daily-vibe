"""Journaling streaks.

A day counts only if its entry contains something the user actually wrote:
plugin blocks, the untouched template lines and placeholders don't count.
The current streak may end today or yesterday: it isn't broken until today
is over.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from daily_vibe.storage import Journal, render_template

_BLOCK_RE = re.compile(r"<!--\s*plugin:([\w.-]+)\s*-->.*?<!--\s*/plugin:\1\s*-->", re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def has_real_content(text: str, template: str, date: dt.date) -> bool:
    text = _COMMENT_RE.sub("", _BLOCK_RE.sub("", text))
    template_lines = {l.strip() for l in render_template(template, date, "").splitlines() if l.strip()}
    for line in text.splitlines():
        s = line.strip()
        if s and s not in template_lines:
            return True
    return False


@dataclass
class StreakInfo:
    current: int
    longest: int
    today_done: bool
    last_day: dt.date | None

    def message(self) -> str:
        best = f"  ·  best: {self.longest}" if self.longest else ""
        if self.current == 0:
            if self.longest:
                return f"🌱 No streak right now — a few lines today starts a new one.{best}"
            return "🌱 Write a few lines today to start your streak."
        if self.today_done:
            return f"🔥 {self.current}-day streak — keep it going!{best}"
        return f"🔥 {self.current}-day streak — write today to make it {self.current + 1}.{best}"


def compute_streak(real_days: set[dt.date], today: dt.date) -> StreakInfo:
    longest = run = 0
    prev = None
    for d in sorted(real_days):
        run = run + 1 if prev is not None and d - prev == dt.timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    today_done = today in real_days
    anchor = today if today_done else today - dt.timedelta(days=1)
    current = 0
    while anchor in real_days:
        current += 1
        anchor -= dt.timedelta(days=1)
    last = max((d for d in real_days if d <= today), default=None)
    return StreakInfo(current, longest, today_done, last)


class StreakTracker:
    """Caches per-file 'real content' results by mtime so updates are cheap."""

    def __init__(self):
        self._cache: dict[Path, tuple[tuple[int, int], str, bool]] = {}

    def real_days(self, journal: Journal, template: str) -> set[dt.date]:
        days = set()
        for date in journal.list_dates():
            path = journal.entry_path(date)
            try:
                st = path.stat()
                mtime = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
            cached = self._cache.get(path)
            if cached and cached[0] == mtime and cached[1] == template:
                real = cached[2]
            else:
                real = has_real_content(journal.read(date), template, date)
                self._cache[path] = (mtime, template, real)
            if real:
                days.add(date)
        return days

    def compute(self, journal: Journal, template: str, today: dt.date | None = None) -> StreakInfo:
        return compute_streak(self.real_days(journal, template), today or dt.date.today())
