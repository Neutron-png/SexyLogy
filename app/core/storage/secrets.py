"""
Local secret storage for proxy credentials / API keys (spec sections 11,
17, 25: "never store secrets in plain text").

Uses Fernet symmetric encryption (cryptography package) with a key file
written once to the user's config directory with owner-only permissions.
This is "encrypted at rest on this machine", not a full secrets vault -
good enough for a local desktop app, and it keeps secrets out of
projects.config_json / logs / exported files entirely.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "LOGY"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key_path() -> Path:
    return _config_dir() / "secret.key"


def _get_or_create_key() -> bytes:
    from cryptography.fernet import Fernet

    key_path = _key_path()
    if key_path.exists():
        return key_path.read_bytes()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # owner read/write only
    except OSError:
        pass  # best-effort on platforms without POSIX perms (e.g. some Windows setups)
    return key


class SecretStore:
    """Encrypts/decrypts short strings (proxy URLs with credentials, API keys)."""

    def __init__(self):
        from cryptography.fernet import Fernet

        self._fernet = Fernet(_get_or_create_key())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")

    @staticmethod
    def redact(value: str) -> str:
        """For display in logs/UI: keep scheme+host, hide credentials."""
        if "@" in value and "://" in value:
            scheme, rest = value.split("://", 1)
            _, _, host = rest.partition("@")
            return f"{scheme}://***:***@{host}"
        return "***"
