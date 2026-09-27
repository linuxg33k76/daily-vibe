"""Export entries to PDF with QTextDocument + QPdfWriter (no WebEngine).

* print-friendly light styling regardless of the app theme
* plugin marker comments stripped (the plugin content itself is kept)
* images embedded; formats Qt can't read (e.g. WebP without the Qt imageformats
  plugin) are converted to PNG via Pillow first
* page numbers ("Page n") come from QTextDocument.print_()
"""
from __future__ import annotations

import datetime as dt
import html as html_lib
import re
import tempfile
from pathlib import Path

import markdown

from daily_vibe.storage import Journal

PRINT_CSS = """
body { font-family: 'DejaVu Serif', Georgia, serif; font-size: 11pt; color: #111; }
h1 { font-size: 20pt; color: #111; margin-bottom: 4pt; }
h2 { font-size: 14pt; color: #222; margin-top: 12pt; }
h3 { font-size: 12pt; color: #222; }
.entry-date { color: #666; font-size: 9pt; }
code { font-family: 'DejaVu Sans Mono', monospace; background-color: #f2f2f2; }
pre { font-family: 'DejaVu Sans Mono', monospace; background-color: #f5f5f5; }
blockquote { color: #444; margin-left: 12pt; font-style: italic; }
a { color: #1a4fa0; }
table { border-collapse: collapse; }
td, th { border: 1px solid #999; padding: 3px 6px; }
th { background-color: #e6e6e6; font-weight: bold; }
del { text-decoration: line-through; color: #555; }
.tag { color: #3a2f8f; background-color: #ebe8fb; font-size: 9pt; }
"""

_COMMENT_RE = re.compile(r"^[ \t]*<!--.*?-->[ \t]*\n?", re.S | re.M)
_IMG_RE = re.compile(r'<img([^>]*?)src="([^"]+)"([^>]*)>')
MAX_IMG_WIDTH = 560  # px at 96 dpi ~ 5.8 in, fits Letter/A4 with margins


def strip_markers(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _COMMENT_RE.sub("", text))


def _qt_can_read(path: Path) -> bool:
    from PySide6.QtGui import QImageReader
    reader = QImageReader(str(path))
    return reader.canRead()


class _ImageResolver:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="daily-vibe-export-")
        self.count = 0

    def resolve(self, path: Path) -> tuple[str, int] | None:
        from PIL import Image
        if not path.is_file():
            return None
        with Image.open(path) as im:
            width = im.width
            if _qt_can_read(path):
                return path.as_uri(), width
            out = Path(self._tmp.name) / f"img{self.count}.png"
            im.save(out, "PNG")
        self.count += 1
        return out.as_uri(), width

    def cleanup(self):
        self._tmp.cleanup()


def entry_html(journal: Journal, date: dt.date, resolver: _ImageResolver, page_break: bool,
               show_journal: bool = False) -> str:
    from daily_vibe.tags import tags_to_html_markdown
    text = tags_to_html_markdown(strip_markers(journal.read(date)), link=False)
    from daily_vibe import md_ext
    body = md_ext.stripe_rows(md_ext.fix_tables(markdown.markdown(md_ext.normalize_lists(text), extensions=md_ext.extensions())), "#f3f3f3")
    base = journal.entry_dir(date)

    def fix(m):
        before, src, after = m.group(1), html_lib.unescape(m.group(2)), m.group(3)
        if re.match(r"^[a-z]+://", src):
            return m.group(0)
        resolved = resolver.resolve((base / src).resolve())
        if resolved is None:
            return f"<p><i>[missing image: {html_lib.escape(src)}]</i></p>"
        uri, width = resolved
        return f'<img{before}src="{html_lib.escape(uri)}" width="{min(width, MAX_IMG_WIDTH)}"{after}>'

    body = _IMG_RE.sub(fix, body)
    style = ' style="page-break-before: always;"' if page_break else ""
    where = html_lib.escape(journal.name) if show_journal else "The Daily Vibe"
    header = f'<p class="entry-date"{style}>{date:%A, %B} {date.day}, {date:%Y} · {where}</p>'
    return header + body


def build_html(journal: Journal, dates: list[dt.date], one_per_page: bool, resolver: _ImageResolver,
               title: str | None = None) -> str:
    parts = []
    if title:
        parts.append(f"<h1>{html_lib.escape(title)}</h1>")
    for i, date in enumerate(dates):
        if not one_per_page and i > 0:
            parts.append("<hr/>")
        parts.append(entry_html(journal, date, resolver, page_break=one_per_page and (i > 0 or bool(title))))
    return f"<html><head><style>{PRINT_CSS}</style></head><body>{''.join(parts)}</body></html>"


def export_pdf(journal: Journal, dates: list[dt.date], out_path: Path, one_per_page: bool = True,
               page_size: str = "Letter", title: str | None = None) -> int:
    """Write the entries for `dates` (those that exist) to `out_path`.
    Returns the number of entries exported."""
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    dates = [d for d in sorted(dates) if journal.exists(d)]
    if not dates:
        raise ValueError("No entries in the selected range.")
    resolver = _ImageResolver()
    try:
        doc = QTextDocument()
        doc.setDefaultStyleSheet(PRINT_CSS)
        doc.setHtml(build_html(journal, dates, one_per_page, resolver, title))
        writer = QPdfWriter(str(out_path))
        size_id = QPageSize.PageSizeId.A4 if page_size.upper() == "A4" else QPageSize.PageSizeId.Letter
        writer.setPageSize(QPageSize(size_id))
        writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
        writer.setTitle(title or f"The Daily Vibe {dates[0]} – {dates[-1]}")
        writer.setCreator("The Daily Vibe")
        doc.print_(writer)  # paginates and adds page numbers
    finally:
        resolver.cleanup()
    return len(dates)


def export_pdf_pairs(pairs, out_path: Path, one_per_page: bool = True, page_size: str = "Letter",
                     title: str | None = None) -> int:
    """Like export_pdf but for (journal, date) pairs, possibly from several
    journals (used by the tag entries view). Keeps the given order; entries
    from more than one journal get the journal name in their header."""
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    pairs = [(j, d) for j, d in pairs if j.exists(d)]
    if not pairs:
        raise ValueError("No entries to export.")
    multi = len({str(j.root) for j, _d in pairs}) > 1
    resolver = _ImageResolver()
    try:
        parts = [f"<h1>{html_lib.escape(title)}</h1>"] if title else []
        for i, (journal, date) in enumerate(pairs):
            if not one_per_page and i > 0:
                parts.append("<hr/>")
            parts.append(entry_html(journal, date, resolver, one_per_page and (i > 0 or bool(title)), multi))
        doc = QTextDocument()
        doc.setDefaultStyleSheet(PRINT_CSS)
        doc.setHtml(f"<html><head><style>{PRINT_CSS}</style></head><body>{''.join(parts)}</body></html>")
        writer = QPdfWriter(str(out_path))
        size_id = QPageSize.PageSizeId.A4 if page_size.upper() == "A4" else QPageSize.PageSizeId.Letter
        writer.setPageSize(QPageSize(size_id))
        writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
        writer.setTitle(title or "The Daily Vibe")
        writer.setCreator("The Daily Vibe")
        doc.print_(writer)
    finally:
        resolver.cleanup()
    return len(pairs)
