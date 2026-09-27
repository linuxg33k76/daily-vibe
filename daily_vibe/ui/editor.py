"""Markdown editor: pasted/dropped images, #tag autocomplete and Live Preview.

Live Preview design (see also daily_vibe/live_highlighter.py): the widget stays
a QPlainTextEdit holding the exact Markdown text. A LiveHighlighter styles the
text in place and hides syntax markers on lines without the cursor; this class
tracks the "active" blocks (cursor line + selection), re-highlights only the
blocks that entered/left that set, paints bullets / checkboxes / rules / tag
chips / inline images over inactive blocks in paintEvent, and draws
full-width bands for code, quotes, plugin blocks and front matter as extra
selections. QPlainTextEdit was chosen over QTextEdit+QTextImageFormat because
it never needs a second "display" document: undo, autosave, find, paste and
autocomplete all keep operating on the one plain-text document, and its
block-based layout stays fast on long entries.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from PySide6.QtCore import (QBuffer, QByteArray, QIODevice, QMimeData, QPoint, QPointF, QRectF,
                            QStringListModel, Qt, QTimer, Signal)
from PySide6.QtGui import (QAction, QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QImageReader, QPainter, QPainterPath,
                           QPen, QPixmap, QTextCursor, QTextFormat)
from PySide6.QtWidgets import QApplication, QCompleter, QMenu, QPlainTextEdit, QTextEdit

from daily_vibe.images import is_image_file
from daily_vibe import md_tables
from daily_vibe.live_highlighter import (AUTOLINK_RE, IMAGE_RE, INDENT_PX, LINK_RE, LIST_ITEM_RE, TABLE, URL_RE,
                                         LiveHighlighter, U16, block_data)
from daily_vibe.tags import find_inline_tags

# Called with either a file Path or raw PNG bytes; returns markdown to insert
ImageHandler = Callable[[Path | bytes], str | None]

# "#" at a word start followed by an (optionally empty) tag prefix, right before the cursor
_TAG_PREFIX_RE = re.compile(r"(?:^|(?<=[^\w/#&`]))#([A-Za-z][\w/-]*)?$")


IMAGE_MAX_HEIGHT = 360
INLINE_IMAGE_MAX_HEIGHT = 160
INLINE_IMAGE_MAX_WIDTH = 420
_PIX_CACHE_MAX = 64


class MarkdownEditor(QPlainTextEdit):
    tag_activated = Signal(str, QPoint)      # Ctrl+click on a tag (global position for a menu)
    tag_entries_requested = Signal(str)      # context menu "Show all entries tagged #x"
    tag_rename_requested = Signal(str)
    link_activated = Signal(str)             # Ctrl+click on a link / image target

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_handler: ImageHandler | None = None
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(max(font.pointSize(), 11))
        self.mono_font = QFont(font)
        live = QFont(QApplication.font())
        live.setPointSize(font.pointSize())
        self.live_font = live
        self.setFont(font)
        # Live Preview state
        self.live = False
        self.theme = None
        self.source_hl = None
        self.live_hl: LiveHighlighter | None = None
        self.active_blocks: set[int] = set()
        self.base_dir: Path | None = None
        self._pix_cache: dict = {}
        self._band_timer = QTimer(self, singleShot=True, interval=120, timeout=self.update_bands)
        self._resize_timer = QTimer(self, singleShot=True, interval=180, timeout=self._relayout_images)
        self._last_width = 0
        # >0 while only formatting changes (highlighter attach/detach emits
        # contentsChange without any text edit); owners ignore changes then.
        self.formatting = 0
        # Live Preview typography (Preferences → Appearance → Live Preview)
        self.line_spacing = 1.0
        self.heading_family = ""
        self.code_family = ""
        self.max_text_width = 0      # 0 = use the full width; else a centered column
        self._band_sels: list = []
        self._find_sels: list = []
        self.cursorPositionChanged.connect(self._update_active)
        self.selectionChanged.connect(self._update_active)
        self.document().contentsChange.connect(self._on_contents_change)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setPlaceholderText("No entry for this day yet. Start typing, or use Entry → Create Entry (with template).")
        self.tag_model = QStringListModel(self)
        self.completer = QCompleter(self.tag_model, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setMaxVisibleItems(8)
        self.completer.activated.connect(self.insert_completion)

    # Highlighting / view mode ---------------------------------------------------------
    def setup_highlighting(self, theme, source_highlighter_cls, live: bool = False) -> None:
        """Create both highlighters (only one is attached to the document at a time)."""
        self.theme = theme
        self.source_hl = source_highlighter_cls(self, theme)
        self.live_hl = LiveHighlighter(self, theme, self)  # QObject-parent ctor: not attached yet
        self.live = not live  # force set_live to do the work
        self.set_live(live)

    @contextmanager
    def formatting_only(self):
        self.formatting += 1
        try:
            yield
        finally:
            self.formatting -= 1

    def set_fonts(self, mono: QFont, live: QFont) -> None:
        self.mono_font, self.live_font = QFont(mono), QFont(live)
        self._apply_font()
        if self.live_hl is not None:
            self.live_hl.set_theme(self.theme, rehighlight=False)
        self.guarded_rehighlight()

    def _apply_font(self) -> None:
        self.setFont(self.live_font if self.live else self.mono_font)
        if self.live:
            self.setTabStopDistance(INDENT_PX)     # a tab = one list nesting level
        else:
            self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self._update_margins()

    def mono_families(self) -> list[str]:
        return [self.code_family or self.mono_font.family(), "monospace"]

    def heading_families(self) -> list[str]:
        return [self.heading_family] if self.heading_family else []

    def set_live_style(self, heading_family: str = "", code_family: str = "", line_spacing: float = 1.0,
                       max_text_width: int = 0, refresh: bool = True) -> None:
        """Live Preview typography; formatting only (never touches the text)."""
        self.heading_family, self.code_family = heading_family or "", code_family or ""
        self.line_spacing = max(1.0, min(2.5, float(line_spacing or 1.0)))
        self.max_text_width = max(0, int(max_text_width or 0))
        if not refresh:
            return
        if self.live_hl is not None:
            self.live_hl.set_theme(self.theme, rehighlight=False)
        self._update_margins()
        self.guarded_rehighlight()
        self.update_bands()

    def _update_margins(self) -> None:
        m = 0
        if self.live and self.max_text_width > 0:
            avail = self.width() - 2 * self.frameWidth() - self.verticalScrollBar().sizeHint().width()
            if avail > self.max_text_width + 40:
                m = (avail - self.max_text_width) // 2
        if self.viewportMargins().left() != m:
            self.setViewportMargins(m, 0, m, 0)

    def set_theme(self, theme) -> None:
        self.theme = theme
        if self.source_hl is not None:
            with self.formatting_only():
                self.source_hl.set_theme(theme)   # (re)highlights only when attached
        if self.live_hl is not None:
            self.live_hl.set_theme(theme, rehighlight=False)
        self.guarded_rehighlight()
        self.update_bands()

    def current_highlighter(self):
        return self.live_hl if self.live else self.source_hl

    def guarded_rehighlight(self) -> None:
        """Re-run highlighting. Formatting-only: emits no contentsChange (so no dirty flag)."""
        hl = self.current_highlighter()
        if hl is not None and hl.document() is self.document():
            with self.formatting_only():
                hl.rehighlight()
        self.viewport().update()

    def set_live(self, on: bool) -> None:
        on = bool(on)
        if on == self.live or self.source_hl is None:
            return
        bar = self.verticalScrollBar().value()
        self.live = on
        self._find_sels = []
        old, new = (self.source_hl, self.live_hl) if on else (self.live_hl, self.source_hl)
        with self.formatting_only():
            old.setDocument(None)
        self.active_blocks = set()
        self._apply_font()
        if on:
            self.live_hl.set_theme(self.theme, rehighlight=False)
            self._compute_active()
        with self.formatting_only():
            new.setDocument(self.document())
        self.guarded_rehighlight()
        self.update_bands()
        self.verticalScrollBar().setValue(bar)

    # Active blocks -------------------------------------------------------------------
    def _compute_active(self) -> set[int]:
        c = self.textCursor()
        doc = self.document()
        first = doc.findBlock(c.selectionStart())
        last = doc.findBlock(c.selectionEnd())
        # a cursor inside a table reveals the whole table as source
        while first.isValid() and first.userState() > 0 and first.userState() & TABLE:
            prev = first.previous()
            if not (prev.isValid() and prev.userState() > 0 and prev.userState() & TABLE):
                break
            first = prev
        if last.isValid() and last.userState() > 0 and last.userState() & TABLE:
            nxt = last.next()
            while nxt.isValid() and nxt.userState() > 0 and nxt.userState() & TABLE and \
                    block_data(nxt) is not None and block_data(nxt).table != "head":
                last = nxt
                nxt = nxt.next()
        b1, b2 = first.blockNumber(), last.blockNumber()
        self.active_blocks = set(range(max(0, b1), max(0, b2) + 1))
        return self.active_blocks

    def _update_active(self) -> None:
        if not self.live or self.live_hl is None:
            return
        old = self.active_blocks
        new = set(self.active_blocks)
        self._compute_active()
        new = self.active_blocks
        changed = old ^ new
        if not changed:
            return
        doc = self.document()
        with self.formatting_only():
            for n in sorted(changed):
                block = doc.findBlockByNumber(n)
                if block.isValid():
                    self.live_hl.rehighlightBlock(block)
        self.viewport().update()

    def _on_contents_change(self, pos: int, removed: int, added: int) -> None:
        if (removed or added) and self.live and not self.formatting:
            self._band_timer.start()
            QTimer.singleShot(0, lambda p=pos: self._rehighlight_neighbors(p))

    def _rehighlight_neighbors(self, pos: int) -> None:
        """Context the highlighter can't see by itself: a table header depends on
        the next line (the delimiter row); a list item's nesting level depends on
        earlier lines, so later items of the same list are refreshed."""
        if not self.live or self.live_hl is None or self.live_hl.document() is not self.document():
            return
        block = self.document().findBlock(min(pos, max(0, self.document().characterCount() - 1)))
        if not block.isValid():
            return
        with self.formatting_only():
            prev = block.previous()
            if prev.isValid() and ("|" in prev.text() or "|" in block.text()):
                self.live_hl.rehighlightBlock(prev)
            if LIST_ITEM_RE.match(block.text().lstrip(">").lstrip()) or block.text()[:1] in (" ", "\t"):
                nxt, n = block.next(), 0
                while nxt.isValid() and n < 100 and nxt.text().strip() and \
                        (LIST_ITEM_RE.match(nxt.text()) or nxt.text()[:1] in (" ", "\t")):
                    self.live_hl.rehighlightBlock(nxt)
                    nxt, n = nxt.next(), n + 1
        self._compute_active()
        self.viewport().update()

    # Full-width bands ------------------------------------------------------------------
    def band_colors(self) -> dict[str, QColor]:
        from daily_vibe.theming import mix
        c = self.theme.colors
        return {
            "code": QColor(self.theme.hl("code_bg")),
            "quote": QColor(mix(c["surface"], c["quote_bg"], 0.7)),
            "plugin": QColor(mix(c["surface"], c["accent"], 0.07)),
            "front": QColor(c["alt_surface"]),
        }

    def update_bands(self) -> None:
        sels = []
        if self.live and self.theme is not None:
            colors = self.band_colors()
            block = self.document().begin()
            run_kind, run_start, run_end = "", None, None

            def flush():
                if run_kind and run_start is not None:
                    sel = QTextEdit.ExtraSelection()
                    cur = QTextCursor(self.document())
                    cur.setPosition(run_start)
                    cur.setPosition(run_end, QTextCursor.MoveMode.KeepAnchor)
                    sel.cursor = cur
                    sel.format.setBackground(colors[run_kind])
                    sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                    sels.append(sel)

            while block.isValid():
                d = block_data(block)
                kind = d.kind if d else ""
                if kind != run_kind:
                    flush()
                    run_kind, run_start = kind, block.position()
                # include the newline: FullWidthSelection only extends lines whose
                # line break is selected
                run_end = min(block.position() + block.length(), self.document().characterCount() - 1)
                block = block.next()
            flush()
        self._band_sels = sels
        self._apply_extra_selections()

    def set_find_selections(self, sels: list) -> None:
        """Find-bar match highlights (kept on top of the Live Preview bands)."""
        self._find_sels = list(sels)
        self._apply_extra_selections()

    def _apply_extra_selections(self) -> None:
        self.setExtraSelections(self._band_sels + self._find_sels)
        self.viewport().update()

    # Images ---------------------------------------------------------------------------
    def resolve_image(self, target: str) -> Path | None:
        target = target.strip().strip("<>")
        if not target or re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I) and not target.startswith("file://"):
            return None
        if target.startswith("file://"):
            target = target[7:]
        path = Path(unquote(target))
        if not path.is_absolute():
            if self.base_dir is None:
                return None
            path = self.base_dir / path
        return path

    def image_max_width(self) -> int:
        return max(120, self.viewport().width() - int(2 * self.document().documentMargin()) - 24)

    def image_pixmap(self, target: str, max_w: int | None = None, max_h: int = IMAGE_MAX_HEIGHT) -> QPixmap | None:
        path = self.resolve_image(target)
        if path is None:
            return None
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return None
        maxw = max_w or self.image_max_width()
        key = (str(path), mtime, maxw, max_h)
        if key in self._pix_cache:
            return self._pix_cache[key]
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            image = _pillow_qimage(path)
        pix = None
        if image is not None and not image.isNull():
            w, h = image.width(), image.height()
            scale = min(1.0, maxw / max(1, w), max_h / max(1, h))
            if scale < 1.0:
                image = image.scaled(max(1, int(w * scale)), max(1, int(h * scale)),
                                     Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pix = QPixmap.fromImage(image)
        if len(self._pix_cache) > _PIX_CACHE_MAX:
            self._pix_cache.clear()
        self._pix_cache[key] = pix
        return pix

    def image_size_for(self, target: str) -> tuple[int, int] | None:
        pix = self.image_pixmap(target)
        return (pix.width(), pix.height()) if pix is not None else None

    def inline_image_limits(self) -> tuple[int, int]:
        return min(INLINE_IMAGE_MAX_WIDTH, max(60, self.image_max_width() // 2)), INLINE_IMAGE_MAX_HEIGHT

    def inline_image_size(self, target: str) -> tuple[int, int] | None:
        w, h = self.inline_image_limits()
        pix = self.image_pixmap(target, w, h)
        return (pix.width(), pix.height()) if pix is not None else None

    def _relayout_images(self) -> None:
        if not self.live or self.live_hl is None:
            return
        block = self.document().begin()
        while block.isValid():
            d = block_data(block)
            if d is not None and (d.image or d.inline_images or d.table):
                with self.formatting_only():
                    self.live_hl.rehighlightBlock(block)
            block = block.next()
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_margins()
        w = self.viewport().width()
        if self.live and abs(w - self._last_width) > 12:
            self._resize_timer.start()
        self._last_width = w

    # Painting -------------------------------------------------------------------------
    def _char_rect(self, block, offset: int):
        c = QTextCursor(block)
        c.setPosition(block.position() + min(offset, max(0, block.length() - 1)))
        return self.cursorRect(c)

    def _task_box(self, block, offset: int) -> QRectF:
        r1 = self._char_rect(block, offset)
        r2 = self._char_rect(block, offset + 3)
        side = max(10.0, min(float(r1.height()) - 5, 16.0))
        width = r2.left() - r1.left() if r2.top() == r1.top() else side + 4
        x = r1.left() + max(0.0, (width - side) / 2)
        return QRectF(x, r1.center().y() - side / 2 + 0.5, side, side)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.live or self.theme is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        offset = self.contentOffset()
        height = self.viewport().height()
        block = self.firstVisibleBlock()
        tables: dict[int, dict] = {}
        while block.isValid():
            geo = self.blockBoundingGeometry(block).translated(offset)
            if geo.top() > height:
                break
            if block.isVisible() and geo.bottom() >= 0:
                d = block_data(block)
                if d is not None and d.guides:
                    self._paint_guides(painter, block, geo, d)
                if d is not None and block.blockNumber() not in self.active_blocks:
                    if d.table:
                        self._paint_table_row(painter, block, geo, d, tables)
                    else:
                        self._paint_block(painter, block, geo, d)
            block = block.next()
        painter.end()

    # Lists ------------------------------------------------------------------------------
    def _paint_guides(self, p: QPainter, block, geo: QRectF, d) -> None:
        pen_color = QColor(self.theme.colors["border"])
        p.setPen(QPen(pen_color, 1))
        for off in d.guides:
            r = self._char_rect(block, off)
            x = r.left() + 3.5
            p.drawLine(QPointF(x, geo.top()), QPointF(x, geo.bottom()))

    # Tables -----------------------------------------------------------------------------
    def table_layout(self, block) -> dict | None:
        """Column widths / alignments / row kinds of the table containing `block`."""
        head = block
        n = 0
        while head.isValid() and n < 5000:
            d = block_data(head)
            if d is None or not d.table:
                return None
            if d.table == "head":
                break
            head = head.previous()
            n += 1
        if not head.isValid():
            return None
        rows, aligns, b = [], [], head
        while b.isValid():
            d = block_data(b)
            if d is None or not d.table or (d.table == "head" and b != head):
                break
            if d.table == "delim":
                aligns = md_tables.alignments(b.text())
            rows.append((b.blockNumber(), d.table, d.cells, d.cell_styles))
            b = b.next()
        ncols = max((len(c) for _n, k, c, _s in rows if k != "delim"), default=0)
        if not ncols:
            return None
        body_font = QFont(self.font())
        head_font = QFont(self.font())
        head_font.setBold(True)
        pad = 10.0
        widths = [40.0] * ncols
        for _n, kind, cells, styles in rows:
            if kind == "delim":
                continue
            base = head_font if kind == "head" else body_font
            for i, c in enumerate(cells[:ncols]):
                f = self._cell_font(base, styles[i] if i < len(styles) else frozenset())
                widths[i] = max(widths[i], QFontMetricsF(f).horizontalAdvance(c) + 2 * pad)
        avail = float(self.viewport().width() - 2 * self.document().documentMargin() - 12)
        total = sum(widths)
        if total > avail > 0:
            widths = [max(36.0, w * avail / total) for w in widths]
        body_index, kinds = 0, {}
        for num, kind, _cells, _styles in rows:
            if kind == "body":
                kinds[num] = ("body", body_index)
                body_index += 1
            else:
                kinds[num] = (kind, 0)
        return {"head": head.blockNumber(), "widths": widths, "aligns": aligns + [""] * (ncols - len(aligns)),
                "ncols": ncols, "kinds": kinds, "pad": pad, "fonts": (head_font, body_font)}

    def _cell_font(self, base: QFont, styles) -> QFont:
        if not styles:
            return base
        f = QFont(base)
        if "code" in styles:
            f = QFont(self.mono_families()[0] if self.mono_families() else self.mono_font.family())
            f.setPointSizeF(base.pointSizeF() * 0.92)
            f.setBold(base.bold())
        if "bold" in styles:
            f.setBold(True)
        if "italic" in styles:
            f.setItalic(True)
        if "strike" in styles:
            f.setStrikeOut(True)
        return f

    def _paint_table_row(self, p: QPainter, block, geo: QRectF, d, cache: dict) -> None:
        layout = None
        for lay in cache.values():
            if block.blockNumber() in lay["kinds"]:
                layout = lay
        if layout is None:
            layout = self.table_layout(block)
            if layout is None:
                return
            cache[layout["head"]] = layout
        if layout["head"] in self.active_blocks:
            return
        from daily_vibe.theming import mix, table_header_bg, table_stripe_bg
        t = self.theme
        kind, idx = layout["kinds"].get(block.blockNumber(), ("body", 0))
        border = QColor(t.colors["border"])
        x0 = geo.left() + self.document().documentMargin()
        if kind == "delim":
            p.setPen(QPen(QColor(t.colors["accent"]), 1.5))
            p.drawLine(QPointF(x0, geo.top()), QPointF(x0 + sum(layout["widths"]), geo.top()))
            return
        row = QRectF(x0, geo.top(), sum(layout["widths"]), geo.height())
        # opaque base first: hides the (tiny) raw text and any selection/find slivers on it
        p.fillRect(QRectF(geo.left(), geo.top(), max(geo.width(), row.right() - geo.left() + 2), geo.height()),
                   self.palette().base().color())
        if kind == "head":
            p.fillRect(row, QColor(table_header_bg(t)))
        elif idx % 2 == 1:
            p.fillRect(row, QColor(table_stripe_bg(t)))
        head_font, body_font = layout["fonts"]
        base = head_font if kind == "head" else body_font
        x = x0
        pad = layout["pad"]
        align_flags = {"center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}
        for i in range(layout["ncols"]):
            w = layout["widths"][i]
            cell = QRectF(x, row.top(), w, row.height())
            p.setPen(QPen(border, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(cell)
            text = d.cells[i] if i < len(d.cells) else ""
            styles = d.cell_styles[i] if i < len(d.cell_styles) else frozenset()
            font = self._cell_font(base, styles)
            p.setFont(font)
            fm = QFontMetricsF(font)
            text = fm.elidedText(text, Qt.TextElideMode.ElideRight, max(0.0, w - 2 * pad))
            color = t.colors["heading"] if kind == "head" else t.colors["text"]
            if "strike" in styles:
                color = t.colors["muted"]
            elif "code" in styles:
                color = t.hl("code") if hasattr(t, "hl") else color
            p.setPen(QColor(color))
            flags = align_flags.get(layout["aligns"][i], Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
            p.drawText(cell.adjusted(pad, 0, -pad, 0), flags, text)
            x += w

    def format_table_at_cursor(self) -> bool:
        """Pad the cells of the table under the cursor so the raw Markdown lines up
        (one undo step). Returns False if the cursor isn't in a table."""
        cur = self.textCursor()
        block = cur.block()
        doc = self.document()
        lines = doc.toPlainText().split("\n")
        found = md_tables.find_table(lines, block.blockNumber())
        if found is None:
            return False
        a, b = found
        new = md_tables.format_table(lines[a:b + 1])
        if new == lines[a:b + 1]:
            return True
        col = cur.positionInBlock()
        row = block.blockNumber()
        first, last = doc.findBlockByNumber(a), doc.findBlockByNumber(b)
        c = QTextCursor(doc)
        c.beginEditBlock()
        c.setPosition(first.position())
        c.setPosition(last.position() + len(last.text()), QTextCursor.MoveMode.KeepAnchor)
        c.insertText("\n".join(new))
        c.endEditBlock()
        nb = doc.findBlockByNumber(row)
        c.setPosition(nb.position() + min(col, len(nb.text())))
        self.setTextCursor(c)
        return True

    def _paint_block(self, p: QPainter, block, geo: QRectF, d) -> None:
        t = self.theme
        accent = QColor(t.colors["accent"])
        if d.hr:
            y = geo.center().y()
            p.setPen(QPen(QColor(t.colors["border"]), 1.5))
            p.drawLine(QPointF(geo.left() + 6, y), QPointF(self.viewport().width() - 10, y))
        levels = d.bullet_levels or [0] * len(d.bullets)
        for off, level in zip(d.bullets, levels):
            r1 = self._char_rect(block, off)
            r2 = self._char_rect(block, off + 1)
            cx = (r1.left() + r2.left()) / 2 if r2.top() == r1.top() else r1.left() + 3
            cy = r1.center().y() + 0.5
            color = QColor(t.hl("list"))
            shape = level % 3          # • ◦ ▪
            if shape == 0:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                p.drawEllipse(QPointF(cx, cy), 3.0, 3.0)
            elif shape == 1:
                p.setPen(QPen(color, 1.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), 2.8, 2.8)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                p.drawRect(QRectF(cx - 2.6, cy - 2.6, 5.2, 5.2))
        for off, checked in d.tasks:
            box = self._task_box(block, off)
            p.setPen(QPen(accent, 1.4))
            p.setBrush(accent if checked else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box, 3, 3)
            if checked:
                path = QPainterPath()
                path.moveTo(box.left() + box.width() * 0.22, box.top() + box.height() * 0.52)
                path.lineTo(box.left() + box.width() * 0.43, box.top() + box.height() * 0.72)
                path.lineTo(box.left() + box.width() * 0.78, box.top() + box.height() * 0.30)
                p.setPen(QPen(QColor(t.accent_text), 1.8))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        fill = QColor(accent)
        fill.setAlpha(48)
        edge = QColor(accent)
        edge.setAlpha(130)
        for s, e, _tag in d.tags:
            r1 = self._char_rect(block, s)
            r2 = self._char_rect(block, e)
            if r2.top() == r1.top():
                chips = [QRectF(r1.left() - 3, r1.top() + 1, r2.left() - r1.left() + 6, r1.height() - 2)]
            else:  # tag wrapped onto the next line: one chip piece per line
                right = geo.right() - 4
                chips = [QRectF(r1.left() - 3, r1.top() + 1, right - r1.left() + 3, r1.height() - 2),
                         QRectF(geo.left() + self.document().documentMargin() - 2, r2.top() + 1,
                                r2.left() - geo.left() - self.document().documentMargin() + 5, r2.height() - 2)]
                # trim the first piece to the text end of its line
                line = block.layout().lineForTextPosition(s)
                if line.isValid():
                    end_x = geo.left() + line.x() + line.naturalTextWidth()
                    chips[0].setRight(end_x + 3)
            p.setPen(QPen(edge, 1))
            p.setBrush(fill)
            for chip in chips:
                p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
        if d.kind == "quote":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.hl("quote")))
            p.drawRoundedRect(QRectF(geo.left() + 1, geo.top() + 1, 3, geo.height() - 2), 1.5, 1.5)
        if d.label and d.kind == "code":
            f = QFont(self.font())
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.75))
            p.setFont(f)
            p.setPen(QColor(t.colors["muted"]))
            r = self._char_rect(block, 0)
            p.drawText(QRectF(geo.left(), r.top(), geo.width() - 10, max(14.0, float(r.height()) + 12)),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, d.label)
        if d.plugin:
            f = QFont(self.font())
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.75))
            p.setFont(f)
            p.setPen(QColor(t.colors["muted"]))
            p.drawText(geo.adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"⚙ {d.plugin} plugin block")
        if d.inline_images:
            mw, mh = self.inline_image_limits()
            layout = block.layout()
            for off, target, _size in d.inline_images:
                pix = self.image_pixmap(target, mw, mh)
                if pix is None:
                    continue
                r = self._char_rect(block, off)
                line = layout.lineForTextPosition(off)
                if line.isValid():
                    y = geo.top() + line.y() + max(0.0, (line.height() - pix.height()) / 2)
                else:
                    y = r.bottom() - pix.height()
                p.drawPixmap(QPointF(r.left() + 2, y), pix)
        if d.image:
            pix = self.image_pixmap(d.image)
            if pix is not None:
                x = geo.left() + self.document().documentMargin() + 2
                y = geo.top() + max(2.0, (geo.height() - pix.height()) / 2)
                p.drawPixmap(QPointF(x, y), pix)

    # Hit testing (checkboxes, links, tags) ------------------------------------------
    def token_at(self, pos: QPoint) -> tuple[str, str] | None:
        """("tag", name) / ("link", target) under a viewport position, else None."""
        cur = self.cursorForPosition(pos)
        block = cur.block()
        text = block.text()
        u = U16(text)
        col16 = cur.positionInBlock()
        for s, e, tag in find_inline_tags(text):
            if u(s) <= col16 <= u(e):
                return ("tag", tag)
        for regex, group in ((IMAGE_RE, 2), (LINK_RE, 2), (AUTOLINK_RE, 1), (URL_RE, 1)):
            for m in regex.finditer(text):
                if u(m.start()) <= col16 <= u(m.end()):
                    return ("link", m.group(group).strip().split(" ")[0].strip("<>"))
        return None

    def task_at(self, pos: QPoint):
        if not self.live:
            return None
        cur = self.cursorForPosition(pos)
        block = cur.block()
        if block.blockNumber() in self.active_blocks:
            return None
        d = block_data(block)
        if d is None:
            return None
        for off, checked in d.tasks:
            if self._task_box(block, off).adjusted(-3, -3, 3, 3).contains(QPointF(pos)):
                return block, off, checked
        return None

    def toggle_task(self, block, offset: int, checked: bool) -> None:
        c = QTextCursor(self.document())
        c.setPosition(block.position() + offset + 1)
        c.setPosition(block.position() + offset + 2, QTextCursor.MoveMode.KeepAnchor)
        c.insertText(" " if checked else "x")  # one undo step

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            hit = self.task_at(pos)
            if hit is not None:
                self.toggle_task(*hit)
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                tok = self.token_at(pos)
                if tok is not None:
                    if tok[0] == "tag":
                        self.tag_activated.emit(tok[1], event.globalPosition().toPoint())
                    else:
                        self.link_activated.emit(tok[1])
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        if event.buttons() == Qt.MouseButton.NoButton:
            pos = event.position().toPoint()
            hand = self.task_at(pos) is not None or (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier and self.token_at(pos) is not None)
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hand else Qt.CursorShape.IBeamCursor)

    def contextMenuEvent(self, event) -> None:
        menu = self.build_context_menu(event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()

    def build_context_menu(self, pos: QPoint) -> QMenu:
        menu = self.createStandardContextMenu(pos)
        tok = self.token_at(pos)
        if tok is not None:
            first = menu.actions()[0] if menu.actions() else None
            extra = []
            if tok[0] == "tag":
                a = QAction(f"Show All Entries Tagged #{tok[1]}", menu)
                a.triggered.connect(lambda _=False, t=tok[1]: self.tag_entries_requested.emit(t))
                b = QAction(f"Rename Tag #{tok[1]}…", menu)
                b.triggered.connect(lambda _=False, t=tok[1]: self.tag_rename_requested.emit(t))
                extra = [a, b]
            else:
                a = QAction("Open Link", menu)
                a.triggered.connect(lambda _=False, t=tok[1]: self.link_activated.emit(t))
                extra = [a]
            for a in extra:
                menu.insertAction(first, a)
            menu.insertSeparator(first)
        return menu

    # Tag autocomplete -------------------------------------------------------------
    def set_tags(self, tags: list[str]) -> None:
        self.tag_model.setStringList(sorted(dict.fromkeys(tags), key=str.lower))

    def _tag_prefix(self) -> str | None:
        cur = self.textCursor()
        before = cur.block().text()[: cur.positionInBlock()]
        if before.lstrip().startswith("```"):
            return None
        m = _TAG_PREFIX_RE.search(before)
        if not m:
            return None
        return m.group(1) or ""

    def insert_completion(self, completion: str) -> None:
        if self.completer.widget() is not self:
            return
        prefix = self._tag_prefix()
        if prefix is None:
            return
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix))
        cur.insertText(completion + " ")
        self.setTextCursor(cur)

    def update_completer(self) -> None:
        prefix = self._tag_prefix()
        if prefix is None or not self.tag_model.rowCount():
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0 or (
                self.completer.completionCount() == 1 and self.completer.currentCompletion() == prefix):
            self.completer.popup().hide()
            return
        rect = self.cursorRect()
        popup = self.completer.popup()
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect.setWidth(max(180, popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 16))
        self.completer.complete(rect)

    def keyPressEvent(self, event) -> None:
        popup = self.completer.popup()
        if popup.isVisible() and event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab,
                                                 Qt.Key.Key_Escape, Qt.Key.Key_Backtab):
            event.ignore()  # QCompleter's event filter handles these (activated -> insert_completion)
            return
        super().keyPressEvent(event)
        if event.text() or event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.update_completer()
        elif popup.isVisible() and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End):
            self.update_completer()

    def _image_files(self, source: QMimeData) -> list[Path]:
        if not source.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in source.urls() if u.isLocalFile() and is_image_file(u.toLocalFile())]

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        if source.hasImage() or self._image_files(source):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        if self.image_handler is not None:
            files = self._image_files(source)
            if files:
                links = [self.image_handler(f) for f in files]
                self._insert_links(links)
                return
            if source.hasImage():
                image = QImage(source.imageData())
                if not image.isNull():
                    ba = QByteArray()
                    buf = QBuffer(ba)
                    buf.open(QIODevice.OpenModeFlag.WriteOnly)
                    image.save(buf, "PNG")
                    buf.close()
                    self._insert_links([self.image_handler(bytes(ba.data()))])
                    return
        super().insertFromMimeData(source)

    def _insert_links(self, links: list[str | None]) -> None:
        links = [l for l in links if l]
        if links:
            self.textCursor().insertText("\n".join(links) + "\n")

    def insert_at_cursor(self, text: str) -> None:
        self.textCursor().insertText(text)


def _pillow_qimage(path: Path) -> QImage | None:
    """Fallback decoder (e.g. a Qt build without the WebP image plugin)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGBA")
            data = im.tobytes("raw", "RGBA")
            qi = QImage(data, im.width, im.height, 4 * im.width, QImage.Format.Format_RGBA8888)
            return qi.copy()
    except Exception:
        return None
