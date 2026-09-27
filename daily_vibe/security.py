"""App lock: salted scrypt password hashes and failed-attempt back-off.

This is an *app lock* only; journal files on disk stay plain Markdown.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Callable

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
_MAXMEM = 64 * 1024 * 1024


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r),
                             p=int(p), maxmem=_MAXMEM, dklen=len(expected))
        return hmac.compare_digest(got, expected)
    except Exception:
        return False


class AttemptLimiter:
    """After `free` failures, each further failure doubles the wait (max `cap` s)."""

    def __init__(self, free: int = 3, base: float = 2.0, cap: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self.free, self.base, self.cap, self.clock = free, base, cap, clock
        self.failures = 0
        self.locked_until = 0.0

    def remaining(self) -> float:
        return max(0.0, self.locked_until - self.clock())

    def can_try(self) -> bool:
        return self.remaining() <= 0

    def record(self, success: bool) -> None:
        if success:
            self.failures, self.locked_until = 0, 0.0
            return
        self.failures += 1
        if self.failures >= self.free:
            delay = min(self.cap, self.base * 2 ** (self.failures - self.free))
            self.locked_until = self.clock() + delay
