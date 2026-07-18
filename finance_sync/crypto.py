"""Encryption for stored OAuth/API tokens.

Tokens are encrypted at rest with Fernet (AES-128-CBC + HMAC). The key comes
from the ``SYNC_ENCRYPTION_KEY`` environment variable when set; otherwise a
key is generated once and persisted next to the database with restrictive
semantics (local single-user app). Usernames and passwords are never stored.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from .exceptions import ConfigurationError

_KEY_FILENAME = ".sync_encryption_key"


class TokenCipher:
    """Encrypts/decrypts credential dictionaries for storage in SQLite."""

    def __init__(self, key: Optional[bytes] = None, base_dir: Optional[str] = None):
        self._base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._fernet = Fernet(key or self._load_or_create_key())

    def _key_path(self) -> str:
        return os.path.join(self._base_dir, _KEY_FILENAME)

    def _load_or_create_key(self) -> bytes:
        env_key = os.environ.get("SYNC_ENCRYPTION_KEY")
        if env_key:
            return env_key.encode()
        path = self._key_path()
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read().strip()
        key = Fernet.generate_key()
        with open(path, "wb") as fh:
            fh.write(key)
        return key

    def encrypt(self, credentials: dict) -> str:
        """Serialize and encrypt a credentials dict to a storable string."""
        raw = json.dumps(credentials).encode()
        return self._fernet.encrypt(raw).decode()

    def decrypt(self, blob: str) -> dict:
        """Decrypt a stored credentials blob back into a dict."""
        if not blob:
            return {}
        try:
            raw = self._fernet.decrypt(blob.encode())
        except InvalidToken as exc:
            raise ConfigurationError(
                "Stored credentials cannot be decrypted (encryption key changed?)"
            ) from exc
        return json.loads(raw.decode())
