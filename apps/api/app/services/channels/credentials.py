"""Per-platform OAuth app credentials entered by the user (or provided via env).

Stored in ``<storage>/channel-credentials.json`` (override with
``AETHER_CHANNELS_CONFIG_PATH``). Client secrets are Fernet-encrypted; client
ids are not secret (they appear in every authorize URL) and are stored as-is.

Environment fallback (used when nothing is saved for that field):
    AETHER_YOUTUBE_CLIENT_ID / AETHER_YOUTUBE_CLIENT_SECRET
    AETHER_FACEBOOK_CLIENT_ID / AETHER_FACEBOOK_CLIENT_SECRET   (Meta App ID / App Secret)
    AETHER_TIKTOK_CLIENT_ID / AETHER_TIKTOK_CLIENT_SECRET       (TikTok client key / secret;
                                                                AETHER_TIKTOK_CLIENT_KEY also accepted)
    AETHER_CHANNELS_REDIRECT_BASE_URL                           (e.g. http://127.0.0.1:8386)
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import storage
from . import PLATFORMS
from .crypto import SecretDecryptionError, decrypt_secret, encrypt_secret, mask_secret

_lock = threading.Lock()


@dataclass
class PlatformCredentials:
    platform: str
    client_id: str
    client_secret: str
    client_id_source: str  # settings | env | none
    client_secret_source: str  # settings | env | none
    scopes_override: str = ""

    @property
    def configured(self) -> bool:
        # Google desktop clients technically work with PKCE alone, but Google
        # issues a secret for "Desktop app" clients and Meta/TikTok require one.
        return bool(self.client_id and self.client_secret)


def config_path() -> Path:
    configured = os.getenv("AETHER_CHANNELS_CONFIG_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        path = Path(storage.STORAGE_ROOT) / "channel-credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _env(platform: str, field: str) -> str:
    names = [f"AETHER_{platform.upper()}_{field}"]
    if platform == "tiktok" and field == "CLIENT_ID":
        names.append("AETHER_TIKTOK_CLIENT_KEY")
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def get_credentials(platform: str) -> PlatformCredentials:
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    stored = (_read().get("platforms") or {}).get(platform) or {}
    client_id = str(stored.get("client_id") or "").strip()
    id_source = "settings" if client_id else "none"
    if not client_id:
        client_id = _env(platform, "CLIENT_ID")
        id_source = "env" if client_id else "none"
    secret = ""
    secret_source = "none"
    encrypted = str(stored.get("client_secret_encrypted") or "")
    if encrypted:
        try:
            secret = decrypt_secret(encrypted)
            secret_source = "settings"
        except SecretDecryptionError:
            secret = ""
    if not secret:
        secret = _env(platform, "CLIENT_SECRET")
        secret_source = "env" if secret else "none"
    return PlatformCredentials(
        platform=platform,
        client_id=client_id,
        client_secret=secret,
        client_id_source=id_source,
        client_secret_source=secret_source,
        scopes_override=str(stored.get("scopes") or "").strip(),
    )


def update_credentials(
    platform: str,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    clear_secret: bool = False,
    scopes: str | None = None,
) -> PlatformCredentials:
    """Save credentials. ``None`` / blank secret keeps the stored secret (write-only field)."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    with _lock:
        data = _read()
        platforms = data.setdefault("platforms", {})
        entry = platforms.setdefault(platform, {})
        if client_id is not None:
            entry["client_id"] = client_id.strip()
        if clear_secret:
            entry["client_secret_encrypted"] = ""
        elif client_secret is not None and client_secret.strip():
            entry["client_secret_encrypted"] = encrypt_secret(client_secret.strip())
        if scopes is not None:
            entry["scopes"] = scopes.strip()
        _write(data)
    return get_credentials(platform)


def get_redirect_base_url() -> str:
    value = str(_read().get("redirect_base_url") or "").strip()
    return (value or os.getenv("AETHER_CHANNELS_REDIRECT_BASE_URL", "").strip()).rstrip("/")


def set_redirect_base_url(value: str) -> str:
    cleaned = (value or "").strip().rstrip("/")
    if cleaned and not cleaned.startswith(("http://", "https://")):
        raise ValueError("redirect_base_url must start with http:// or https://")
    with _lock:
        data = _read()
        data["redirect_base_url"] = cleaned
        _write(data)
    return get_redirect_base_url()


def public_view(platform: str) -> dict[str, Any]:
    creds = get_credentials(platform)
    return {
        "platform": platform,
        "client_id": creds.client_id,
        "client_id_source": creds.client_id_source,
        "client_secret_set": bool(creds.client_secret),
        "client_secret_masked": mask_secret(creds.client_secret),
        "client_secret_source": creds.client_secret_source,
        "scopes_override": creds.scopes_override,
        "configured": creds.configured,
    }
