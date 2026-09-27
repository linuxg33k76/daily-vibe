"""Markdown syntax highlighting for the editor, colored from the active theme."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from daily_vibe.tags import find_inline_tags

IN_FENCE = 1


def _fmt(color: str, bold=False, italic=False, underline=False, background: str | None = None) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    if underline:
        f.setFontUnderline(True)
    if background:
        f.setBackground(QColor(background))
    return f


class MarkdownHighlighter(QSyntaxHighlighter):
    def __init__(self, document, theme):
        super().__init__(document)
        self.fence_re = QRegularExpression(r"^\s*(```|~~~)")
        self.set_theme(theme)

    def set_theme(self, theme) -> None:
        h = theme.hl
        code_bg = h("code_bg")
        self.formats = {
            "heading": _fmt(h("heading"), bold=True),
            "bold": _fmt(h("emphasis"), bold=True),
            "italic": _fmt(h("emphasis"), italic=True),
            "code": _fmt(h("code"), background=code_bg),
            "fence": _fmt(h("code"), background=code_bg),
            "link": _fmt(h("link"), underline=True),
            "image": _fmt(h("image")),
            "list": _fmt(h("list"), bold=True),
            "quote": _fmt(h("quote"), italic=True),
            "marker": _fmt(h("marker"), italic=True),
            "tag": _fmt(h("tag"), bold=True),
        }
        # (regex, format name, capture group to color)
        self.rules = [
            (QRegularExpression(r"^\s{0,3}#{1,6}\s.*$"), "heading", 0),
            (QRegularExpression(r"^\s*>.*$"), "quote", 0),
            (QRegularExpression(r"^\s*([-*+]|\d+[.)])\s"), "list", 1),
            (QRegularExpression(r"(?<![*\w])\*(?![\s*])[^*\n]+?(?<!\s)\*(?![*\w])|(?<![_\w])_(?![\s_])[^_\n]+?(?<!\s)_(?![_\w])"), "italic", 0),
            (QRegularExpression(r"\*\*(?!\s)[^*\n]+?\*\*|__(?!\s)[^_\n]+?__"), "bold", 0),
            (QRegularExpression(r"(?<!!)\[[^\]\n]*\]\([^)\n]*\)|<https?://[^>\s]+>"), "link", 0),
            (QRegularExpression(r"!\[[^\]\n]*\]\([^)\n]*\)"), "image", 0),
            (QRegularExpression(r"`[^`\n]+`"), "code", 0),
            (QRegularExpression(r"<!--.*?-->"), "marker", 0),
        ]
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        fence = self.fence_re.match(text).hasMatch()
        if self.previousBlockState() == IN_FENCE:
            self.setFormat(0, len(text), self.formats["fence"])
            self.setCurrentBlockState(0 if fence else IN_FENCE)
            return
        if fence:
            self.setFormat(0, len(text), self.formats["fence"])
            self.setCurrentBlockState(IN_FENCE)
            return
        self.setCurrentBlockState(0)
        for regex, name, group in self.rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(group), m.capturedLength(group), self.formats[name])
        # Tags last, using the same rules as the tag index (code spans, URLs,
        # headings "# ", hex colors are skipped). Offsets are Python str
        # indices; convert to UTF-16 for Qt.
        from daily_vibe.markers import utf16_len
        for start, end, _tag in find_inline_tags(text):
            s16 = utf16_len(text[:start])
            self.setFormat(s16, utf16_len(text[start:end]), self.formats["tag"])
