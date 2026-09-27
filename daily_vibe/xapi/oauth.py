"""OAuth 2.0 Authorization Code flow with PKCE for X (public "Native App" client).

Flow (see https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code):

1. make a random ``code_verifier`` and its S256 ``code_challenge`` plus a random ``state``
2. start a one-shot HTTP server on ``http://127.0.0.1:<port>/callback`` (the exact
   URL must be registered as a Callback URI in the X Developer Console; X asks
   for ``127.0.0.1``, not ``localhost``, for local development)
3. open ``https://x.com/i/oauth2/authorize?...`` in the system browser
4. X redirects back with ``?code=…&state=…``; check ``state``
5. exchange the code (valid ~30 s) at ``POST https://api.x.com/2/oauth2/token``
6. with ``offline.access`` a refresh token is returned; access tokens last ~2 h
"""
from __future__ import annotations

import base64
import hashlib
import html
import http.server
import secrets as _secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass, field
from typing import Callable

from daily_vibe.xapi import net

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
REVOKE_URL = "https://api.x.com/2/oauth2/revoke"
ME_URL = "https://api.x.com/2/users/me"
SCOPES = ("tweet.read", "users.read", "bookmark.read", "offline.access")
CALLBACK_PATH = "/callback"
DEFAULT_PORT = 8765


class OAuthError(RuntimeError):
    pass


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}{CALLBACK_PATH}"


# PKCE ----------------------------------------------------------------------------
def make_code_verifier(nbytes: int = 48) -> str:
    """RFC 7636: 43–128 chars from [A-Za-z0-9-._~]. 48 random bytes -> 64 chars."""
    v = base64.urlsafe_b64encode(_secrets.token_bytes(nbytes)).rstrip(b"=").decode("ascii")
    assert 43 <= len(v) <= 128
    return v


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(client_id: str, redirect: str, state: str, challenge: str,
                        scopes=SCOPES) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    # quote_via=quote -> spaces become %20 (X's docs show %20-separated scopes)
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


# Tokens --------------------------------------------------------------------------
@dataclass
class Token:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0          # unix time
    scope: str = ""
    client_id: str = ""
    user_id: str = ""
    username: str = ""
    name: str = ""
    obtained_at: float = field(default_factory=time.time)

    def expired(self, now: float | None = None, leeway: float = 60.0) -> bool:
        return bool(self.expires_at) and (now if now is not None else time.time()) >= self.expires_at - leeway

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Token":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def _token_request(data: dict, client_id: str, client_secret: str = "",
                   transport: net.Transport | None = None, now: float | None = None) -> dict:
    transport = transport or net.request
    headers = {}
    if client_secret:  # confidential client ("Web App"/"Automated App"): HTTP Basic
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    data = {**data, "client_id": client_id}  # public clients must send client_id in the body
    resp = transport("POST", TOKEN_URL, data=data, headers=headers)
    payload = resp.json()
    if not resp.ok or "access_token" not in payload:
        err = payload.get("error_description") or payload.get("error") or payload.get("detail") or f"HTTP {resp.status}"
        raise OAuthError(f"X token endpoint refused the request: {err}")
    return payload


def _token_from_payload(payload: dict, client_id: str, previous: Token | None = None,
                        now: float | None = None) -> Token:
    now = time.time() if now is None else now
    tok = Token(
        access_token=payload["access_token"],
        # X rotates refresh tokens; keep the old one only if none was returned
        refresh_token=payload.get("refresh_token") or (previous.refresh_token if previous else ""),
        expires_at=now + float(payload.get("expires_in") or 7200),
        scope=payload.get("scope", previous.scope if previous else ""),
        client_id=client_id,
        obtained_at=now,
    )
    if previous:
        tok.user_id, tok.username, tok.name = previous.user_id, previous.username, previous.name
    return tok


def exchange_code(client_id: str, code: str, verifier: str, redirect: str, client_secret: str = "",
                  transport: net.Transport | None = None, now: float | None = None) -> Token:
    payload = _token_request({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": redirect, "code_verifier": verifier},
                             client_id, client_secret, transport)
    return _token_from_payload(payload, client_id, now=now)


def refresh_token(token: Token, client_id: str, client_secret: str = "",
                  transport: net.Transport | None = None, now: float | None = None) -> Token:
    if not token.refresh_token:
        raise OAuthError("no refresh token stored (was offline.access granted?); reconnect your X account")
    payload = _token_request({"grant_type": "refresh_token", "refresh_token": token.refresh_token},
                             client_id, client_secret, transport)
    return _token_from_payload(payload, client_id, previous=token, now=now)


def revoke(token: Token, client_id: str, client_secret: str = "",
           transport: net.Transport | None = None) -> bool:
    """Best effort: revoke the refresh (and access) token at X. Returns True on success."""
    transport = transport or net.request
    ok = True
    for value, hint in ((token.refresh_token, "refresh_token"), (token.access_token, "access_token")):
        if not value:
            continue
        headers = {}
        if client_secret:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        try:
            resp = transport("POST", REVOKE_URL, data={"token": value, "token_type_hint": hint,
                                                       "client_id": client_id}, headers=headers)
            ok = ok and resp.ok
        except Exception:
            ok = False
    return ok


def fetch_me(access_token: str, transport: net.Transport | None = None) -> dict:
    transport = transport or net.request
    resp = transport("GET", ME_URL, params={"user.fields": "name,username"},
                     headers={"Authorization": f"Bearer {access_token}"})
    data = resp.json().get("data")
    if not resp.ok or not data:
        raise OAuthError(f"could not read your X user (/2/users/me): HTTP {resp.status}")
    return data


# Loopback receiver ------------------------------------------------------------------
_PAGE = """<!doctype html><meta charset="utf-8"><title>The Daily Vibe</title>
<body style="font-family:sans-serif;max-width:32em;margin:4em auto;line-height:1.5">
<h2>{title}</h2><p>{msg}</p><p>You can close this tab and return to The Daily Vibe.</p></body>"""


class LoopbackReceiver:
    """One-shot HTTP server on 127.0.0.1 that captures the OAuth redirect."""

    def __init__(self, port: int, expected_state: str):
        self.expected_state = expected_state
        self.result: dict | None = None
        self._event = threading.Event()
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                if q.get("state") != receiver.expected_state:
                    title, msg = "Sign-in failed", "The response did not match this sign-in attempt (state mismatch)."
                    receiver._finish({"error": "state_mismatch"})
                elif "error" in q:
                    title, msg = "Sign-in canceled", html.escape(q.get("error_description") or q["error"])
                    receiver._finish({"error": q["error"]})
                elif "code" in q:
                    title, msg = "Connected to X", "The Daily Vibe received the authorization."
                    receiver._finish({"code": q["code"]})
                else:
                    title, msg = "Sign-in failed", "No authorization code in the response."
                    receiver._finish({"error": "no_code"})
                body = _PAGE.format(title=title, msg=msg).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # keep the console quiet
                pass

        try:
            self.server = http.server.HTTPServer(("127.0.0.1", int(port)), Handler)
        except OSError as exc:
            raise OAuthError(f"could not listen on 127.0.0.1:{port} ({exc.strerror or exc}); "
                             "pick another Redirect port and register the matching Callback URI") from exc
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()

    def _finish(self, result: dict) -> None:
        if self.result is None:
            self.result = result
        self._event.set()

    def wait(self, timeout: float) -> dict | None:
        self._event.wait(timeout)
        return self.result

    def close(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass


def authorize(client_id: str, port: int = DEFAULT_PORT, client_secret: str = "",
              open_browser: Callable[[str], object] = webbrowser.open, timeout: float = 180.0,
              transport: net.Transport | None = None, cancel: threading.Event | None = None) -> Token:
    """Run the whole interactive sign-in. Blocks until the browser comes back
    (or `timeout`); call from a background thread."""
    client_id = (client_id or "").strip()
    if not client_id:
        raise OAuthError("enter your X app's OAuth 2.0 Client ID first")
    verifier, state = make_code_verifier(), _secrets.token_urlsafe(24)
    receiver = LoopbackReceiver(port, state)
    try:
        redirect = redirect_uri(receiver.port)
        url = build_authorize_url(client_id, redirect, state, code_challenge(verifier))
        if not open_browser(url):
            raise OAuthError(f"could not open a web browser; open this URL manually: {url}")
        deadline = time.time() + timeout
        result = None
        while time.time() < deadline:
            result = receiver.wait(0.25)
            if result is not None or (cancel is not None and cancel.is_set()):
                break
        if result is None:
            raise OAuthError("sign-in canceled" if cancel is not None and cancel.is_set()
                             else f"no response from the browser within {int(timeout)} s")
        if "error" in result:
            raise OAuthError(f"X sign-in did not complete: {result['error']}")
        token = exchange_code(client_id, result["code"], verifier, redirect, client_secret, transport)
    finally:
        receiver.close()
    me = fetch_me(token.access_token, transport)
    token.user_id, token.username, token.name = str(me.get("id", "")), me.get("username", ""), me.get("name", "")
    return token
