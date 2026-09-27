"""Moon phase, computed offline (no network, no extra deps).

Uses the mean synodic month from a reference new moon. Accurate to within
roughly half a day, which is plenty for a journal.
"""
from __future__ import annotations

import datetime as dt
import math

ID = "moon_phase"
NAME = "Moon Phase"
TITLE = "Moon"
VERSION = "1.1.0"
AUTHOR = "The Daily Vibe"
API_VERSION = 1
DESCRIPTION = "Moon phase name, illumination and emoji (offline)."
SETTINGS = [
    {"key": "show_details", "label": "Show age and waxing/waning", "type": "bool", "default": True},
    {"key": "hemisphere", "label": "Hemisphere", "type": "choice", "choices": ["northern", "southern"],
     "default": "northern", "help": "Crescent emoji are mirrored in the southern hemisphere."},
]
SOUTHERN = {"🌒": "🌘", "🌓": "🌗", "🌔": "🌖", "🌖": "🌔", "🌗": "🌓", "🌘": "🌒"}

SYNODIC_MONTH = 29.530588853
# Reference new moon: 2000-01-06 18:14 UTC
REFERENCE_NEW_MOON = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)

PHASES = [
    # (upper bound of age in days, name, emoji)
    (1.84566, "New Moon", "🌑"),
    (5.53699, "Waxing Crescent", "🌒"),
    (9.22831, "First Quarter", "🌓"),
    (12.91963, "Waxing Gibbous", "🌔"),
    (16.61096, "Full Moon", "🌕"),
    (20.30228, "Waning Gibbous", "🌖"),
    (23.99361, "Last Quarter", "🌗"),
    (27.68493, "Waning Crescent", "🌘"),
    (SYNODIC_MONTH + 1, "New Moon", "🌑"),
]


def moon_info(when: dt.datetime | dt.date) -> dict:
    if not isinstance(when, dt.datetime):
        when = dt.datetime(when.year, when.month, when.day, 12)  # local noon
    if when.tzinfo is None:
        when = when.astimezone()  # treat naive as local time
    days = (when - REFERENCE_NEW_MOON).total_seconds() / 86400
    age = days % SYNODIC_MONTH
    illumination = (1 - math.cos(2 * math.pi * age / SYNODIC_MONTH)) / 2
    for bound, name, emoji in PHASES:
        if age < bound:
            break
    return {
        "age_days": age,
        "illumination": illumination,
        "phase": name,
        "emoji": emoji,
        "waxing": age < SYNODIC_MONTH / 2,
    }


def render(date, context) -> str:
    settings = context.get("settings", {}) if context else {}
    info = moon_info(date)
    emoji = info["emoji"]
    if settings.get("hemisphere") == "southern":
        emoji = SOUTHERN.get(emoji, emoji)
    text = f"{emoji} **{info['phase']}** — {info['illumination'] * 100:.0f}% illuminated"
    if settings.get("show_details", True):
        trend = "waxing" if info["waxing"] else "waning"
        text += f" ({trend}, {info['age_days']:.1f} days old)"
    return text
