"""GFM pipe tables: parsing helpers for Live Preview and the Format Table action.

    | Day | Distance | Pace |
    |:----|:--------:|-----:|
    | Mon | 5 km     | 5:40 |

A table is a header row, a delimiter row with the same number of cells
(``---``, ``:---`` left, ``:---:`` center, ``---:`` right) and any number of
body rows; it ends at the first blank line or line without a pipe.
"""
from __future__ import annotations

import re
import unicodedata

_DELIM_CELL = re.compile(r"^\s*:?-+:?\s*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")


def split_row(line: str) -> list[str] | None:
    """Cells of a table row (outer pipes optional, ``\\|`` escaped, pipes inside
    code spans ignored). None if the line has no unescaped pipe."""
    s = line.strip()
    if "|" not in s:
        return None
    masked = _CODE_SPAN.sub(lambda m: "x" * len(m.group(0)), s)
    masked = masked.replace("\\|", "xx")
    cuts = [i for i, ch in enumerate(masked) if ch == "|"]
    if not cuts:
        return None
    cells, start = [], 0
    for c in cuts:
        cells.append(s[start:c])
        start = c + 1
    cells.append(s[start:])
    if s.startswith("|"):
        cells = cells[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        cells = cells[:-1]
    return [c.strip() for c in cells]


def is_delimiter(line: str) -> bool:
    cells = split_row(line)
    return bool(cells) and all(_DELIM_CELL.match(c) for c in cells)


def alignments(line: str) -> list[str]:
    out = []
    for c in split_row(line) or []:
        c = c.strip()
        left, right = c.startswith(":"), c.endswith(":")
        out.append("center" if left and right else "right" if right else "left" if left else "")
    return out


def is_header(line: str, next_line: str | None) -> bool:
    if next_line is None or not line.strip():
        return False
    cells, delim = split_row(line), split_row(next_line)
    return bool(cells) and bool(delim) and is_delimiter(next_line) and len(cells) == len(delim)


def is_row(line: str) -> bool:
    return bool(line.strip()) and split_row(line) is not None


def find_table(lines: list[str], index: int) -> tuple[int, int] | None:
    """(first, last) line indices of the table containing line `index`."""
    if not (0 <= index < len(lines)) or not is_row(lines[index]):
        return None
    start = index
    while start > 0 and is_row(lines[start - 1]):
        start -= 1
    # the header is the first row followed by a delimiter row
    for h in range(start, index + 1):
        if h + 1 < len(lines) and is_header(lines[h], lines[h + 1]):
            end = h + 1
            while end + 1 < len(lines) and is_row(lines[end + 1]):
                end += 1
            if h <= index <= end:
                return h, end
    return None


def display_width(s: str) -> int:
    w = 0
    for ch in s:
        if unicodedata.combining(ch) or ch in "\u200d\ufe0f":
            continue
        w += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return w


def _pad(s: str, width: int, align: str) -> str:
    gap = width - display_width(s)
    if align == "right":
        return " " * gap + s
    if align == "center":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def format_table(lines: list[str]) -> list[str]:
    """Pad cells so the raw Markdown lines up (header, delimiter, rows)."""
    if len(lines) < 2 or not is_delimiter(lines[1]):
        return lines
    header = split_row(lines[0]) or []
    aligns = alignments(lines[1])
    rows = [split_row(l) or [] for l in lines[2:]]
    ncols = max([len(header), len(aligns)] + [len(r) for r in rows])
    header += [""] * (ncols - len(header))
    aligns += [""] * (ncols - len(aligns))
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    widths = [max(3, display_width(header[i]), *(display_width(r[i]) for r in rows)) for i in range(ncols)]

    def line(cells, pad_aligns):
        return "| " + " | ".join(_pad(c, widths[i], pad_aligns[i]) for i, c in enumerate(cells)) + " |"

    def delim(i):
        a, w = aligns[i], widths[i]
        if a == "center":
            return ":" + "-" * (w - 2) + ":"
        if a == "right":
            return "-" * (w - 1) + ":"
        if a == "left":
            return ":" + "-" * (w - 1)
        return "-" * w

    out = [line(header, [a if a else "left" for a in aligns])]
    out.append("| " + " | ".join(delim(i) for i in range(ncols)) + " |")
    out += [line(r, [a if a else "left" for a in aligns]) for r in rows]
    return out


_MD_INLINE = [
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"(\*\*|__)(.+?)\1"), r"\2"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"\1"),
    (re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])"), r"\1"),
    (re.compile(r"~~(.+?)~~"), r"\1"),
    (re.compile(r"(`+)(.+?)\1"), r"\2"),
]


def cell_text(cell: str) -> str:
    """Plain display text of a cell (inline Markdown markers removed)."""
    s = cell.replace("\\|", "|")
    for rx, rep in _MD_INLINE:
        s = rx.sub(rep, s)
    return s


_WHOLE = [
    ("bold", re.compile(r"^(\*\*|__)(?!\s).*(?<!\s)\1$")),
    ("strike", re.compile(r"^~~(?!\s).*(?<!\s)~~$")),
    ("code", re.compile(r"^`[^`].*`$|^`[^`]`$")),
    ("italic", re.compile(r"^(\*|_)(?![\s*_]).*(?<![\s*_])\1$")),
]


def cell_style(cell: str) -> frozenset:
    """Styles that wrap the *whole* cell (bold / italic / strike / code), used by
    the Live Preview grid; partial inline styling is shown as plain text."""
    s = cell.strip()
    out = set()
    for _ in range(3):              # peel nested wrappers like **~~x~~**
        for name, rx in _WHOLE:
            if name not in out and rx.match(s):
                out.add(name)
                k = 2 if name in ("bold", "strike") else 1
                s = s[k:-k].strip()
                break
        else:
            break
    return frozenset(out)
