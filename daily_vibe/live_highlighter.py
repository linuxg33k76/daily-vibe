"""Live Preview highlighting (Obsidian-style) for the plain-text editor.

The document text is never modified. Everything is done with character
formats from a QSyntaxHighlighter plus a little custom painting in the editor:

* Markdown syntax markers (``#``, ``**``, backticks, ``>``, link URLs, plugin
  comment markers, front matter fences) are *hidden* on inactive lines by
  giving them a 1 px transparent font, and shown in a muted color on the
  lines that hold the cursor or selection ("active" blocks).
* Headings get larger bold fonts, emphasis/strike/code/links/tags are styled.
* Per-block ``LiveBlockData`` records what the editor should paint on top of
  inactive blocks: list bullets, task checkboxes, horizontal rules and inline
  images. For an image-only line the first character gets a transparent font
  whose pixel size makes the line as tall as the scaled image, and the editor
  paints the pixmap into that space.
* Block kinds (quote / code / plugin / front) drive full-width background
  bands, drawn by the editor as extra selections.

Qt positions are UTF-16 code units; Python indices are converted with ``U16``.
"""
from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QFontMetrics, QFontMetricsF, QSyntaxHighlighter, QTextBlockUserData, QTextCharFormat

from daily_vibe import md_tables
from daily_vibe.tags import find_inline_tags

FENCE = 1
FRONT = 2
PLUGIN = 4
TABLE = 8

INDENT_PX = 22          # Live Preview width of one list nesting level
TABLE_PAD_Y = 12        # extra row height of a rendered table row
LIST_ITEM_RE = re.compile(r"^([ \t]*)([-*+]|\d+[.)])(\s+|$)")

HEADING_SCALE = {1: 1.75, 2: 1.45, 3: 1.25, 4: 1.12, 5: 1.05, 6: 1.0}

FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(\s{0,3}#{1,6})(\s+)(.*)$")
HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
QUOTE_RE = re.compile(r"^(\s*(?:>\s?)+)")
TASK_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)\[([ xX])\](\s|$)")
BULLET_RE = re.compile(r"^(\s*)([-*+])(\s+)")
ORDERED_RE = re.compile(r"^(\s*)(\d+[.)])(\s+)")
IMAGE_LINE_RE = re.compile(r"^\s*!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)\s*$")
IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]*)\)")
LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\n]*)\)")
AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
URL_RE = re.compile(r"(?<![(<\w])(https?://[^\s)>\]]+)")
CODE_RE = re.compile(r"(`+)(?=[^`])(.+?)(?<=[^`])\1(?!`)")
BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
ITALIC_RE = re.compile(r"(?<![*\w])(\*)(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])|(?<![_\w])(_)(?=[^\s_])(.+?)(?<=[^\s_])_(?![_\w])")
STRIKE_RE = re.compile(r"(~~)(?=\S)(.+?)(?<=\S)~~")
HIGHLIGHT_RE = re.compile(r"(==)(?=\S)(.+?)(?<=\S)==")
PLUGIN_OPEN_RE = re.compile(r"^\s*<!--\s*plugin:([\w.-]+)\s*-->\s*$")
PLUGIN_CLOSE_RE = re.compile(r"^\s*<!--\s*/plugin:([\w.-]+)\s*-->\s*$")
COMMENT_RE = re.compile(r"<!--.*?-->")


class U16:
    """Convert Python str indices to Qt UTF-16 positions (cheap for BMP-only text)."""
    __slots__ = ("pref",)

    def __init__(self, text: str):
        if any(ord(c) > 0xFFFF for c in text):
            pref = [0]
            for c in text:
                pref.append(pref[-1] + (2 if ord(c) > 0xFFFF else 1))
            self.pref = pref
        else:
            self.pref = None

    def __call__(self, i: int) -> int:
        return i if self.pref is None else self.pref[i]


class LiveBlockData(QTextBlockUserData):
    """What to paint over an (inactive) block, and which band it belongs to."""

    def __init__(self):
        super().__init__()
        self.kind = ""              # "", quote, code, plugin, front
        self.bullets: list[int] = []   # UTF-16 offset of a list marker
        self.tasks: list[tuple[int, bool]] = []  # (offset of "[", checked)
        self.hr = False
        self.image: str | None = None   # link target of an image-only line
        self.image_size: tuple[int, int] | None = None  # painted size
        self.heading = 0
        self.tags: list[tuple[int, int, str]] = []  # UTF-16 (start, end, tag) for chips
        self.label = ""       # small right-aligned label (code language)
        self.plugin = ""      # plugin id on an opening marker line (painted as a label)
        self.bullet_levels: list[int] = []   # nesting level of each bullet (glyph • ◦ ▪)
        self.guides: list[int] = []          # UTF-16 offsets of ancestor list markers (indent guides)
        self.inline_images: list[tuple[int, str, tuple[int, int]]] = []  # (offset of "!", target, size)
        self.table: str = ""                 # "head" | "delim" | "body" for a rendered table row
        self.cells: list[str] = []           # display text of the cells
        self.cell_styles: list[frozenset] = []  # whole-cell bold / italic / strike / code


def indent_cols(ws: str) -> int:
    cols = 0
    for ch in ws:
        cols = (cols // 4 + 1) * 4 if ch == "\t" else cols + 1
    return cols


def offset_for_col(ws: str, col: int) -> int:
    """Index into the leading whitespace `ws` where column `col` starts."""
    cols = 0
    for i, ch in enumerate(ws):
        if cols >= col:
            return i
        cols = (cols // 4 + 1) * 4 if ch == "\t" else cols + 1
    return len(ws)


def _color(c: str, alpha: int | None = None) -> QColor:
    q = QColor(c)
    if alpha is not None:
        q.setAlpha(alpha)
    return q


class LiveHighlighter(QSyntaxHighlighter):
    """Needs ``editor`` for the active-block set, base font and image sizing."""

    def __init__(self, document, theme, editor):
        super().__init__(document)
        self.editor = editor
        self.enabled = True
        self.set_theme(theme, rehighlight=False)

    # Formats ---------------------------------------------------------------------
    def set_theme(self, theme, rehighlight: bool = True) -> None:
        self.theme = theme
        h, c = theme.hl, theme.colors
        base = self.editor.font()
        self.base_px = base.pixelSize() if base.pixelSize() > 0 else round(base.pointSizeF() * 96 / 72)

        def fmt(color=None, bold=False, italic=False, underline=False, bg=None, strike=False, mono=False):
            f = QTextCharFormat()
            if color:
                f.setForeground(_color(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            if italic:
                f.setFontItalic(True)
            if underline:
                f.setFontUnderline(True)
            if strike:
                f.setFontStrikeOut(True)
            if bg:
                f.setBackground(_color(bg))
            if mono:
                f.setFontFamilies(self.editor.mono_families())
            return f

        self.f_marker = fmt(h("marker"))
        self.f_hidden = QTextCharFormat()
        self.f_hidden.setForeground(_color("#000000", 0))
        self.f_hidden.setFontPointSize(1)
        # 1 % letter spacing: hidden runs (link URLs...) take ~0 px instead of ~0.5 px/char
        self.f_hidden.setFontLetterSpacingType(QFont.SpacingType.PercentageSpacing)
        self.f_hidden.setFontLetterSpacing(1.0)
        self.f_plugin_hidden = QTextCharFormat(self.f_hidden)
        self.f_plugin_hidden.setFontPointSize(max(7.0, base.pointSizeF() * 0.75))
        self.f_bullet_gap = QTextCharFormat()   # a little air between the bullet and the text
        self.f_bullet_gap.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        self.f_bullet_gap.setFontLetterSpacing(5)
        self.f_ghost = QTextCharFormat()  # keeps width, invisible (list markers under a painted bullet)
        self.f_ghost.setForeground(_color("#000000", 0))
        self.f_bold = fmt(bold=True)
        self.f_italic = fmt(italic=True)
        self.f_strike = fmt(strike=True, color=c["muted"])
        self.f_mark = QTextCharFormat()
        self.f_mark.setBackground(_color(c["accent"], 70))
        self.f_code = fmt(h("code"), bg=h("code_bg"), mono=True)
        self.f_block_code = fmt(h("code"), mono=True)
        self.f_link = fmt(h("link"), underline=True)
        self.f_url = fmt(h("marker"))
        self.f_tag = fmt(h("tag"), bold=True)  # the editor paints a chip behind it
        self.f_tag_active = fmt(h("tag"), bold=True)
        self.f_quote = fmt(h("quote"), italic=True)
        self.f_list = fmt(h("list"), bold=True)
        self.f_done = fmt(c["muted"], strike=True)
        self.f_front = fmt(c["muted"])
        self.f_front.setFontPointSize(max(7.0, base.pointSizeF() * 0.85))
        self.f_plugin_marker = fmt(c["muted"], italic=True)
        self.f_plugin_marker.setFontPointSize(max(7.0, base.pointSizeF() * 0.85))
        self.f_comment = fmt(c["muted"], italic=True)
        self.f_heading = {}
        heading_families = self.editor.heading_families()
        for level, scale in HEADING_SCALE.items():
            f = fmt(h("heading"), bold=True)
            f.setFontPointSize(base.pointSizeF() * scale)
            if heading_families:
                f.setFontFamilies(heading_families)
            self.f_heading[level] = f
        self.f_table_raw = fmt(mono=True)
        self._space_cache: dict = {}
        self._space_adv = QFontMetricsF(base).horizontalAdvance(" ")
        self._row_format = self._image_format(QFontMetrics(base).lineSpacing() + TABLE_PAD_Y)
        if rehighlight:
            self.editor.guarded_rehighlight()

    # Helpers --------------------------------------------------------------------
    def _set(self, u, start: int, end: int, f: QTextCharFormat, merge: bool = True) -> None:
        if end <= start:
            return
        s = u(start)
        n = u(end) - s
        if merge:
            nf = QTextCharFormat(self.format(s))
            nf.merge(f)
            f = nf
        self.setFormat(s, n, f)

    def _marker(self, u, start: int, end: int, active: bool, ghost: bool = False) -> None:
        if active:
            self._set(u, start, end, self.f_marker)
        else:
            self._set(u, start, end, self.f_ghost if ghost else self.f_hidden)

    def _image_format(self, height: int) -> QTextCharFormat:
        """Transparent format whose line height is about ``height`` px."""
        f = QFont(self.editor.font())
        px = max(8, int(height / 1.2))
        for _ in range(3):
            f.setPixelSize(px)
            got = QFontMetrics(f).lineSpacing()
            if abs(got - height) <= 2:
                break
            px = max(8, int(px * height / max(1, got)))
        tf = QTextCharFormat()
        tf.setFont(f)
        tf.setForeground(_color("#000000", 0))
        return tf

    # Highlighting ------------------------------------------------------------------
    def highlightBlock(self, text: str) -> None:
        block = self.currentBlock()
        data = LiveBlockData()
        self.setCurrentBlockUserData(data)
        prev = self.previousBlockState()
        prev = 0 if prev < 0 else prev
        active = block.blockNumber() in self.editor.active_blocks
        u = U16(text)
        n = len(text)
        state = prev & PLUGIN

        # Front matter (first line "---" ... closing "---"/"...")
        if prev & FRONT or (block.blockNumber() == 0 and text.strip() == "---"):
            data.kind = "front"
            closing = prev & FRONT and text.strip() in ("---", "...")
            fence_line = closing or not prev & FRONT
            self.setCurrentBlockState(state if closing else state | FRONT)
            if fence_line:
                self._marker(u, 0, n, active)
            else:
                self._set(u, 0, n, self.f_front if not active else self.f_marker, merge=False)
            return

        # Fenced code
        is_fence = bool(FENCE_RE.match(text))
        if prev & FENCE:
            data.kind = "code"
            self.setCurrentBlockState(state if is_fence else state | FENCE)
            if is_fence:
                self._marker(u, 0, n, active)
            else:
                self._set(u, 0, n, self.f_block_code, merge=False)
                self._line_spacing(text, u)
            return
        if is_fence:
            data.kind = "code"
            self.setCurrentBlockState(state | FENCE)
            m = FENCE_RE.match(text)
            data.label = text[m.end():].strip()
            self._marker(u, 0, m.end(), active)
            self._set(u, m.end(), n, self.f_marker if active else self.f_hidden, merge=False)
            return

        # Plugin block markers
        pm = PLUGIN_OPEN_RE.match(text)
        if pm:
            data.kind = "plugin"
            data.plugin = pm.group(1)
            self.setCurrentBlockState(PLUGIN)
            self._set(u, 0, n, self.f_plugin_marker if active else self.f_plugin_hidden, merge=False)
            return
        if PLUGIN_CLOSE_RE.match(text):
            data.kind = "plugin"
            self.setCurrentBlockState(0)
            self._set(u, 0, n, self.f_plugin_marker if active else self.f_hidden, merge=False)
            return
        self.setCurrentBlockState(state)
        if state & PLUGIN:
            data.kind = "plugin"

        # GFM pipe tables (header row found by peeking at the next line)
        if "|" in text:
            in_table = bool(prev & TABLE) and md_tables.is_row(text)
            nxt = block.next()
            is_head = (not in_table and nxt.isValid() and md_tables.is_header(text, nxt.text()))
            if in_table or is_head:
                self.setCurrentBlockState(state | TABLE)
                self._table_row(text, u, data, is_head, active)
                return

        # Horizontal rule
        if HR_RE.match(text):
            data.hr = True
            self._set(u, 0, n, self.f_marker if active else self.f_ghost, merge=False)
            return

        # Image-only line -> painted image
        m = IMAGE_LINE_RE.match(text)
        if m and not active:
            size = self.editor.image_size_for(m.group(2))
            if size:
                data.image = m.group(2)
                data.image_size = size
                self._set(u, 0, n, self.f_hidden, merge=False)
                self._set(u, 0, 1, self._image_format(size[1] + 8), merge=False)
                return

        pos = 0
        # Headings
        hm = HEADING_RE.match(text)
        if hm:
            level = min(6, hm.group(1).count("#"))
            data.heading = level
            self._set(u, 0, n, self.f_heading[level], merge=False)
            self._marker(u, 0, hm.end(2), active)
        else:
            qm = QUOTE_RE.match(text)
            if qm:
                data.kind = data.kind or "quote"
                self._set(u, qm.end(), n, self.f_quote, merge=False)
                self._marker(u, 0, qm.end(), active, ghost=True)  # keeps the indent next to the bar
                pos = qm.end()
            rest = text[pos:]
            li = LIST_ITEM_RE.match(rest)
            level = 0
            if li:
                ws = li.group(1)
                cols = indent_cols(ws)
                if cols:
                    ancestors = self._list_ancestors(block, cols)
                    level = len(ancestors)
                    data.guides = [u(pos + offset_for_col(ws, a)) for a in ancestors]
                    self._indent(u, pos, pos + len(ws), ws, cols, level)
            tm = TASK_RE.match(rest)
            if tm:
                bracket = pos + tm.start(4) - 1
                checked = tm.group(4) in "xX"
                data.tasks.append((u(bracket), checked))
                if checked:
                    self._set(u, bracket + 3, n, self.f_done)
                if active:
                    self._set(u, pos, bracket + 3, self.f_list)
                else:
                    self._set(u, pos + tm.start(2), bracket + 3, self.f_ghost)
            else:
                bm = BULLET_RE.match(rest)
                if bm:
                    off = pos + bm.start(2)
                    if active:
                        self._set(u, off, off + 1, self.f_list)
                    else:
                        data.bullets.append(u(off))
                        data.bullet_levels.append(level)
                        self._set(u, off, off + 1, self.f_ghost)
                    self._set(u, off, off + 1, self.f_bullet_gap)   # same width raw or rendered
                else:
                    om = ORDERED_RE.match(rest)
                    if om:
                        self._set(u, pos + om.start(2), pos + om.end(2), self.f_list)

        self._inline(text, u, active)
        self._line_spacing(text, u)

    # Lists -----------------------------------------------------------------------
    def _list_ancestors(self, block, cols: int, max_steps: int = 200) -> list[int]:
        """Indent columns of the enclosing list items (outermost first)."""
        out, cur = [], cols
        b = block.previous()
        steps = 0
        while b.isValid() and cur > 0 and steps < max_steps:
            steps += 1
            t = b.text()
            q = QUOTE_RE.match(t)
            if q:
                t = t[q.end():]
            if t.strip():
                m = LIST_ITEM_RE.match(t)
                ind = indent_cols(m.group(1) if m else t[: len(t) - len(t.lstrip())])
                if m and ind < cur:
                    out.append(ind)
                    cur = ind
                elif not m and ind == 0:
                    break           # a paragraph ends the list
            b = b.previous()
        return out[::-1]

    def _indent(self, u, start: int, end: int, ws: str, cols: int, level: int) -> None:
        """Widen leading spaces so each nesting level is INDENT_PX wide
        (tabs use the editor's tab stops, also INDENT_PX)."""
        if level <= 0 or " " not in ws:
            return
        unit = max(1.0, cols / level)
        extra = INDENT_PX / unit - self._space_adv
        f = QTextCharFormat()
        f.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        f.setFontLetterSpacing(extra)
        for i, ch in enumerate(ws):
            if ch == " ":
                self._set(u, start + i, start + i + 1, f)

    # Tables ------------------------------------------------------------------------
    def _table_row(self, text: str, u, data: LiveBlockData, is_head: bool, active: bool) -> None:
        n = len(text)
        prev = self.currentBlock().previous()
        pd = block_data(prev) if prev.isValid() else None
        if is_head:
            data.table = "head"
        elif md_tables.is_delimiter(text) and pd is not None and pd.table == "head":
            data.table = "delim"
        else:
            data.table = "body"
        raw_cells = md_tables.split_row(text) or []
        data.cells = [md_tables.cell_text(c) for c in raw_cells]
        data.cell_styles = [md_tables.cell_style(c) for c in raw_cells]
        if active:  # raw source, monospace so "Format Table" output lines up
            self._set(u, 0, n, self.f_table_raw, merge=False)
            for i, ch in enumerate(text):
                if ch == "|" or (data.table == "delim" and ch in ":-"):
                    self._set(u, i, i + 1, self.f_marker)
            if data.table == "head":
                self._set(u, 0, n, self.f_bold)
            return
        self._set(u, 0, n, self.f_hidden, merge=False)
        if data.table != "delim":
            self._set(u, 0, 1, self._row_format, merge=False)

    # Line spacing ----------------------------------------------------------------------
    def _line_spacing(self, text: str, u) -> None:
        """Extra line spacing without touching the document: spaces (invisible
        anyway) get a taller font with negative letter spacing so their width is
        unchanged; the taller glyph box makes the whole visual line taller."""
        ls = self.editor.line_spacing
        if ls <= 1.01 or " " not in text:
            return
        base_pt = self.editor.font().pointSizeF()
        for i, ch in enumerate(text):
            if ch != " ":
                continue
            s16 = u(i)
            cur = self.format(s16)
            pt = cur.fontPointSize() or base_pt
            if pt <= 1.01:
                continue    # hidden marker
            key = (round(pt, 2), cur.fontWeight(), tuple(cur.fontFamilies() or ()))
            adj = self._space_cache.get(key)
            if adj is None:
                small = QFont(self.editor.font())
                if cur.fontFamilies():
                    small.setFamilies(cur.fontFamilies())
                small.setPointSizeF(pt)
                small.setWeight(QFont.Weight(cur.fontWeight()) if cur.fontWeight() else small.weight())
                big = QFont(small)
                big.setPointSizeF(pt * ls)
                adj = (pt * ls, QFontMetricsF(small).horizontalAdvance(" ") - QFontMetricsF(big).horizontalAdvance(" "))
                self._space_cache[key] = adj
            nf = QTextCharFormat(cur)
            nf.setFontPointSize(adj[0])
            spacing = adj[1]
            if cur.fontLetterSpacingType() == QFont.SpacingType.AbsoluteSpacing:
                spacing += cur.fontLetterSpacing()      # keep list indent widening
            nf.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
            nf.setFontLetterSpacing(spacing)
            self.setFormat(s16, 1, nf)

    def _inline_image_format(self, w: int, h: int) -> QTextCharFormat:
        """Transparent "!" whose line height is ~h+6 and whose advance is w+4
        (the image is painted vertically centered in that line, so the big glyph's
        descent doesn't leave an empty band under the image)."""
        f = QFont(self.editor.font())
        px = max(8, int(h * 0.8))
        for _ in range(4):
            f.setPixelSize(px)
            height = QFontMetrics(f).height()
            if abs(height - (h + 6)) <= 1:
                break
            px = max(8, int(px * (h + 6) / max(1, height)))
        tf = QTextCharFormat()
        tf.setFont(f)
        tf.setForeground(_color("#000000", 0))
        tf.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        tf.setFontLetterSpacing(w + 4 - QFontMetricsF(f).horizontalAdvance("!"))
        return tf

    def _inline(self, text: str, u, active: bool) -> None:
        taken = bytearray(len(text))  # code spans / link urls are opaque to later rules

        def free(a, b):
            return not any(taken[a:b])

        def take(a, b):
            taken[a:b] = b"\x01" * (b - a)

        for m in CODE_RE.finditer(text):
            s, e = m.start(), m.end()
            k = len(m.group(1))
            self._set(u, s + k, e - k, self.f_code)
            self._marker(u, s, s + k, active)
            self._marker(u, e - k, e, active)
            take(s, e)
        for m in COMMENT_RE.finditer(text):
            if free(m.start(), m.end()):
                self._set(u, m.start(), m.end(), self.f_comment if active else self.f_hidden, merge=False)
                take(m.start(), m.end())
        for m in IMAGE_RE.finditer(text):
            if free(m.start(), m.end()) and not active:
                target = m.group(2).strip().split(" ")[0].strip("<>")
                size = self.editor.inline_image_size(target)
                if size:
                    self._set(u, m.start(), m.end(), self.f_hidden, merge=False)
                    self._set(u, m.start(), m.start() + 1, self._inline_image_format(*size), merge=False)
                    data = self.currentBlockUserData()
                    if isinstance(data, LiveBlockData):
                        data.inline_images.append((u(m.start()), target, size))
                    take(m.start(), m.end())
                    continue
            if free(m.start(), m.end()):
                self._set(u, m.start(), m.end(), self.f_link)
                self._marker(u, m.start(), m.start() + 2, active)
                self._marker(u, m.end(1), m.end(), active)
                take(m.start(), m.end())
        for m in LINK_RE.finditer(text):
            if free(m.start(), m.end()):
                self._set(u, m.start(1), m.end(1), self.f_link)
                self._marker(u, m.start(), m.start(1), active)
                self._marker(u, m.end(1), m.end(), active)
                take(m.end(1), m.end())
        for m in AUTOLINK_RE.finditer(text):
            if free(m.start(), m.end()):
                self._set(u, m.start(1), m.end(1), self.f_link)
                self._marker(u, m.start(), m.start() + 1, active)
                self._marker(u, m.end() - 1, m.end(), active)
                take(m.start(), m.end())
        for m in URL_RE.finditer(text):
            if free(m.start(), m.end()):
                self._set(u, m.start(), m.end(), self.f_link)
                take(m.start(), m.end())
        for regex, f in ((BOLD_RE, self.f_bold), (STRIKE_RE, self.f_strike), (HIGHLIGHT_RE, self.f_mark)):
            for m in regex.finditer(text):
                s, e = m.start(), m.end()
                if not free(s, e):
                    continue
                k = len(m.group(1))
                self._set(u, s + k, e - k, f)
                self._marker(u, s, s + k, active)
                self._marker(u, e - k, e, active)
        for m in ITALIC_RE.finditer(text):
            s, e = m.start(), m.end()
            if not free(s, e):
                continue
            self._set(u, s + 1, e - 1, self.f_italic)
            self._marker(u, s, s + 1, active)
            self._marker(u, e - 1, e, active)
        if "#" in text:
            data = self.currentBlockUserData()
            for s, e, tag in find_inline_tags(text):
                self._set(u, s, e, self.f_tag_active if active else self.f_tag)
                if isinstance(data, LiveBlockData):
                    data.tags.append((u(s), u(e), tag))


def block_data(block) -> LiveBlockData | None:
    d = block.userData()
    return d if isinstance(d, LiveBlockData) else None
