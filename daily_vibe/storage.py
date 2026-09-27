"""Plain-file journal storage.

A journal is one self-contained folder:
    <journal>/YYYY/MM/YYYY-MM-DD.md
    <journal>/assets/YYYY/MM/*.webp        (images, linked relatively: ../../assets/YYYY/MM/x.webp)
    <journal>/.dailyvibe.toml              (optional metadata, see journal_meta.py)
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

ENTRY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


@dataclass
class SearchHit:
    date: dt.date
    line_no: int
    snippet: str
    journal: str = ""  # journal name (set for library-wide searches)


class Journal:
    def __init__(self, root: Path | str, name: str | None = None):
        self.root = Path(root).expanduser()
        self._name = name

    @property
    def name(self) -> str:
        return self._name or self.meta.get("name") or self.root.name

    @property
    def meta(self) -> dict:
        from daily_vibe.journal_meta import load_meta
        return load_meta(self.root)

    @property
    def overrides(self) -> dict:
        o = self.meta.get("overrides")
        return o if isinstance(o, dict) else {}

    def template(self, default: str) -> str:
        t = self.overrides.get("template")
        return t if isinstance(t, str) and t.strip() else default

    def plugin_settings(self, plugin_id: str) -> dict:
        """Per-journal plugin setting overrides ([overrides.plugin_settings.<id>])."""
        ps = self.overrides.get("plugin_settings")
        section = ps.get(plugin_id) if isinstance(ps, dict) else None
        return dict(section) if isinstance(section, dict) else {}

    def plugin_ids(self, default: list[str]) -> list[str]:
        p = self.overrides.get("plugins")
        return [str(x) for x in p] if isinstance(p, list) else list(default)

    # Paths -----------------------------------------------------------------
    def entry_path(self, date: dt.date) -> Path:
        return self.root / f"{date:%Y}" / f"{date:%m}" / f"{date:%Y-%m-%d}.md"

    def entry_dir(self, date: dt.date) -> Path:
        return self.entry_path(date).parent

    def assets_dir(self, date: dt.date) -> Path:
        return self.root / "assets" / f"{date:%Y}" / f"{date:%m}"

    # CRUD ------------------------------------------------------------------
    def exists(self, date: dt.date) -> bool:
        return self.entry_path(date).is_file()

    def read(self, date: dt.date) -> str:
        path = self.entry_path(date)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def write(self, date: dt.date, text: str) -> Path:
        path = self.entry_path(date)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)  # atomic-ish save
        return path

    def list_dates(self) -> list[dt.date]:
        """All dates that have an entry, newest first."""
        dates = []
        if not self.root.is_dir():
            return dates
        for path in self.root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"):
            m = ENTRY_RE.match(path.name)
            if not m:
                continue
            try:
                dates.append(dt.date(int(m[1]), int(m[2]), int(m[3])))
            except ValueError:
                continue
        return sorted(dates, reverse=True)

    def summary(self, date: dt.date, length: int = 60) -> str:
        """First meaningful non-heading line, for the timeline list."""
        from daily_vibe.tags import split_front_matter
        text = re.sub(r"<!--\s*plugin:([\w.-]+)\s*-->.*?<!--\s*/plugin:\1\s*-->", "",
                      split_front_matter(self.read(date))[1], flags=re.S)
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith(("#", "<!--", "![", "|", ">")):
                continue
            return re.sub(r"[*_`]", "", s)[:length]
        return ""

    # Search ----------------------------------------------------------------
    def search(self, query: str, limit: int = 200, tag_index=None) -> list[SearchHit]:
        """Case-insensitive search. Whitespace-separated terms must all appear in
        the entry; `tag:foo` terms require the tag (nested tags match their parent).
        Returns one hit per matching entry (the first matching line)."""
        from daily_vibe import tags as tagmod
        tag_terms = [t[4:] for t in query.split() if t.lower().startswith("tag:") and len(t) > 4]
        terms = [t.lower() for t in query.split() if t.strip() and not t.lower().startswith("tag:")]
        if not terms and not tag_terms:
            return []
        hits: list[SearchHit] = []
        for date in self.list_dates():
            if tag_terms:
                entry_tags = (tag_index.tags_for(self, date) if tag_index is not None
                              else tagmod.extract_tags(self.read(date)))
                if not tagmod.matches(entry_tags, tag_terms):
                    continue
            text = self.read(date)
            lower = text.lower()
            if not all(t in lower for t in terms):
                continue
            snippet, line_no = "", 1
            probe = terms or ["#" + t.lower() for t in tag_terms]
            for i, line in enumerate(text.splitlines(), 1):
                if any(t in line.lower() for t in probe):
                    snippet, line_no = line.strip()[:120], i
                    break
            if not snippet:
                snippet = self.summary(date, 120)
            hits.append(SearchHit(date, line_no, snippet, self.name))
            if len(hits) >= limit:
                break
        return hits


def render_template(template: str, date: dt.date, plugins_md: str = "") -> str:
    values = {
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "long_date": f"{date:%B} {date.day}, {date:%Y}",
        "plugins": plugins_md.strip(),
    }
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    if "{plugins}" not in template and plugins_md.strip():
        # No placeholder: put plugin blocks right after the first heading.
        from daily_vibe.markers import insert_after_heading
        text = insert_after_heading(text, plugins_md.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text
