"""Find & Replace inside one entry (pure logic; the find bar is ui/find_bar.py).

Positions are Python str indices; the UI converts them to Qt UTF-16 positions."""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_MATCHES = 10000


@dataclass
class FindOptions:
    case: bool = False
    word: bool = False
    regex: bool = False


def compile_pattern(query: str, opts: FindOptions) -> re.Pattern | None:
    """None for an empty query; raises re.error for an invalid regex."""
    if not query:
        return None
    body = query if opts.regex else re.escape(query)
    if opts.word:
        body = rf"(?<!\w)(?:{body})(?!\w)"
    return re.compile(body, 0 if opts.case else re.IGNORECASE)


def find_all(text: str, query: str, opts: FindOptions, limit: int = MAX_MATCHES) -> list[tuple[int, int]]:
    pat = compile_pattern(query, opts)
    if pat is None:
        return []
    out = []
    for m in pat.finditer(text):
        if m.end() == m.start():
            continue            # empty regex matches are useless here
        out.append((m.start(), m.end()))
        if len(out) >= limit:
            break
    return out


def replacement_for(text: str, span: tuple[int, int], query: str, repl: str, opts: FindOptions) -> str:
    """Text that replaces the match at `span` (regex: \\1 / \\g<name> expand)."""
    if not opts.regex:
        return repl
    pat = compile_pattern(query, opts)
    m = pat.match(text, span[0]) if pat else None
    if m is None or m.end() != span[1]:
        return repl
    try:
        return m.expand(repl)
    except (re.error, IndexError):
        return repl


def replace_all(text: str, query: str, repl: str, opts: FindOptions) -> tuple[str, int]:
    spans = find_all(text, query, opts)
    out, pos = [], 0
    for s, e in spans:
        out.append(text[pos:s])
        out.append(replacement_for(text, (s, e), query, repl, opts))
        pos = e
    out.append(text[pos:])
    return "".join(out), len(spans)


def utf16_offsets(text: str):
    """Callable mapping Python indices to UTF-16 positions (fast for BMP-only text)."""
    if all(ord(c) <= 0xFFFF for c in text):
        return lambda i: i
    pref = [0]
    for c in text:
        pref.append(pref[-1] + (2 if ord(c) > 0xFFFF else 1))
    return lambda i: pref[i]
