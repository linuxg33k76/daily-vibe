"""Bundled Unicode emoji data (resources/emoji.json, built by
scripts/build_emoji_data.py from emoji-test.txt + CLDR annotations), search
and the recently-used list."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "resources" / "emoji.json"
DEFAULT_MAX_VERSION = 15.1   # newer emoji often render as boxes with older fonts
RECENT_MAX = 32

GROUPS = [  # (group name in the data, tab label, tab icon)
    ("Smileys & Emotion", "Smileys", "😀"),
    ("People & Body", "People", "👋"),
    ("Animals & Nature", "Nature", "🌿"),
    ("Food & Drink", "Food", "🍎"),
    ("Travel & Places", "Travel", "✈️"),
    ("Activities", "Activities", "⚽"),
    ("Objects", "Objects", "💡"),
    ("Symbols", "Symbols", "❤️"),
    ("Flags", "Flags", "🏁"),
]
SUGGESTED = ["📓", "📔", "🌿", "💼", "❤️", "🏠", "✈️", "🏃", "🍳", "📚", "🎨", "🎵",
             "💡", "🌙", "☀️", "🧘", "🐶", "👶", "💰", "🎓", "🛠️", "🌱", "⭐", "🔒"]


@dataclass(frozen=True)
class Emoji:
    char: str
    name: str
    group: str
    keywords: tuple[str, ...]
    version: float


@lru_cache(maxsize=4)
def load(max_version: float = DEFAULT_MAX_VERSION) -> tuple[Emoji, ...]:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return tuple(Emoji(r["e"], r["n"], r["g"], tuple(r.get("k", ())), float(r.get("v", 0)))
                 for r in raw if float(r.get("v", 0)) <= max_version)


def by_group(max_version: float = DEFAULT_MAX_VERSION) -> dict[str, list[Emoji]]:
    out: dict[str, list[Emoji]] = {g: [] for g, _l, _i in GROUPS}
    for e in load(max_version):
        out.setdefault(e.group, []).append(e)
    return out


def lookup(char: str) -> Emoji | None:
    for e in load(99.0):
        if e.char == char or e.char.replace("\ufe0f", "") == char.replace("\ufe0f", ""):
            return e
    return None


def search(query: str, max_version: float = DEFAULT_MAX_VERSION, limit: int = 400) -> list[Emoji]:
    """All words of `query` must prefix-match a word of the name or a keyword.
    Ranked: exact name, name starts with query, name word matches, keyword matches."""
    words = [w for w in query.lower().replace(":", " ").split() if w]
    if not words:
        return []
    scored = []
    for order, e in enumerate(load(max_version)):
        name = e.name.lower()
        name_words = name.replace("-", " ").split()
        kw_words = [w for k in e.keywords for w in k.lower().replace("-", " ").split()]
        score = 0
        ok = True
        for w in words:
            if any(nw.startswith(w) for nw in name_words):
                score += 2
            elif any(kw.startswith(w) for kw in kw_words):
                score += 1
            elif w in name:
                score += 1
            else:
                ok = False
                break
        if not ok:
            continue
        q = " ".join(words)
        if name == q:
            score += 10
        elif name.startswith(q):
            score += 4
        scored.append((-score, order, e))
    scored.sort()
    return [e for _s, _o, e in scored[:limit]]


def push_recent(recent: list[str], char: str, max_len: int = RECENT_MAX) -> list[str]:
    return ([char] + [c for c in recent if c != char])[:max_len]
