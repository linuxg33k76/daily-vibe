"""Secret plugin settings: stored in the OS keyring when the optional
`keyring` package is installed and has a working backend; otherwise in
config.toml (plain text) and the UI shows a warning."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
SERVICE = "daily-vibe"


def _keyring():
    try:
        import keyring  # type: ignore
        from keyring.backends import fail  # type: ignore
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def keyring_available() -> bool:
    return _keyring() is not None


def _user(plugin_id: str, key: str) -> str:
    return f"plugin:{plugin_id}:{key}"


def get_secret(plugin_id: str, key: str, plugin_config: dict) -> str:
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(SERVICE, _user(plugin_id, key))
            if value is not None:
                return value
        except Exception as exc:
            log.warning("keyring read failed: %s", exc)
    return str(plugin_config.get(key, "") or "")


def set_secret(plugin_id: str, key: str, value: str, plugin_config: dict) -> str:
    """Store a secret. Returns "keyring" or "config" (where it ended up).
    When stored in the keyring, the key is removed from plugin_config."""
    kr = _keyring()
    if kr is not None:
        try:
            if value:
                kr.set_password(SERVICE, _user(plugin_id, key), value)
            else:
                try:
                    kr.delete_password(SERVICE, _user(plugin_id, key))
                except Exception:
                    pass
            plugin_config.pop(key, None)
            return "keyring"
        except Exception as exc:
            log.warning("keyring write failed, falling back to config: %s", exc)
    plugin_config[key] = value
    return "config"
