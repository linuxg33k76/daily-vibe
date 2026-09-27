"""Plugin block markers inside entries.

    <!-- plugin:weather -->
    ## Weather
    ...
    <!-- /plugin:weather -->
"""
from __future__ import annotations

import re


def open_marker(name: str) -> str:
    return f"<!-- plugin:{name} -->"


def close_marker(name: str) -> str:
    return f"<!-- /plugin:{name} -->"


def wrap_block(name: str, body: str) -> str:
    # Blank lines around the body keep Markdown parsing of the content intact.
    return f"{open_marker(name)}\n\n{body.strip()}\n\n{close_marker(name)}"


def _block_re(name: str) -> re.Pattern:
    n = re.escape(name)
    return re.compile(rf"<!--\s*plugin:{n}\s*-->.*?<!--\s*/plugin:{n}\s*-->", re.S)


_ANY_CLOSE = re.compile(r"<!--\s*/plugin:[\w.-]+\s*-->")


def find_blocks(text: str) -> list[str]:
    return re.findall(r"<!--\s*plugin:([\w.-]+)\s*-->", text)


def insert_after_heading(text: str, block: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[: i + 1] + ["", block, ""] + lines[i + 1 :])
    return block + "\n\n" + text


def replace_block(text: str, name: str, body: str) -> str:
    """Replace the block for `name` if present, else insert it after the last
    existing plugin block, else after the first H1, else at the top."""
    block = wrap_block(name, body)
    pattern = _block_re(name)
    if pattern.search(text):
        return pattern.sub(lambda _m: block, text, count=1)
    closes = list(_ANY_CLOSE.finditer(text))
    if closes:
        end = closes[-1].end()
        return text[:end] + "\n\n" + block + text[end:]
    return insert_after_heading(text, block)


def remove_block(text: str, name: str) -> str:
    text = _block_re(name).sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def block_span(text: str, name: str) -> tuple[int, int] | None:
    """(start, end) string indices of the block for `name`, or None."""
    m = _block_re(name).search(text)
    return (m.start(), m.end()) if m else None


def utf16_len(s: str) -> int:
    """Length in UTF-16 code units (what QTextDocument positions count)."""
    return len(s.encode("utf-16-le")) // 2
