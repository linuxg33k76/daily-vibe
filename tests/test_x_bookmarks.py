"""X Bookmarks plugin (round 8). All HTTP is mocked: no network access."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import stat
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from daily_vibe import secrets
from daily_vibe.xapi import net, oauth
from daily_vibe.xapi.client import NotConnected, RateLimited, XApiError, XClient
from daily_vibe.xapi.render import expand_text, normalize_page, render_post, render_posts
from daily_vibe.xapi.sync import FIRST_ASK, FIRST_LAST_N, FIRST_NONE, SeenState, state_path, sync
from daily_vibe.xapi.tokens import TokenStore

TODAY = dt.date(2026, 9, 26)
NOW = 1_790_000_000.0


# Fakes -------------------------------------------------------------------------------
class FakeTransport:
    """Routes (method, url-prefix) to a list of responses (served in order;
    the last one repeats). Records every call."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[dict] = []

    def add(self, method, prefix, *responses):
        self.routes.setdefault((method, prefix), []).extend(responses)
        return self

    def __call__(self, method, url, *, params=None, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": dict(params or {}),
                           "data": dict(data or {}), "headers": dict(headers or {})})
        best = None
        for (m, prefix), resps in self.routes.items():
            if m == method and url.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, resps)
        if best is None:
            raise AssertionError(f"unexpected request {method} {url}")
        resps = best[1]
        r = resps.pop(0) if len(resps) > 1 else resps[0]
        return r(self.calls[-1]) if callable(r) else r

    def of(self, method, prefix):
        return [c for c in self.calls if c["method"] == method and c["url"].startswith(prefix)]


def J(status=200, body=None, headers=None):
    return net.Response(status, {k.lower(): v for k, v in (headers or {}).items()},
                        json.dumps(body if body is not None else {}).encode())


def tok(**kw):
    base = dict(access_token="AT1", refresh_token="RT1", expires_at=NOW + 3600, client_id="cid",
                user_id="42", username="me", name="Me", obtained_at=NOW)
    base.update(kw)
    return oauth.Token(**base)


def store_with(tmp_path, token=None):
    st = TokenStore(tmp_path / "tokens.json")
    if token:
        st.save(token)
    return st


def post(pid, text=None, author="1", **extra):
    d = {"id": str(pid), "text": text or f"post {pid}", "author_id": author,
         "created_at": "2026-09-20T15:04:05.000Z"}
    d.update(extra)
    return d


def page(ids, next_token=None, **inc):
    body = {"data": [post(i) for i in ids],
            "includes": {"users": [{"id": "1", "name": "Ada Example", "username": "ada_example"}], **inc},
            "meta": {"result_count": len(ids)}}
    if next_token:
        body["meta"]["next_token"] = next_token
    return body


BM = "https://api.x.com/2/users/42/bookmarks"


def client_for(tmp_path, transport, token=None, **kw):
    return XClient("cid", store_with(tmp_path, token or tok()), transport=transport,
                   clock=lambda: NOW, sleep=kw.pop("sleep", lambda s: None), **kw)


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch):
    monkeypatch.setattr(secrets, "_keyring", lambda: None)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted in a test")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


# PKCE / authorize URL -------------------------------------------------------------------
def test_pkce_verifier_and_challenge():
    v = oauth.make_code_verifier()
    assert 43 <= len(v) <= 128
    assert set(v) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    assert oauth.make_code_verifier() != v
    # RFC 7636 appendix B test vector
    assert oauth.code_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == \
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    exp = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert oauth.code_challenge(v) == exp


def test_authorize_url_params():
    url = oauth.build_authorize_url("my-client", oauth.redirect_uri(8765), "st4te", "chal")
    parsed = urllib.parse.urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://x.com/i/oauth2/authorize"
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert q == {"response_type": "code", "client_id": "my-client",
                 "redirect_uri": "http://127.0.0.1:8765/callback",
                 "scope": "tweet.read users.read bookmark.read offline.access",
                 "state": "st4te", "code_challenge": "chal", "code_challenge_method": "S256"}
    assert "%20" in parsed.query and "+" not in parsed.query


def test_full_authorize_flow_with_loopback(tmp_path):
    """Browser is simulated by hitting the loopback server with the redirect."""
    t = FakeTransport()
    t.add("POST", oauth.TOKEN_URL, J(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 7200,
                                           "scope": "tweet.read users.read bookmark.read offline.access"}))
    t.add("GET", oauth.ME_URL, J(200, {"data": {"id": "42", "username": "me", "name": "Me"}}))
    seen = {}

    def fake_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        seen.update(q)

        def hit():
            import http.client
            port = int(urllib.parse.urlparse(q["redirect_uri"]).port)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", f"/callback?state={q['state']}&code=CODE123")
            seen["page"] = conn.getresponse().read().decode()
        threading.Thread(target=hit, daemon=True).start()
        return True

    token = oauth.authorize("cid", 0, open_browser=fake_browser, timeout=10, transport=t)
    assert token.access_token == "AT" and token.refresh_token == "RT"
    assert token.username == "me" and token.user_id == "42"
    exch = t.of("POST", oauth.TOKEN_URL)[0]["data"]
    assert exch["grant_type"] == "authorization_code" and exch["code"] == "CODE123"
    assert exch["client_id"] == "cid" and exch["redirect_uri"] == seen["redirect_uri"]
    assert oauth.code_challenge(exch["code_verifier"]) == seen["code_challenge"]
    assert "Connected to X" in seen["page"]


def test_authorize_rejects_state_mismatch():
    def fake_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))

        def hit():
            import http.client
            port = urllib.parse.urlparse(q["redirect_uri"]).port
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/callback?state=evil&code=X")
            conn.getresponse().read()
        threading.Thread(target=hit, daemon=True).start()
        return True

    with pytest.raises(oauth.OAuthError, match="state_mismatch"):
        oauth.authorize("cid", 0, open_browser=fake_browser, timeout=10, transport=FakeTransport())


def test_authorize_needs_client_id():
    with pytest.raises(oauth.OAuthError, match="Client ID"):
        oauth.authorize("  ", 0, open_browser=lambda u: True)


# Token storage --------------------------------------------------------------------------
def test_fallback_token_file_is_0600_and_labeled(tmp_path):
    st = TokenStore(tmp_path / "t.json")
    assert st.backend() == "file" and "plain text" in st.describe()
    assert st.save(tok()) == "file"
    mode = stat.S_IMODE(os.stat(tmp_path / "t.json").st_mode)
    assert mode == 0o600
    raw = json.loads((tmp_path / "t.json").read_text())
    assert "PLAIN-TEXT" in raw["_warning"]
    assert st.load().refresh_token == "RT1"
    st.clear()
    assert st.load() is None and not (tmp_path / "t.json").exists()


def test_keyring_backend_preferred(tmp_path, monkeypatch):
    class KR:
        def __init__(self):
            self.d = {}

        def get_password(self, s, u):
            return self.d.get((s, u))

        def set_password(self, s, u, v):
            self.d[(s, u)] = v

        def delete_password(self, s, u):
            self.d.pop((s, u), None)
    kr = KR()
    monkeypatch.setattr(secrets, "_keyring", lambda: kr)
    st = TokenStore(tmp_path / "t.json")
    assert st.save(tok()) == "keyring"
    assert not (tmp_path / "t.json").exists()
    assert ("daily-vibe", "plugin:x_bookmarks:oauth") in kr.d
    assert st.load().access_token == "AT1"
    st.clear()
    assert kr.d == {}


# Refresh ------------------------------------------------------------------------------------
def test_expired_token_is_refreshed_and_rotated_token_saved(tmp_path):
    t = FakeTransport()
    t.add("POST", oauth.TOKEN_URL, J(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200}))
    t.add("GET", BM, J(200, page([])))
    c = client_for(tmp_path, t, tok(expires_at=NOW - 10))
    list(c.bookmark_pages("42"))
    body = t.of("POST", oauth.TOKEN_URL)[0]["data"]
    assert body == {"grant_type": "refresh_token", "refresh_token": "RT1", "client_id": "cid"}
    assert t.of("GET", BM)[0]["headers"]["Authorization"] == "Bearer AT2"
    saved = c.store.load()
    assert (saved.access_token, saved.refresh_token, saved.username) == ("AT2", "RT2", "me")
    assert saved.expires_at == NOW + 7200


def test_401_triggers_one_refresh_and_retry(tmp_path):
    t = FakeTransport()
    t.add("GET", BM, J(401, {"title": "Unauthorized"}), J(200, page(["1"])))
    t.add("POST", oauth.TOKEN_URL, J(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200}))
    c = client_for(tmp_path, t)
    pages = list(c.bookmark_pages("42"))
    assert pages[0]["data"][0]["id"] == "1"
    assert [call["headers"]["Authorization"] for call in t.of("GET", BM)] == ["Bearer AT1", "Bearer AT2"]


def test_failed_refresh_asks_to_reconnect(tmp_path):
    t = FakeTransport().add("POST", oauth.TOKEN_URL, J(400, {"error": "invalid_request",
                                                           "error_description": "Value passed for the token was invalid."}))
    c = client_for(tmp_path, t, tok(expires_at=NOW - 1))
    with pytest.raises(NotConnected, match="Reconnect"):
        c.token()


def test_confidential_client_uses_basic_auth(tmp_path):
    t = FakeTransport().add("POST", oauth.TOKEN_URL, J(200, {"access_token": "A", "expires_in": 60}))
    new = oauth.refresh_token(tok(), "cid", "sec", t, now=NOW)
    h = t.calls[0]["headers"]["Authorization"]
    assert h == "Basic " + base64.b64encode(b"cid:sec").decode()
    assert new.refresh_token == "RT1"  # kept when X doesn't send a new one


def test_not_connected(tmp_path):
    c = XClient("cid", TokenStore(tmp_path / "none.json"), transport=FakeTransport())
    with pytest.raises(NotConnected, match="Connect X account"):
        c.token()


# Pagination / rate limits ------------------------------------------------------------------
def test_pagination_follows_next_token_and_params(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["5", "4"], "T2")), J(200, page(["3", "2"], "T3")),
                            J(200, page(["1"])))
    c = client_for(tmp_path, t)
    pages = list(c.bookmark_pages("42", page_size=2, max_pages=10))
    assert len(pages) == 3
    calls = t.of("GET", BM)
    assert "pagination_token" not in calls[0]["params"]
    assert [c["params"].get("pagination_token") for c in calls[1:]] == ["T2", "T3"]
    p0 = calls[0]["params"]
    assert p0["max_results"] == 2
    assert "note_tweet" in p0["tweet.fields"] and "entities" in p0["tweet.fields"]
    assert "referenced_tweets.id.author_id" in p0["expansions"]
    # max_pages caps the walk
    t2 = FakeTransport().add("GET", BM, J(200, page(["5"], "T2")))
    assert len(list(client_for(tmp_path, t2).bookmark_pages("42", max_pages=2))) == 2


def test_429_short_wait_then_retry(tmp_path):
    slept = []
    t = FakeTransport().add("GET", BM, J(429, {"title": "Too Many Requests"}, {"x-rate-limit-reset": str(int(NOW + 5))}),
                            J(200, page(["1"])))
    c = client_for(tmp_path, t, sleep=slept.append)
    assert list(c.bookmark_pages("42"))[0]["data"][0]["id"] == "1"
    assert slept and 5 <= slept[0] <= 7


def test_429_long_wait_raises_with_reset_time(tmp_path):
    t = FakeTransport().add("GET", BM, J(429, {}, {"x-rate-limit-reset": str(int(NOW + 900))}))
    c = client_for(tmp_path, t)
    with pytest.raises(RateLimited) as ei:
        list(c.bookmark_pages("42"))
    assert ei.value.reset_at == NOW + 900
    assert "rate limit" in str(ei.value) and dt.datetime.fromtimestamp(NOW + 900).strftime("%H:%M") in str(ei.value)


def test_402_credits_message(tmp_path):
    t = FakeTransport().add("GET", BM, J(402, {"title": "CreditsDepleted", "detail": "Your account has no credits."}))
    with pytest.raises(XApiError, match="credits"):
        list(client_for(tmp_path, t).bookmark_pages("42"))


# Sync / dedupe / first run ---------------------------------------------------------------------
def run_sync(tmp_path, t, **kw):
    c = client_for(tmp_path, t)
    root = tmp_path / "Journal"
    return sync(c, root, kw.pop("date", TODAY), download_media=False, **kw), root


def test_first_run_ask_fetches_nothing(tmp_path):
    t = FakeTransport()
    res, root = run_sync(tmp_path, t, first_run=FIRST_ASK)
    assert res.first_run_pending and not t.calls
    assert not state_path(root).exists()


def test_first_run_import_none_marks_seen(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["9", "8", "7"], "T2")))
    res, root = run_sync(tmp_path, t, first_run=FIRST_NONE, page_size=20)
    assert res.added == [] and res.baseline == 3 and res.first_run_done == FIRST_NONE
    assert len(t.of("GET", BM)) == 1  # only one page for the baseline
    acc = SeenState(root).account("42")
    assert acc["initialized"] and acc["seen"] == ["7", "8", "9"]


def test_first_run_import_last_n(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["9", "8", "7", "6", "5"])))
    res, root = run_sync(tmp_path, t, first_run=FIRST_LAST_N, first_run_count=2, page_size=20)
    assert [r["id"] for r in res.added] == ["8", "9"]  # oldest first
    assert res.baseline == 3
    assert [r["id"] for r in SeenState(root).records_for("42", TODAY)] == ["8", "9"]


def test_only_new_bookmarks_are_added_and_dedupe(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["3", "2", "1"])))
    run_sync(tmp_path, t, first_run=FIRST_NONE)
    # next day: two new ones on top; stops at the first page with a seen ID
    t2 = FakeTransport().add("GET", BM, J(200, page(["5", "4", "3"], "T2")), J(200, page(["2", "1"])))
    res, root = run_sync(tmp_path, t2, date=TODAY + dt.timedelta(days=1))
    assert [r["id"] for r in res.added] == ["4", "5"]
    assert len(t2.of("GET", BM)) == 1
    # same day again: nothing new, stored records still rendered
    t3 = FakeTransport().add("GET", BM, J(200, page(["5", "4", "3"])))
    res3, _ = run_sync(tmp_path, t3, date=TODAY + dt.timedelta(days=1))
    assert res3.added == [] and [r["id"] for r in res3.records] == ["4", "5"]
    # the earlier day is untouched
    assert SeenState(root).records_for("42", TODAY) == []


def test_pages_until_seen_and_gap_handling(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["1"])))
    run_sync(tmp_path, t, first_run=FIRST_NONE)
    # 4 new posts, page size 2, max 1 page -> stops early, gap flagged
    t2 = FakeTransport().add("GET", BM, J(200, page(["5", "4"], "T2")))
    res, root = run_sync(tmp_path, t2, max_pages=1, page_size=2)
    assert [r["id"] for r in res.added] == ["4", "5"] and res.notes
    assert set(SeenState(root).account("42")["gap_ids"]) == {"4", "5"}
    # next sync looks past the gap posts and finds 3 and 2, then reaches 1
    t3 = FakeTransport().add("GET", BM, J(200, page(["5", "4"], "T2")), J(200, page(["3", "2"], "T3")),
                             J(200, page(["1"])))
    res3, _ = run_sync(tmp_path, t3, max_pages=5, page_size=2)
    assert [r["id"] for r in res3.added] == ["2", "3"]
    assert SeenState(root).account("42")["gap_ids"] == []


def test_rate_limit_mid_pagination_keeps_what_was_found(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["1"])))
    run_sync(tmp_path, t, first_run=FIRST_NONE)
    t2 = FakeTransport().add("GET", BM, J(200, page(["3"], "T2")),
                             J(429, {}, {"x-rate-limit-reset": str(int(NOW + 900))}))
    res, _ = run_sync(tmp_path, t2, max_pages=3)
    assert [r["id"] for r in res.added] == ["3"]
    assert any("rate limit" in n for n in res.notes)


def test_seen_state_is_per_journal(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["2", "1"])))
    c = client_for(tmp_path, t)
    sync(c, tmp_path / "A", TODAY, first_run=FIRST_LAST_N, first_run_count=5, download_media=False)
    res = sync(c, tmp_path / "B", TODAY, first_run=FIRST_LAST_N, first_run_count=5, download_media=False)
    assert [r["id"] for r in res.added] == ["1", "2"]
    assert (tmp_path / "A" / ".dailyvibe" / "x_bookmarks.json").is_file()


# Rendering ---------------------------------------------------------------------------------
RICH = {
    "data": [{
        "id": "100", "author_id": "1", "created_at": "2026-09-25T18:30:00.000Z",
        "text": "Read this &amp; that https://t.co/aaa #python\n# not a heading https://t.co/pic",
        "entities": {"urls": [
            {"url": "https://t.co/aaa", "expanded_url": "https://example.com/article", "display_url": "example.com/article"},
            {"url": "https://t.co/pic", "expanded_url": "https://x.com/ada_example/status/100/photo/1",
             "media_key": "3_1"}]},
        "attachments": {"media_keys": ["3_1"]},
        "referenced_tweets": [{"type": "quoted", "id": "90"}],
    }],
    "includes": {
        "users": [{"id": "1", "name": "Ada Example", "username": "ada_example"},
                  {"id": "2", "name": "Bob Sample", "username": "bob_sample"}],
        "media": [{"media_key": "3_1", "type": "photo", "url": "https://pbs.twimg.com/media/abc.jpg",
                   "alt_text": "A chart"}],
        "tweets": [{"id": "90", "author_id": "2", "text": "Original thought https://t.co/q",
                    "entities": {"urls": [{"url": "https://t.co/q", "expanded_url": "https://example.org/q"}]}}],
    },
}


def test_expand_text_and_long_posts():
    rec = normalize_page(RICH)[0]
    assert rec["text"] == "Read this & that https://example.com/article #python\n# not a heading"
    note = {"text": "short https://t.co/x", "note_tweet": {"text": "long version https://t.co/y",
            "entities": {"urls": [{"url": "https://t.co/y", "expanded_url": "https://long.example"}]}}}
    assert expand_text(note) == "long version https://long.example"


def test_markdown_rendering():
    rec = normalize_page(RICH)[0]
    assert rec["url"] == "https://x.com/ada_example/status/100"
    assert rec["quoted"]["author_username"] == "bob_sample"
    md = render_post(rec)
    lines = md.splitlines()
    assert lines[0].startswith("**Ada Example** ([@ada_example](https://x.com/ada_example)) · 2026-09-")
    assert lines[0].endswith("[View post](https://x.com/ada_example/status/100)")
    assert "> Read this & that https://example.com/article #python" in md
    assert "> \\# not a heading" in md  # no accidental heading inside the block
    assert "> ↪ Quoting Bob Sample (@bob_sample): Original thought https://example.org/q · " \
           "[link](https://x.com/bob_sample/status/90)" in md
    assert "[🖼 A chart](https://pbs.twimg.com/media/abc.jpg)" in md  # not downloaded -> link
    assert "↪" not in render_post(rec, show_quoted=False)
    assert "<!--" not in render_posts([dict(rec, text="<!-- /plugin:x_bookmarks -->")])


def _jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (64, 40), (200, 50, 50)).save(buf, "JPEG")
    return buf.getvalue()


def test_media_saved_as_webp_with_relative_link(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["1"])))
    run_sync(tmp_path, t, first_run=FIRST_NONE)
    t2 = FakeTransport().add("GET", BM, J(200, RICH))
    t2.add("GET", "https://pbs.twimg.com/media/abc.jpg", net.Response(200, {"content-type": "image/jpeg"}, _jpeg_bytes()))
    c = client_for(tmp_path, t2)
    root = tmp_path / "Journal"
    entry_dir = root / "2026" / "09"
    assets = root / "assets" / "2026" / "09"
    res = sync(c, root, TODAY, download_media=True, entry_dir=entry_dir, assets_dir=assets)
    out = assets / "x-100-1.webp"
    assert out.is_file()
    with Image.open(out) as im:
        assert im.format == "WEBP" and im.size == (64, 40)
    assert res.added[0]["media"][0]["local"] == "../../assets/2026/09/x-100-1.webp"
    assert "name=large" in t2.of("GET", "https://pbs.twimg.com")[0]["url"]
    assert "![A chart](../../assets/2026/09/x-100-1.webp)" in render_posts(res.records)


def test_media_download_failure_keeps_link(tmp_path):
    t = FakeTransport().add("GET", BM, J(200, page(["1"])))
    run_sync(tmp_path, t, first_run=FIRST_NONE)
    t2 = FakeTransport().add("GET", BM, J(200, RICH)).add("GET", "https://pbs.twimg.com", net.Response(404))
    c = client_for(tmp_path, t2)
    root = tmp_path / "Journal"
    res = sync(c, root, TODAY, download_media=True, entry_dir=root / "2026/09", assets_dir=root / "assets/2026/09")
    assert "[🖼 A chart](https://pbs.twimg.com/media/abc.jpg)" in render_posts(res.records)


# Plugin through the PluginManager -------------------------------------------------------------
def _pm_and_journal(tmp_path, settings=None):
    from daily_vibe.config import Config
    from daily_vibe.plugin_manager import PluginManager
    from daily_vibe.storage import Journal
    cfg = Config({"plugins": {"enabled": ["x_bookmarks"],
                              "x_bookmarks": {"client_id": "cid", "download_media": False, **(settings or {})}}})
    return PluginManager(cfg, use_entry_points=False), Journal(tmp_path / "J")


def test_plugin_settings_schema_and_actions(tmp_path):
    pm, _ = _pm_and_journal(tmp_path)
    p = pm.plugins["x_bookmarks"]
    assert p.implemented and p.version == "1.0.0"
    kinds = {f.key: f.type for f in p.settings}
    assert kinds["account"] == "action" and kinds["client_id"] == "string"
    labels = [a["label"] for a in next(f for f in p.settings if f.key == "account").actions]
    assert labels == ["Connect X account", "Disconnect"]
    assert "account" not in pm.settings_for(p)


def test_plugin_not_connected_is_a_clear_failure(tmp_path):
    pm, j = _pm_and_journal(tmp_path)
    r = pm.run_plugin(pm.plugins["x_bookmarks"], dt.date.today(), j)
    assert not r.ok and "Connect X account" in r.markdown


def test_plugin_end_to_end(tmp_path, monkeypatch):
    mod = __import__("daily_vibe.xapi.client", fromlist=["x"])
    t = FakeTransport().add("GET", "https://api.x.com/2/users/42/bookmarks", J(200, page(["2", "1"])))
    monkeypatch.setattr(net, "request", t)
    TokenStore().save(tok(expires_at=9e12))
    pm, j = _pm_and_journal(tmp_path, {"first_run": FIRST_LAST_N, "first_run_count": 1})
    plugin = pm.plugins["x_bookmarks"]
    today = dt.date.today()
    r = pm.run_plugin(plugin, today, j)
    assert r.ok, r.markdown
    assert r.markdown.startswith("## X Bookmarks") and "post 2" in r.markdown and "post 1" not in r.markdown
    # refresh later with nothing new: the stored post stays
    r2 = pm.run_plugin(plugin, today, j)
    assert "post 2" in r2.markdown
    # a past day renders offline
    n = len(t.calls)
    r3 = pm.run_plugin(plugin, today - dt.timedelta(days=3), j)
    assert "No new bookmarks recorded" in r3.markdown and len(t.calls) == n
    # API failure later today keeps what's stored + a warning line
    t.routes.clear()
    t.add("GET", "https://api.x.com/2/users/42/bookmarks", J(503, {"title": "Service Unavailable"}))
    r4 = pm.run_plugin(plugin, today, j)
    assert r4.ok and "post 2" in r4.markdown and "Couldn't check" in r4.markdown
    assert mod  # imported fine


def test_plugin_first_run_ask_message(tmp_path, monkeypatch):
    t = FakeTransport()
    monkeypatch.setattr(net, "request", t)
    TokenStore().save(tok(expires_at=9e12))
    pm, j = _pm_and_journal(tmp_path)
    r = pm.run_plugin(pm.plugins["x_bookmarks"], dt.date.today(), j)
    assert r.ok and "First sync" in r.markdown and not t.calls


def test_disconnect_revokes_and_clears(tmp_path, monkeypatch):
    t = FakeTransport().add("POST", oauth.REVOKE_URL, J(200, {"revoked": True}))
    monkeypatch.setattr(net, "request", t)
    TokenStore().save(tok())
    pm, _ = _pm_and_journal(tmp_path)
    mod = pm.plugins["x_bookmarks"].module
    assert "Connected as @me" in mod.account_status({})
    msg = mod.disconnect({"client_id": "cid"})
    assert "revoked" in msg and TokenStore().load() is None
    assert {c["data"]["token_type_hint"] for c in t.calls} == {"refresh_token", "access_token"}
    assert mod.account_status({}).startswith("Not connected")


# Settings UI --------------------------------------------------------------------------------
def test_settings_page_action_buttons(qapp, tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import wait_until
    from daily_vibe.ui.plugin_settings import PluginsPage
    from daily_vibe.workers import TaskRunner
    from PySide6.QtWidgets import QPushButton
    pm, _ = _pm_and_journal(tmp_path)
    mod = pm.plugins["x_bookmarks"].module
    got = {}
    monkeypatch.setattr(mod.oauth, "authorize", lambda cid, port, sec, **k: (got.update(cid=cid, port=port), tok())[1])
    page_ = PluginsPage(pm, TaskRunner())
    page_.load({"plugins": {"enabled": ["x_bookmarks"], "x_bookmarks": {"client_id": "cid"}}})
    f, status, buttons = page_.action_rows[("x_bookmarks", "account")]
    assert [b.text() for b in buttons] == ["Connect X account", "Disconnect"]
    assert "Not connected" in status.text()
    page_.field("x_bookmarks", "client_id").setText("typed-id")
    buttons[0].click()
    assert wait_until(qapp, lambda: "Connected as @me" in status.text())
    assert got == {"cid": "typed-id", "port": 8765}
    # action rows are not saved as settings
    data = page_.collect_into({"plugins": {}}, write_secrets=False)
    assert "account" not in data["plugins"]["x_bookmarks"]
    assert page_.findChildren(QPushButton, "action_x_bookmarks_account_1")
