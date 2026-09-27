"""Tags: inline #tags and YAML front matter.

Inline tag rule:
  * `#` followed by a letter, then letters/digits/`_`/`-`, optionally nested with `/`
    (`#work`, `#health/running`, `#deep-work`, `#2026goals` is NOT a tag: must start
    with a letter).
  * the `#` must not directly follow a letter/digit/`/`/`#`/`&` (so `C#`, `a#b`,
    `&#123;` and URL fragments are not tags).
  * ignored inside fenced code blocks, inline code spans, URLs / Markdown link targets,
    HTML comments and plugin blocks (generated content).
  * Markdown headings (`# Title`) are never tags because `#` is followed by a space.
  * hex colors are not tags: a 3- or 6-character hex token that contains a digit
    (`#1e1e2e`, `#0af`) or is one repeated character (`#fff`, `#000`) is skipped.
    Letter-only words like `#cafe` or `#bad` are still tags.

Front matter (first thing in the file):
    ---
    tags: [travel, family]      # or: tags: travel, family
    ---                         # or a list:  tags:\\n  - travel\\n  - family

Grouping is case-insensitive (`#Work` == `#work`); the most common spelling is
displayed. Filtering by `a` also matches nested tags `a/...`.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from collections import Counter
from pathlib import Path

TAG_RE = re.compile(r"(?<![\w/#&])#([A-Za-z][\w-]*(?:/[\w-]+)*)")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.S | re.M)
_UNCLOSED_FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*\Z", re.S | re.M)
_CODE_SPAN_RE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
_URL_RE = re.compile(r"(?:https?|ftp|file)://\S+|www\.\S+|\]\([^)]*\)|<[^>\s]+>")
_COMMENT_BLOCK_RE = re.compile(r"<!--\s*plugin:([\w.-]+)\s*-->.*?<!--\s*/plugin:\1\s*-->|<!--.*?-->", re.S)
_HEX_RE = re.compile(r"^(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
FRONT_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S)


TAG_FULL_RE = re.compile(r"^[A-Za-z][\w-]*(?:/[\w-]+)*$")


def is_valid_tag(tag: str) -> bool:
    """Would `#tag` be recognized as a tag (same rules as the parser)?"""
    tag = tag.strip().lstrip("#")
    return bool(TAG_FULL_RE.match(tag)) and not tag.endswith(("-", "/")) and not _is_hex_color(tag)


def normalize(tag: str) -> str:
    return tag.strip().strip("#").strip("/").lower()


def _is_hex_color(token: str) -> bool:
    return bool(_HEX_RE.match(token)) and (any(c.isdigit() for c in token) or len(set(token.lower())) == 1)


# Front matter ---------------------------------------------------------------------
def split_front_matter(text: str) -> tuple[str, str]:
    """(front matter source without fences, rest of the text)."""
    m = FRONT_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def _clean_item(s: str) -> str:
    return s.strip().strip("'\"").strip().lstrip("#")


def front_matter_tags(text: str) -> list[str]:
    fm, _ = split_front_matter(text)
    if not fm:
        return []
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(tags|tag|keywords)\s*:\s*(.*)$", line.strip(), re.I)
        if not m:
            continue
        value = m.group(2).strip()
        if value.startswith("["):
            return [t for t in (_clean_item(x) for x in value.strip("[]").split(",")) if t]
        if value:
            return [t for t in (_clean_item(x) for x in re.split(r"[,\s]+", value)) if t]
        items = []
        for nxt in lines[i + 1:]:
            lm = re.match(r"^\s*-\s*(.+)$", nxt)
            if not lm:
                break
            items.append(_clean_item(lm.group(1)))
        return [t for t in items if t]
    return []


# Inline tags ----------------------------------------------------------------------
def _mask(text: str) -> str:
    """Replace ignored regions with spaces (same length, so offsets stay valid)."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    fm = FRONT_RE.match(text)
    if fm:
        text = blank(fm) + text[fm.end():]
    for rx in (_COMMENT_BLOCK_RE, _FENCE_RE, _UNCLOSED_FENCE_RE, _CODE_SPAN_RE, _URL_RE):
        text = rx.sub(blank, text)
    return text


def find_inline_tags(text: str) -> list[tuple[int, int, str]]:
    """[(start, end, tag_without_hash)] for inline tags; offsets index into `text`."""
    masked = _mask(text)
    out = []
    for m in TAG_RE.finditer(masked):
        tag = m.group(1).rstrip("-/")
        if _is_hex_color(tag):
            continue
        out.append((m.start(), m.start() + 1 + len(tag), tag))
    return out


def extract_tags(text: str) -> list[str]:
    """All tags (original spelling, de-duplicated case-insensitively, in order)."""
    seen, out = set(), []
    for tag in front_matter_tags(text) + [t for _s, _e, t in find_inline_tags(text)]:
        key = normalize(tag)
        if key and key not in seen:
            seen.add(key)
            out.append(tag)
    return out


def matches(entry_tags, wanted) -> bool:
    """AND semantics; `a` matches `a` and `a/b`."""
    keys = {normalize(t) for t in entry_tags}
    for w in wanted:
        w = normalize(w)
        if not any(k == w or k.startswith(w + "/") for k in keys):
            return False
    return True


# Index -------------------------------------------------------------------------------
class TagIndex:
    """Per-file tag cache keyed by (mtime_ns, size)."""

    def __init__(self):
        self._cache: dict[Path, tuple[tuple[int, int], list[str]]] = {}

    def tags_for_path(self, path: Path) -> list[str]:
        try:
            st = path.stat()
        except OSError:
            return []
        key = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
        tags = extract_tags(path.read_text(encoding="utf-8", errors="replace"))
        self._cache[path] = (key, tags)
        return tags

    def tags_for(self, journal, date: dt.date) -> list[str]:
        return self.tags_for_path(journal.entry_path(date))

    def counts(self, journals) -> dict[str, tuple[str, int]]:
        """normalized tag -> (display spelling, number of entries)."""
        spellings: dict[str, Counter] = {}
        counts: Counter = Counter()
        for journal in journals:
            for date in journal.list_dates():
                for tag in self.tags_for(journal, date):
                    key = normalize(tag)
                    counts[key] += 1
                    spellings.setdefault(key, Counter())[tag] += 1
        return {k: (spellings[k].most_common(1)[0][0], n) for k, n in counts.items()}

    def entries_with(self, journals, wanted) -> list[tuple[object, dt.date]]:
        out = []
        for journal in journals:
            for date in journal.list_dates():
                if matches(self.tags_for(journal, date), wanted):
                    out.append((journal, date))
        out.sort(key=lambda jd: jd[1], reverse=True)
        return out


# Rendering ----------------------------------------------------------------------------
def chip_html(tag: str, link: bool = True) -> str:
    import html as _html
    label = _html.escape(tag)
    if link:
        href = "tag:" + urllib.parse.quote(tag, safe="/-_")
        return f'<a class="tag" href="{href}">&nbsp;#{label}&nbsp;</a>'
    return f'<span class="tag">&nbsp;#{label}&nbsp;</span>'


def tags_to_html_markdown(text: str, link: bool = True) -> str:
    """Prepare entry Markdown for display: drop the YAML front matter (its tags
    become a chip row at the top) and turn inline #tags into chips (links with
    the ``tag:`` scheme when `link`, else plain spans for PDF)."""
    fm_tags = front_matter_tags(text)
    spans = find_inline_tags(text)
    out = text
    for start, end, tag in reversed(spans):
        out = out[:start] + chip_html(tag, link) + out[end:]
    _fm, body = split_front_matter(out)
    if fm_tags:
        body = '<p class="fm-tags">' + " ".join(chip_html(t, link) for t in fm_tags) + "</p>\n\n" + body
    return body
