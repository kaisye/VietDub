"""Viral-discovery source settings, stored in ``<storage>/viral-settings.json``.

API keys (YouTube Data API, optional Douyin third-party API) are encrypted at rest
with the same local Fernet key as channel tokens (services/channels/crypto.py),
never logged and only returned masked. Patch semantics for keys mirror the
assistant settings: omitted / ``None`` / a masked echo keeps, ``""`` clears.
"""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import storage
from ..channels import crypto

SECRET_FIELDS = ("youtube_api_key", "tikhub_api_key")
YOUTUBE_MODES = ("auto", "api", "ytdlp")
DOUYIN_MODES = ("public", "tikhub", "off")
YOUTUBE_DAILY_QUOTA = 10_000
DEFAULT_TIKHUB_BASE_URL = "https://api.tikhub.io"

DEFAULTS: dict[str, Any] = {
    "youtube_api_key": "",
    "youtube_mode": "auto",
    "default_region": "US",
    "douyin_mode": "public",
    "tikhub_api_key": "",
    "tikhub_base_url": DEFAULT_TIKHUB_BASE_URL,
    "translate_titles": True,
    "auto_rescan_enabled": False,
    "auto_rescan_hours": 6,
    "quota": {"date": "", "used": 0},
}

_lock = threading.Lock()


class SettingsError(ValueError):
    pass


def _path() -> Path:
    root = Path(storage.STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root / "viral-settings.json"


def _decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return crypto.decrypt_secret(value)
    except crypto.SecretDecryptionError:
        return ""


def load_settings() -> dict[str, Any]:
    """Settings with secrets decrypted (internal use only)."""
    data: dict[str, Any] = {}
    path = _path()
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, OSError):
            data = {}
    out = deepcopy(DEFAULTS)
    for key, value in data.items():
        if key in out and value is not None:
            out[key] = value
    for key in SECRET_FIELDS:
        out[key] = _decrypt(str(out.get(key) or ""))
    return out


def save_settings(settings: dict[str, Any]) -> None:
    stored = deepcopy(settings)
    for key in SECRET_FIELDS:
        stored[key] = crypto.encrypt_secret(str(stored.get(key) or ""))
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stored, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def apply_patch(settings: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(settings)
    for key, value in patch.items():
        if key not in DEFAULTS or key == "quota" or value is None:
            continue
        if key in SECRET_FIELDS:
            text = str(value).strip()
            if "•" in text:
                continue
            out[key] = text
        elif isinstance(DEFAULTS[key], bool):
            out[key] = bool(value)
        elif isinstance(DEFAULTS[key], int):
            try:
                out[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise SettingsError(f"{key} must be a number") from exc
        else:
            out[key] = str(value).strip()
    if out["youtube_mode"] not in YOUTUBE_MODES:
        raise SettingsError(f"youtube_mode must be one of {', '.join(YOUTUBE_MODES)}")
    if out["douyin_mode"] not in DOUYIN_MODES:
        raise SettingsError(f"douyin_mode must be one of {', '.join(DOUYIN_MODES)}")
    if not 1 <= int(out["auto_rescan_hours"]) <= 168:
        raise SettingsError("auto_rescan_hours must be between 1 and 168")
    region = (out["default_region"] or "").upper()
    if region and (len(region) != 2 or not region.isalpha()):
        raise SettingsError("default_region must be a 2-letter country code")
    out["default_region"] = region
    out["tikhub_base_url"] = (out["tikhub_base_url"] or DEFAULT_TIKHUB_BASE_URL).rstrip("/")
    return out


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        settings = apply_patch(load_settings(), patch)
        save_settings(settings)
        return settings


def _quota_day(now: datetime | None = None) -> str:
    # YouTube quotas reset at midnight Pacific time; UTC-8 is close enough for an estimate.
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(hours=8)).strftime("%Y-%m-%d")


def quota_used_today() -> int:
    quota = load_settings().get("quota") or {}
    return int(quota.get("used") or 0) if quota.get("date") == _quota_day() else 0


def add_quota(units: int) -> int:
    """Record YouTube API units spent today; returns the new total."""
    with _lock:
        settings = load_settings()
        quota = settings.get("quota") or {}
        day = _quota_day()
        used = int(quota.get("used") or 0) if quota.get("date") == day else 0
        used += int(units)
        settings["quota"] = {"date": day, "used": used}
        save_settings(settings)
        return used


def public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in settings.items() if k not in SECRET_FIELDS and k != "quota"}
    for key in SECRET_FIELDS:
        secret = settings.get(key) or ""
        out[f"{key}_set"] = bool(secret)
        out[f"{key}_masked"] = crypto.mask_secret(secret)
    quota = settings.get("quota") or {}
    out["youtube_quota_used_today"] = int(quota.get("used") or 0) if quota.get("date") == _quota_day() else 0
    out["youtube_quota_daily"] = YOUTUBE_DAILY_QUOTA
    return out
