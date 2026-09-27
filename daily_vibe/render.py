"""Markdown -> HTML for the preview pane."""
from __future__ import annotations

import html
import re
from pathlib import Path

import markdown

CSS = """
body { font-family: sans-serif; font-size: 14px; line-height: 1.5; color: #222; }
h1 { font-size: 24px; border-bottom: 1px solid #ddd; }
h2 { font-size: 18px; color: #334; margin-top: 18px; }
code { background: #f2f2f2; font-family: monospace; }
pre { background: #f6f6f6; padding: 8px; }
blockquote { color: #8a4b00; background: #fff6e5; margin-left: 0; padding: 4px 10px; }
table { border-collapse: collapse; }
td, th { border: 1px solid #ccc; padding: 4px 8px; }
th { background-color: #eef0f3; }
del { text-decoration: line-through; }
a.tag { color: #ffffff; background-color: #6c5ce7; text-decoration: none; }
"""

_IMG_RE = re.compile(r'<img([^>]*?)src="([^"]+)"([^>]*)>')


def markdown_to_html(text: str, base_dir: Path | None = None, max_img_width: int = 700, css: str | None = None,
                     tag_chips: bool = True, stripe_bg: str = "#f4f5f7") -> str:
    if tag_chips:  # hide front matter, render #tags as clickable chips (tag: links)
        from daily_vibe.tags import tags_to_html_markdown
        text = tags_to_html_markdown(text, link=True)
    from daily_vibe import md_ext
    body = markdown.markdown(md_ext.normalize_lists(text), extensions=md_ext.extensions("toc"), output_format="html")
    body = md_ext.stripe_rows(md_ext.fix_tables(body), stripe_bg)

    def fix_img(m: re.Match) -> str:
        before, src, after = m.group(1), html.unescape(m.group(2)), m.group(3)
        path = Path(src)
        if base_dir is not None and not re.match(r"^[a-z]+://", src) and not path.is_absolute():
            path = (base_dir / src).resolve()
        width_attr = ""
        if path.is_file():
            try:
                from PIL import Image
                with Image.open(path) as im:
                    w = im.width
                width_attr = f' width="{min(w, max_img_width)}"'
            except Exception:
                pass
            src = path.as_uri()
        return f'<img{before}src="{html.escape(src)}"{width_attr}{after}>'

    body = _IMG_RE.sub(fix_img, body)
    return f"<html><head><style>{css or CSS}</style></head><body>{body}</body></html>"
