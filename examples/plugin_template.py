"""Quote of the Day - an example / template plugin for The Daily Vibe.

Copy this file into your user plugins folder (Preferences → Plugins →
"Open plugins folder"), rename it, change ID/NAME, click "Reload plugins" and
tick it in the list. Works fully offline.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

# --- metadata (plugin API v1) ------------------------------------------------------
ID = "quote_of_the_day"          # unique + stable: used in config.toml and block markers
NAME = "Quote of the Day"        # shown in Preferences
TITLE = "Quote"                  # block header ("## Quote"); set to None for no header
VERSION = "1.0.0"
DESCRIPTION = "A daily quote, picked deterministically from a built-in or custom list."
AUTHOR = "Your Name"
API_VERSION = 1

# --- settings schema: rendered automatically in Preferences → Plugins ----------------
# types: string, int, float, bool, choice, secret
SETTINGS = [
    {"key": "style", "label": "Style", "type": "choice", "choices": ["blockquote", "italic", "plain"],
     "default": "blockquote", "help": "How the quote is formatted."},
    {"key": "show_author", "label": "Show author", "type": "bool", "default": True},
    {"key": "custom_file", "label": "Custom quotes file", "type": "string", "default": "",
     "help": "Optional text file, one quote per line as 'quote — author'. Empty = built-in list."},
]

QUOTES = [
    ("The secret of getting ahead is getting started.", "Mark Twain"),
    ("Well begun is half done.", "Aristotle"),
    ("What we think, we become.", "Buddha"),
    ("Simplicity is the ultimate sophistication.", "Leonardo da Vinci"),
    ("The best way out is always through.", "Robert Frost"),
    ("Little by little, one travels far.", "J.R.R. Tolkien"),
    ("Fill your paper with the breathings of your heart.", "William Wordsworth"),
]


def _load_quotes(path: str) -> list[tuple[str, str]]:
    if not path:
        return QUOTES
    quotes = []
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        if line.strip():
            text, _, author = line.partition(" — ")
            quotes.append((text.strip(), author.strip()))
    return quotes or QUOTES


def pick(date: dt.date, quotes: list[tuple[str, str]]) -> tuple[str, str]:
    """Same date -> same quote (stable across refreshes)."""
    digest = hashlib.sha256(date.isoformat().encode()).digest()
    return quotes[int.from_bytes(digest[:4], "big") % len(quotes)]


# --- hooks ---------------------------------------------------------------------------
def on_load(context) -> None:
    """Optional: called once after the plugin is discovered/reloaded.
    Raise to mark the plugin with a warning in Preferences."""
    custom = context["settings"].get("custom_file")
    if custom and not Path(custom).expanduser().is_file():
        raise FileNotFoundError(f"custom quotes file not found: {custom}")


def render(date: dt.date, context) -> str:
    """Required: return a Markdown block (without the header) for `date`.
    Runs on a background thread; don't touch Qt widgets here."""
    s = context["settings"]  # values from SETTINGS, with defaults applied
    text, author = pick(date, _load_quotes(s.get("custom_file", "")))
    attribution = f" — {author}" if s.get("show_author", True) and author else ""
    if s.get("style") == "italic":
        return f"*{text}*{attribution}"
    if s.get("style") == "plain":
        return f"{text}{attribution}"
    return f"> {text}{attribution}"


def on_entry_created(date: dt.date, context) -> None:
    """Optional: called (in the background) after a new entry file was written.
    Useful for side effects such as logging; the return value is ignored."""
    return None
