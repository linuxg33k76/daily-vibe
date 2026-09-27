"""X Bookmarks: posts you newly bookmarked on X, added to the day's entry.

Uses the official X API v2 (``GET /2/users/:id/bookmarks``) with OAuth 2.0
Authorization Code + PKCE (user context; scopes ``tweet.read users.read
bookmark.read offline.access``). You bring your own X developer app: paste its
OAuth 2.0 Client ID in the settings, register the Callback URI shown there and
click **Connect X account**. X API access is paid (pay-per-use credits); see
README → "X Bookmarks" for current pricing notes and a step-by-step setup.

How days work: the API gives no "bookmarked at" time, so the plugin remembers
every post ID it has seen (per journal, ``<journal>/.dailyvibe/x_bookmarks.json``)
and only today's entry fetches; new posts are stored under today's date and
past days re-render from that file without network access.
"""
from __future__ import annotations

import datetime as dt
import threading

from daily_vibe import secrets
from daily_vibe.xapi import oauth
from daily_vibe.xapi.client import NotConnected, XClient
from daily_vibe.xapi.render import render_posts
from daily_vibe.xapi.sync import (FIRST_ASK, FIRST_LAST_N, FIRST_NONE, FIRST_RUN_CHOICES,
                                  stored_records, sync)
from daily_vibe.xapi.tokens import TokenStore

ID = "x_bookmarks"
NAME = "X Bookmarks"
TITLE = "X Bookmarks"
VERSION = "1.0.0"
AUTHOR = "The Daily Vibe"
API_VERSION = 1
DESCRIPTION = ("Posts you newly bookmarked on X, added to today's entry (official X API, "
               "OAuth 2.0 PKCE with your own developer app; X API usage is paid).")

_cancel = threading.Event()


def _store() -> TokenStore:
    return TokenStore()


def _client_secret(values: dict) -> str:
    if values.get("client_secret"):
        return values["client_secret"]
    return secrets.get_secret(ID, "client_secret", {})


# Settings actions (run in the background by Preferences) ---------------------------
def account_status(values: dict | None = None) -> str:
    store = _store()
    tok = store.load()
    if tok is None:
        return f"Not connected. {store.describe()}"
    who = f"@{tok.username}" if tok.username else f"user {tok.user_id}"
    exp = ""
    if tok.expires_at:
        exp = f" Access token valid until {dt.datetime.fromtimestamp(tok.expires_at):%Y-%m-%d %H:%M}" + \
              (" (refreshes automatically)." if tok.refresh_token else " (no refresh token: reconnect then).")
    return f"✓ Connected as {who}.{exp} {store.describe()}"


def connect(values: dict) -> str:
    _cancel.clear()
    client_id = (values.get("client_id") or "").strip()
    port = int(values.get("redirect_port") or oauth.DEFAULT_PORT)
    tok = oauth.authorize(client_id, port, _client_secret(values), cancel=_cancel)
    where = _store().save(tok)
    loc = "system keyring" if where == "keyring" else f"fallback file {_store().fallback_path} (0600)"
    return (f"✓ Connected as @{tok.username}. Tokens saved in the {loc}. "
            "Next: choose “First sync” below, then use Entry → Refresh Plugin Blocks.")


def disconnect(values: dict) -> str:
    _cancel.set()  # also aborts a sign-in that's still waiting for the browser
    store = _store()
    tok = store.load()
    if tok is None:
        return "Not connected."
    revoked = oauth.revoke(tok, (values.get("client_id") or tok.client_id).strip(), _client_secret(values))
    store.clear()
    return "Disconnected; tokens deleted" + (" and revoked at X." if revoked else
                                             " (X did not confirm the revocation; you can also remove the "
                                             "app under X → Settings → Security → Connected apps).")


SETTINGS = [
    {"key": "client_id", "label": "Client ID", "type": "string", "default": "",
     "help": "OAuth 2.0 Client ID of your own X developer app (console.x.com → your app → "
             "Keys and tokens). Use app type “Native App” (public client, PKCE, no secret)."},
    {"key": "client_secret", "label": "Client Secret (optional)", "type": "secret", "default": "",
     "help": "Only for confidential app types (Web App / Automated App). Leave empty for a Native App."},
    {"key": "redirect_port", "label": "Redirect port", "type": "int", "default": oauth.DEFAULT_PORT,
     "min": 1024, "max": 65535,
     "help": f"Register exactly http://127.0.0.1:{oauth.DEFAULT_PORT}/callback as a Callback URI in your "
             "app's User authentication settings (change the port here and there together)."},
    {"key": "account", "label": "X account", "type": "action", "status": account_status,
     "actions": [{"label": "Connect X account", "run": connect},
                 {"label": "Disconnect", "run": disconnect}],
     "help": "Connect opens your browser at x.com to approve read access to your bookmarks "
             "(scopes: tweet.read users.read bookmark.read offline.access)."},
    {"key": "first_run", "label": "First sync", "type": "choice", "choices": FIRST_RUN_CHOICES,
     "default": FIRST_ASK,
     "help": "What to do with the bookmarks you already have the first time this journal syncs. "
             "“Import none” only remembers them as seen; “Import last N” adds your N most recent to today. "
             "“Ask first” fetches nothing until you pick one."},
    {"key": "first_run_count", "label": "N (for Import last N)", "type": "int", "default": 10, "min": 1, "max": 100},
    {"key": "page_size", "label": "Posts per request", "type": "int", "default": 20, "min": 5, "max": 100,
     "help": "X bills per post returned, so smaller pages cost less when you bookmark little. "
             "Each sync reads at least one page."},
    {"key": "max_pages", "label": "Max pages per sync", "type": "int", "default": 3, "min": 1, "max": 20},
    {"key": "download_media", "label": "Save images", "type": "bool", "default": True,
     "help": "Download attached photos (video/GIF previews) as WebP into the journal's assets folder. "
             "Off: link to them on X's servers instead."},
    {"key": "show_quoted", "label": "Show quoted post", "type": "bool", "default": True},
]


def render(date, context) -> str:
    s = context.get("settings") or {}
    root = context["journal_root"]
    today = context.get("is_today", date == dt.date.today())
    if not today:
        # Past (or future) days: whatever was recorded then; never touches the network.
        recs = stored_records(root, date)
        return render_posts(recs, s.get("show_quoted", True)) if recs else "_No new bookmarks recorded for this day._"

    client = XClient(s.get("client_id", ""), _store(), _client_secret(s))
    entry_path = context.get("entry_path")
    entry_dir = entry_path.parent if entry_path is not None else root / f"{date:%Y}" / f"{date:%m}"
    try:
        res = sync(client, root, date,
                   first_run=s.get("first_run", FIRST_ASK), first_run_count=s.get("first_run_count", 10),
                   page_size=s.get("page_size", 20), max_pages=s.get("max_pages", 3),
                   download_media=s.get("download_media", True),
                   entry_dir=entry_dir, assets_dir=root / "assets" / f"{date:%Y}" / f"{date:%m}")
    except NotConnected:
        recs = stored_records(root, date)
        if recs:  # don't wipe what's already in today's block
            return render_posts(recs, s.get("show_quoted", True)) + \
                "\n\n_⚠️ X account not connected; new bookmarks weren't checked._"
        raise
    except Exception as exc:
        recs = stored_records(root, date)
        if recs:
            return render_posts(recs, s.get("show_quoted", True)) + \
                f"\n\n_⚠️ Couldn't check for new bookmarks: {exc}_"
        raise

    if res.first_run_pending:
        return ("_Connected. Before the first sync, choose what to do with the bookmarks you already have: "
                "Preferences → Plugins → X Bookmarks → **First sync** (“Import none” or “Import last N”), "
                "then Entry → Refresh Plugin Blocks. Nothing has been fetched yet._")
    parts = []
    if res.records:
        parts.append(render_posts(res.records, s.get("show_quoted", True)))
    elif res.first_run_done == FIRST_NONE:
        parts.append(f"_First sync: {res.baseline} existing bookmark(s) marked as seen. "
                     "Posts you bookmark from now on will show up here._")
    else:
        parts.append("_No new bookmarks today._")
    if res.first_run_done == FIRST_LAST_N and res.baseline:
        parts.append(f"_First sync: imported your {len(res.added)} most recent bookmark(s); "
                     f"{res.baseline} older one(s) marked as seen._")
    parts += [f"_⚠️ {n}_" for n in res.notes]
    return "\n\n".join(parts)
