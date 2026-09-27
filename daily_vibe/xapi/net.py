"""Minimal HTTP transport on urllib. Everything that talks to the network goes
through :func:`request`, so tests can pass a fake ``transport`` instead."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

try:
    from daily_vibe import __version__ as _V
except Exception:  # pragma: no cover
    _V = "0"

USER_AGENT = f"DailyVibe/{_V} (The Daily Vibe desktop journal; X Bookmarks plugin)"
DEFAULT_TIMEOUT = 15


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)  # keys lower-cased
    body: bytes = b""

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            return {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


# (method, url, params, data, headers, timeout) -> Response
Transport = Callable[..., Response]


def request(method: str, url: str, *, params: dict | None = None, data: dict | None = None,
            headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> Response:
    """Perform an HTTP request. `data` is sent form-encoded. Never raises for
    HTTP error statuses (returns the Response); network errors do raise."""
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b"")
