"""Turn X API v2 bookmark pages into small records, and records into plain Markdown.

A record (stored in the journal's seen-state file so past days re-render offline):

    {"id", "url", "author_name", "author_username", "created_at" (ISO, UTC),
     "text" (t.co links expanded, media links removed),
     "media": [{"type", "url", "alt", "local"}],   # local = relative link to the WebP or ""
     "quoted": {"id", "url", "author_name", "author_username", "text"} | None}
"""
from __future__ import annotations

import datetime as dt
import html
import re

_MEDIA_LINK_RE = re.compile(r"^https?://(?:x|twitter)\.com/[^/]+/status/\d+/(?:photo|video)/\d+", re.I)


def post_url(username: str, post_id: str) -> str:
    return f"https://x.com/{username or 'i'}/status/{post_id}" if username else f"https://x.com/i/status/{post_id}"


def expand_text(post: dict) -> str:
    """Full text (note_tweet for long posts) with t.co links replaced by
    `expanded_url`; links that only point at the post's own media are removed."""
    note = post.get("note_tweet") or {}
    text = note.get("text") or post.get("text") or ""
    urls = ((note.get("entities") or {}).get("urls") if note.get("text") else None) \
        or (post.get("entities") or {}).get("urls") or []
    # replace longest first so https://t.co/abc doesn't clobber https://t.co/abcd
    for u in sorted(urls, key=lambda u: -len(u.get("url") or "")):
        short = u.get("url")
        if not short:
            continue
        expanded = u.get("expanded_url") or ""
        if u.get("media_key") or _MEDIA_LINK_RE.match(expanded):
            text = text.replace(short, "")
        elif expanded:
            text = text.replace(short, expanded)
    text = html.unescape(text)  # v2 returns &amp; &lt; &gt;
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def normalize_page(page: dict) -> list[dict]:
    """Records for every post in `page['data']`, in API order (newest bookmark first)."""
    inc = page.get("includes") or {}
    users = {u.get("id"): u for u in inc.get("users") or []}
    media = {m.get("media_key"): m for m in inc.get("media") or []}
    tweets = {t.get("id"): t for t in inc.get("tweets") or []}
    out = []
    for p in page.get("data") or []:
        au = users.get(p.get("author_id"), {})
        rec = {
            "id": str(p["id"]),
            "author_name": au.get("name") or "",
            "author_username": au.get("username") or "",
            "created_at": p.get("created_at") or "",
            "text": expand_text(p),
            "media": [],
            "quoted": None,
        }
        rec["url"] = post_url(rec["author_username"], rec["id"])
        for key in (p.get("attachments") or {}).get("media_keys") or []:
            m = media.get(key)
            if not m:
                continue
            src = m.get("url") or m.get("preview_image_url")
            if src:
                rec["media"].append({"type": m.get("type", "photo"), "url": src,
                                     "alt": m.get("alt_text") or "", "local": ""})
        for ref in p.get("referenced_tweets") or []:
            if ref.get("type") == "quoted":
                q = tweets.get(ref.get("id")) or {}
                qa = users.get(q.get("author_id"), {})
                rec["quoted"] = {
                    "id": str(ref.get("id")),
                    "author_name": qa.get("name") or "",
                    "author_username": qa.get("username") or "",
                    "text": expand_text(q) if q else "",
                }
                rec["quoted"]["url"] = post_url(rec["quoted"]["author_username"], rec["quoted"]["id"])
        out.append(rec)
    return out


# Markdown ---------------------------------------------------------------------------
_BLOCK_START_RE = re.compile(r"^(\s*)([#>+\-*]|\d+[.)])(?=\s|$)")


def _escape_line(line: str) -> str:
    """Keep post text as text: no accidental headings/lists/quotes/HTML."""
    line = line.replace("<", "&lt;")
    return _BLOCK_START_RE.sub(lambda m: m.group(1) + "\\" + m.group(2), line)


def _date(iso: str) -> str:
    if not iso:
        return ""
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return t.astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return iso[:10]


def _who(name: str, username: str) -> str:
    name = (name or username or "unknown").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]")
    return f"**{name}** ([@{username}](https://x.com/{username}))" if username else f"**{name}**"


def render_post(rec: dict, show_quoted: bool = True, show_media: bool = True) -> str:
    date = _date(rec.get("created_at", ""))
    head = _who(rec.get("author_name", ""), rec.get("author_username", ""))
    head += f" · {date}" if date else ""
    head += f" · [View post]({rec['url']})"
    lines = [head, ""]
    body = [_escape_line(x) for x in (rec.get("text") or "").splitlines()] or [""]
    lines += [f"> {x}".rstrip() for x in body]
    q = rec.get("quoted")
    if show_quoted and q:
        qtext = " ".join((q.get("text") or "").split())
        if len(qtext) > 280:
            qtext = qtext[:277].rstrip() + "…"
        who = f"{q.get('author_name') or ''} (@{q['author_username']})" if q.get("author_username") else "a post"
        lines += [">", f"> ↪ Quoting {_escape_line(who).strip()}: {_escape_line(qtext)} · [link]({q['url']})".rstrip()]
    if show_media:
        for i, m in enumerate(rec.get("media") or [], 1):
            alt = " ".join((m.get("alt") or f"{m.get('type', 'image')} {i}").split())
            alt = alt.replace("[", "(").replace("]", ")")[:120]
            if m.get("local"):
                lines += ["", f"![{alt}]({m['local']})"]
            elif m.get("url"):
                lines += ["", f"[🖼 {alt}]({m['url']})"]
    return "\n".join(lines)


def render_posts(records: list[dict], show_quoted: bool = True, show_media: bool = True) -> str:
    return "\n\n".join(render_post(r, show_quoted, show_media) for r in records)
