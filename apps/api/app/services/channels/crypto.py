"""Encryption at rest for channel tokens and OAuth client secrets.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from ``cryptography``. The key is read
from ``AETHER_CHANNELS_SECRET_KEY`` (a urlsafe-base64 Fernet key) when set,
otherwise generated once and stored in ``<storage>/secrets/channels.key`` with
0600 permissions. This protects the SQLite database and its backups if they are
copied elsewhere; it does not defend against malware running as the same OS
user (that would need the OS keychain).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .. import storage

_PREFIX = "fernet:"
_lock = threading.Lock()
_cached: tuple[str, Fernet] | None = None


class SecretDecryptionError(RuntimeError):
    """Stored ciphertext cannot be decrypted (key changed or data corrupted)."""


def _key_path() -> Path:
    return Path(storage.STORAGE_ROOT) / "secrets" / "channels.key"


def _load_key() -> bytes:
    configured = os.getenv("AETHER_CHANNELS_SECRET_KEY", "").strip()
    if configured:
        return configured.encode("ascii")
    path = _key_path()
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Create with restrictive permissions from the start (no world-readable window).
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _fernet() -> Fernet:
    global _cached
    with _lock:
        identity = os.getenv("AETHER_CHANNELS_SECRET_KEY", "").strip() or str(_key_path())
        if _cached is None or _cached[0] != identity:
            _cached = (identity, Fernet(_load_key()))
        return _cached[1]


def encrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        raise SecretDecryptionError("Stored secret is not encrypted with the channels key.")
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:  # key rotated / file replaced
        raise SecretDecryptionError("Stored secret cannot be decrypted with the current key.") from exc


def mask_secret(value: str | None) -> str:
    """Return a display-safe hint such as ``••••••3f9a`` (never the full value)."""
    if not value:
        return ""
    tail = value[-4:] if len(value) > 8 else ""
    return "••••••" + tail


def reset_cache_for_tests() -> None:
    global _cached
    with _lock:
        _cached = None
