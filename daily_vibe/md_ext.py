"""Markdown pieces shared by the preview/Reading view and the PDF export:
~~strikethrough~~ (-> <del>), and table markup Qt's rich-text engine renders well."""
from __future__ import annotations

import re

from markdown.extensions import Extension
from markdown.inlinepatterns import SimpleTagInlineProcessor

STRIKE_RE = r"(~~)(?=\S)(.+?)(?<=\S)~~"


class StrikethroughExtension(Extension):
    def extendMarkdown(self, md):  # noqa: N802 (markdown API)
        # above emphasis (60) so "~~**x**~~" nests correctly
        md.inlinePatterns.register(SimpleTagInlineProcessor(STRIKE_RE, "del"), "strikethrough", 65)


def extensions(*extra: str) -> list:
    return ["extra", "sane_lists", *extra, StrikethroughExtension()]


_ALIGN_RE = re.compile(r'<(t[hd]) style="text-align: ?(left|center|right);?">')


def fix_tables(body: str) -> str:
    """Qt ignores CSS text-align on cells but honors align=; add spacing attributes
    so tables get borders and padding even where CSS support is partial."""
    body = _ALIGN_RE.sub(lambda m: f'<{m.group(1)} align="{m.group(2)}">', body)
    return body.replace("<table>", '<table class="md" cellspacing="0" cellpadding="6">')


def table_css(border: str, header_bg: str, stripe_bg: str) -> str:
    return (f"table.md {{ border-collapse: collapse; margin: 6px 0; }}\n"
            f"table.md th, table.md td {{ border: 1px solid {border}; padding: 4px 8px; }}\n"
            f"table.md th {{ background-color: {header_bg}; font-weight: bold; }}\n"
            f"del {{ text-decoration: line-through; }}\n")


def stripe_rows(body: str, stripe_bg: str) -> str:
    """Alternate row shading (Qt has no :nth-child and ignores <tr bgcolor>):
    the cells of every second body row get a bgcolor."""
    def fix_tbody(m):
        rows = re.split(r"(?=<tr>)", m.group(1))
        out, i = [], 0
        for r in rows:
            if r.startswith("<tr>"):
                if i % 2 == 1:
                    r = re.sub(r"<td(?=[ >])", f'<td bgcolor="{stripe_bg}"', r)
                i += 1
            out.append(r)
        return "<tbody>" + "".join(out) + "</tbody>"
    return re.sub(r"<tbody>(.*?)</tbody>", fix_tbody, body, flags=re.S)


_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_ITEM_RE = re.compile(r"^([ \t]*)([-+*]|\d{1,9}[.)])([ \t]+)(.*)$")
_TASK_RE = re.compile(r"^\[([ xX])\](?=[ \t]|$)")


def _cols(ws: str) -> int:
    c = 0
    for ch in ws:
        c = (c // 4 + 1) * 4 if ch == "\t" else c + 1
    return c


def normalize_lists(text: str) -> str:
    """Render-time only (never written back): Python-Markdown needs 4-space nesting,
    journals often use 2 spaces or tabs. Re-indent nested list items to 4 spaces per
    level (same nesting rules as Live Preview) and show task boxes as ☐ / ☑."""
    out, stack, fence = [], [], None
    for line in text.split("\n"):
        fm = _FENCE_RE.match(line)
        if fence:
            out.append(line)
            if fm and fm.group(1) == fence:
                fence = None
            continue
        if fm:
            fence = fm.group(1)
            out.append(line)
            continue
        m = _ITEM_RE.match(line)
        if m and not (m.group(2) in "-*" and re.fullmatch(r"[ \t]*([-*])([ \t]*\1){2,}[ \t]*", line)):
            cols = _cols(m.group(1))
            while stack and stack[-1] >= cols:
                stack.pop()
            level = len(stack)
            stack.append(cols)
            rest = m.group(4)
            tm = _TASK_RE.match(rest)
            if tm:
                rest = ("☑" if tm.group(1) in "xX" else "☐") + rest[3:]
            out.append(" " * (4 * level) + m.group(2) + " " + rest)
        elif not line.strip():
            out.append(line)
        else:
            ws = line[: len(line) - len(line.lstrip())]
            if not ws:
                stack = []
                out.append(line)
            elif stack:
                out.append(" " * (4 * len(stack)) + line.lstrip())   # item continuation
            else:
                out.append(line)
    return "\n".join(out)
