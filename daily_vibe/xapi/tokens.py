"""Where the X OAuth tokens live.

1. The OS keyring (``keyring`` package: macOS Keychain, Windows Credential
   Manager, Secret Service/KWallet on Linux) under service ``daily-vibe``,
   user ``plugin:x_bookmarks:oauth``.
2. Fallback when no keyring backend works: ``<config dir>/x_bookmarks_tokens.json``,
   created with 0600 permissions and a ``_warning`` field saying it holds
   plain-text tokens. Preferences shows which one is in use.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from daily_vibe import secrets
from daily_vibe.config import config_dir
from daily_vibe.xapi.oauth import Token

log = logging.getLogger(__name__)
KEYRING_USER = "plugin:x_bookmarks:oauth"
FALLBACK_NAME = "x_bookmarks_tokens.json"
FALLBACK_WARNING = ("PLAIN-TEXT X (Twitter) OAuth tokens for The Daily Vibe's X Bookmarks plugin. "
                    "Stored here only because no system keyring was available (pip install keyring). "
                    "Use Preferences → Plugins → X Bookmarks → Disconnect, or delete this file, to remove them.")
_lock = threading.RLock()


class TokenStore:
    def __init__(self, fallback_path: Path | None = None):
        self._fallback_path = fallback_path

    @property
    def fallback_path(self) -> Path:
        return self._fallback_path or (config_dir() / FALLBACK_NAME)

    @staticmethod
    def _kr():
        return secrets._keyring()

    def backend(self) -> str:
        """"keyring" or "file" (where a save would go)."""
        return "keyring" if self._kr() is not None else "file"

    def describe(self) -> str:
        if self.backend() == "keyring":
            return "Tokens are stored in the system keyring."
        return f"⚠ No system keyring available: tokens are stored in plain text (0600) in {self.fallback_path}"

    # ---------------------------------------------------------------------------
    def load(self) -> Token | None:
        with _lock:
            kr = self._kr()
            if kr is not None:
                try:
                    raw = kr.get_password(secrets.SERVICE, KEYRING_USER)
                    if raw:
                        return Token.from_dict(json.loads(raw))
                except Exception as exc:
                    log.warning("keyring read failed: %s", exc)
            p = self.fallback_path
            if p.is_file():
                try:
                    return Token.from_dict(json.loads(p.read_text(encoding="utf-8")).get("token") or {})
                except Exception as exc:
                    log.warning("could not read %s: %s", p, exc)
            return None

    def save(self, token: Token) -> str:
        with _lock:
            kr = self._kr()
            if kr is not None:
                try:
                    kr.set_password(secrets.SERVICE, KEYRING_USER, json.dumps(token.to_dict()))
                    self._remove_file()
                    return "keyring"
                except Exception as exc:
                    log.warning("keyring write failed, using the fallback file: %s", exc)
            self._write_file(token)
            return "file"

    def clear(self) -> None:
        with _lock:
            kr = self._kr()
            if kr is not None:
                try:
                    kr.delete_password(secrets.SERVICE, KEYRING_USER)
                except Exception:
                    pass
            self._remove_file()

    # ---------------------------------------------------------------------------
    def _write_file(self, token: Token) -> None:
        p = self.fallback_path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        data = json.dumps({"_warning": FALLBACK_WARNING, "token": token.to_dict()}, indent=2)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        os.chmod(tmp, 0o600)  # in case the file pre-existed with other bits / umask
        os.replace(tmp, p)

    def _remove_file(self) -> None:
        try:
            self.fallback_path.unlink(missing_ok=True)
        except Exception as exc:
            log.warning("could not delete %s: %s", self.fallback_path, exc)
