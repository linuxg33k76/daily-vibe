"""Authenticated X API v2 client for the bookmarks endpoint.

* refreshes the access token automatically (``offline.access``), before expiry
  and once more on a 401; refreshed (rotated) tokens are saved immediately
* ``GET /2/users/:id/bookmarks`` with ``pagination_token`` paging
* HTTP 429: waits for ``x-rate-limit-reset`` (or ``retry-after``) if that's at
  most ``max_wait`` seconds away, otherwise raises :class:`RateLimited`
* other errors become :class:`XApiError` with X's own ``title``/``detail`` text
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Callable, Iterator

from daily_vibe.xapi import net, oauth
from daily_vibe.xapi.tokens import TokenStore

API = "https://api.x.com/2"
BOOKMARK_PARAMS = {
    "tweet.fields": "created_at,author_id,entities,note_tweet,attachments,referenced_tweets",
    "expansions": "author_id,attachments.media_keys,referenced_tweets.id,referenced_tweets.id.author_id",
    "user.fields": "name,username",
    "media.fields": "type,url,preview_image_url,alt_text,width,height",
}
_refresh_lock = threading.Lock()  # one refresh at a time (refresh tokens are single-use)


class XApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotConnected(XApiError):
    pass


class RateLimited(XApiError):
    def __init__(self, reset_at: float | None, message: str | None = None):
        when = ""
        if reset_at:
            when = f" after {dt.datetime.fromtimestamp(reset_at):%H:%M}"
        super().__init__(message or f"X API rate limit reached; try Refresh Plugin Blocks again{when}", 429)
        self.reset_at = reset_at


def _problem_text(resp: net.Response) -> str:
    p = resp.json()
    parts = [p.get("title"), p.get("detail")]
    if not any(parts) and isinstance(p.get("errors"), list) and p["errors"]:
        e = p["errors"][0]
        parts = [e.get("title"), e.get("detail") or e.get("message")]
    text = ": ".join(str(x) for x in parts if x)
    return text or f"HTTP {resp.status}"


class XClient:
    def __init__(self, client_id: str, store: TokenStore | None = None, client_secret: str = "",
                 transport: net.Transport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, max_wait: float = 30.0):
        self.client_id = (client_id or "").strip()
        self.client_secret = client_secret or ""
        self.store = store or TokenStore()
        self.transport = transport
        self.sleep, self.clock, self.max_wait = sleep, clock, max_wait
        self.last_rate: dict = {}  # x-rate-limit-* of the last response

    def _send(self, method, url, **kw) -> net.Response:
        return (self.transport or net.request)(method, url, **kw)

    # Tokens -----------------------------------------------------------------------
    def token(self) -> oauth.Token:
        tok = self.store.load()
        if tok is None or not tok.access_token:
            raise NotConnected("X account not connected: open Preferences → Plugins → X Bookmarks "
                               "and click “Connect X account”")
        if tok.client_id and self.client_id and tok.client_id != self.client_id:
            raise NotConnected("the Client ID changed since you connected; click “Connect X account” again")
        if tok.expired(self.clock()):
            tok = self.refresh(tok)
        return tok

    def refresh(self, stale: oauth.Token) -> oauth.Token:
        with _refresh_lock:
            current = self.store.load() or stale
            if current.access_token != stale.access_token and not current.expired(self.clock()):
                return current  # another thread refreshed already
            try:
                new = oauth.refresh_token(current, self.client_id or current.client_id, self.client_secret,
                                          self._send, now=self.clock())
            except oauth.OAuthError as exc:
                raise NotConnected(f"{exc}. Reconnect in Preferences → Plugins → X Bookmarks") from exc
            self.store.save(new)
            return new

    # Requests ---------------------------------------------------------------------
    def get(self, path: str, params: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{API}{path}"
        tok = self.token()
        refreshed = waited = False
        while True:
            resp = self._send("GET", url, params=params, headers={"Authorization": f"Bearer {tok.access_token}"})
            self.last_rate = {k: v for k, v in resp.headers.items() if k.startswith("x-rate-limit")}
            if resp.ok:
                return resp.json()
            if resp.status == 401 and not refreshed:
                refreshed = True
                tok = self.refresh(tok)
                continue
            if resp.status == 429:
                reset_at = self._reset_at(resp)
                wait = (reset_at - self.clock()) if reset_at else None
                if not waited and wait is not None and wait <= self.max_wait:
                    waited = True
                    self.sleep(max(0.0, wait) + 1.0)
                    continue
                raise RateLimited(reset_at)
            if resp.status == 401:
                raise NotConnected(f"X rejected the stored token ({_problem_text(resp)}); reconnect your account", 401)
            if resp.status in (402, 403):
                raise XApiError(f"X API refused access ({_problem_text(resp)}). Check that your app has "
                                "API credits / an active plan in the X Developer Console", resp.status)
            raise XApiError(f"X API error {resp.status}: {_problem_text(resp)}", resp.status)

    def _reset_at(self, resp: net.Response) -> float | None:
        h = resp.headers
        try:
            if h.get("x-rate-limit-reset"):
                return float(h["x-rate-limit-reset"])
            if h.get("retry-after"):
                return self.clock() + float(h["retry-after"])
        except ValueError:
            pass
        return None

    def rate_exhausted(self) -> bool:
        return str(self.last_rate.get("x-rate-limit-remaining", "1")) == "0"

    def me(self) -> dict:
        return self.get("/users/me", {"user.fields": "name,username"}).get("data") or {}

    def bookmark_pages(self, user_id: str, page_size: int = 20, max_pages: int = 3) -> Iterator[dict]:
        """Yield raw pages (dict with data/includes/meta), newest bookmarks first."""
        token = None
        for _ in range(max(1, max_pages)):
            params = dict(BOOKMARK_PARAMS, max_results=max(1, min(100, int(page_size))))
            if token:
                params["pagination_token"] = token
            page = self.get(f"/users/{user_id}/bookmarks", params)
            yield page
            token = (page.get("meta") or {}).get("next_token")
            if not token:
                return

    def download(self, url: str) -> bytes:
        """Fetch a media file (pbs.twimg.com URLs from the API's media objects)."""
        resp = self._send("GET", url, headers={"Accept": "image/*"})
        if not resp.ok or not resp.body:
            raise XApiError(f"media download failed: HTTP {resp.status}", resp.status)
        return resp.body
