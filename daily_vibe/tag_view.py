"""Data for the tag entries view: every entry carrying a tag, as display cards."""
from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass, field

from daily_vibe import tags as tagmod

_PLUGIN_BLOCK_RE = re.compile(r"<!--\s*plugin:([\w.-]+)\s*-->.*?<!--\s*/plugin:\1\s*-->", re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


@dataclass
class TagEntry:
    journal: object
    date: dt.date
    title: str
    excerpt_html: str           # escaped, the tag wrapped in <b class="hit">
    tags: list[str] = field(default_factory=list)   # all tags of the entry


def _plain(line: str) -> str:
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
    line = re.sub(r"^\s*(>\s?)+", "", line)
    line = re.sub(r"^\s*([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?", "", line)
    return re.sub(r"(\*\*|__|~~|`|(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w))", "", line).strip()


def tag_matches(entry_tags, tag: str, nested: bool) -> bool:
    if nested:
        return tagmod.matches(entry_tags, [tag])
    key = tagmod.normalize(tag)
    return any(tagmod.normalize(t) == key for t in entry_tags)


def _hit_keys(tag: str, nested: bool):
    key = tagmod.normalize(tag)
    return lambda t: tagmod.normalize(t) == key or (nested and tagmod.normalize(t).startswith(key + "/"))


def make_card(journal, date: dt.date, text: str, tag: str, nested: bool, entry_tags: list[str],
              excerpt_len: int = 220) -> TagEntry:
    body = tagmod.split_front_matter(text)[1]
    body = _COMMENT_RE.sub("", _PLUGIN_BLOCK_RE.sub("", body))
    lines = [l for l in body.splitlines() if l.strip() and not l.strip().startswith(("```", "~~~", "|", "!["))]
    title = ""
    for l in lines:
        if re.match(r"^\s{0,3}#{1,6}\s", l):
            title = _plain(l)
            break
    if not title and lines:
        title = _plain(lines[0])[:80]
    is_hit = _hit_keys(tag, nested)
    # excerpt: the first line mentioning the tag inline, else the first body line
    chosen = None
    for l in lines:
        if any(is_hit(t) for _s, _e, t in tagmod.find_inline_tags(l)) and not re.match(r"^\s{0,3}#{1,6}\s", l):
            chosen = l
            break
    if chosen is None:
        chosen = next((l for l in lines if not re.match(r"^\s{0,3}#{1,6}\s", l)), "")
    plain = _plain(chosen)
    if len(plain) > excerpt_len:
        plain = plain[:excerpt_len].rsplit(" ", 1)[0] + "…"
    parts, pos = [], 0
    for s, e, t in tagmod.find_inline_tags(plain):
        parts.append(html.escape(plain[pos:s]))
        seg = html.escape(plain[s:e])
        parts.append(f'<b class="hit">{seg}</b>' if is_hit(t) else f'<span class="tagtxt">{seg}</span>')
        pos = e
    parts.append(html.escape(plain[pos:]))
    return TagEntry(journal, date, title or "(untitled)", "".join(parts), list(entry_tags))


def collect(journals, tag_index: tagmod.TagIndex, tag: str, nested: bool = True,
            newest_first: bool = True) -> list[TagEntry]:
    out = []
    for journal in journals:
        for date in journal.list_dates():
            entry_tags = tag_index.tags_for(journal, date)
            if tag_matches(entry_tags, tag, nested):
                out.append(make_card(journal, date, journal.read(date), tag, nested, entry_tags))
    out.sort(key=lambda c: (c.date, c.journal.name.lower()), reverse=newest_first)
    return out


def cards_html(cards: list[TagEntry], tag: str, nested: bool, show_journal: bool, theme) -> str:
    c = theme.colors
    from daily_vibe.theming import mix
    css = f"""
    body {{ background:{c['surface']}; color:{c['text']}; font-size:14px; }}
    table.card {{ margin: 0 0 10px 0; background:{c['alt_surface']}; border:1px solid {c['border']}; }}
    .date {{ color:{c['muted']}; font-size:12px; }}
    .journal {{ color:{c['accent']}; font-size:12px; font-weight:bold; }}
    a.title {{ color:{c['heading']}; font-size:16px; font-weight:bold; text-decoration:none; }}
    .excerpt {{ color:{c['text']}; }}
    b.hit {{ background:{theme.hl('tag')}; color:{theme.accent_text}; }}
    .tagtxt {{ color:{theme.hl('tag')}; }}
    a.tag {{ background:{mix(c['surface'], c['accent'], 0.18)}; color:{c['text']}; text-decoration:none; font-size:12px; }}
    a.tag.self {{ background:{theme.hl('tag')}; color:{theme.accent_text}; }}
    .empty {{ color:{c['muted']}; }}
    """
    if not cards:
        body = f'<p class="empty">No entries tagged #{html.escape(tag)}.</p>'
    else:
        is_hit = _hit_keys(tag, nested)
        rows = []
        for card in cards:
            href = f"entry:{html.escape(card.journal.root.name)}/{card.date.isoformat()}"
            chips = " ".join(
                tagmod.chip_html(t).replace('class="tag"', 'class="tag self"') if is_hit(t) else tagmod.chip_html(t)
                for t in card.tags)
            journal = f' · <span class="journal">{html.escape(card.journal.name)}</span>' if show_journal else ""
            rows.append(
                f'<table class="card" width="100%" cellpadding="8"><tr><td>'
                f'<span class="date">{card.date:%a %d %b %Y}</span>{journal}<br/>'
                f'<a class="title" href="{href}">{html.escape(card.title)}</a><br/>'
                f'<span class="excerpt">{card.excerpt_html}</span><br/>{chips}'
                f'</td></tr></table>')
        body = "".join(rows)
    return f"<html><head><style>{css}</style></head><body>{body}</body></html>"
