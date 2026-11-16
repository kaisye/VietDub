"""Assistant provider settings: persisted locally, API keys encrypted at rest.

Settings live in ``<storage>/assistant-settings.json`` (override with
``AETHER_ASSISTANT_SETTINGS_PATH``). API keys are Fernet-encrypted with a key
kept next to the settings file (``assistant-secret.key``, mode 0600) or taken
from ``AETHER_ASSISTANT_SECRET_KEY``. Keys are never logged and never returned
in full by the API — see :func:`public_settings`.
"""
from __future__ import annotations

import base64
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..storage import ensure_storage

ANTHROPIC = "anthropic"
OPENAI_COMPATIBLE = "openai_compatible"
PROVIDERS = (ANTHROPIC, OPENAI_COMPATIBLE)

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
# Cheap, tool-capable default for the OpenAI-compatible path (via OpenRouter).
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3.7-flash"
# Writing content (titles, captions, posts) reads better with DeepSeek; OpenRouter only.
DEFAULT_CONTENT_MODEL = "deepseek/deepseek-v4.1-flash-1017"

# OpenAI-compatible presets the UI can offer; ``base_url`` is editable anyway.
OPENAI_PRESETS: dict[str, dict[str, Any]] = {
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "requires_key": True, "default_model": DEFAULT_OPENROUTER_MODEL},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "requires_key": True, "default_model": "gpt-4o-mini"},
    "ollama": {"label": "Ollama", "base_url": "http://localhost:11434/v1", "requires_key": False, "default_model": ""},
    "lmstudio": {"label": "LM Studio", "base_url": "http://localhost:1234/v1", "requires_key": False, "default_model": ""},
    "custom": {"label": "Tùy chỉnh", "base_url": "", "requires_key": False, "default_model": ""},
}

EFFORT_VALUES = ("auto", "low", "medium", "high")
TOOLS_MODES = ("auto", "on", "off")

DEFAULTS: dict[str, Any] = {
    "active_provider": OPENAI_COMPATIBLE,
    "max_tokens": 16000,
    ANTHROPIC: {
        "api_key": "",
        "model": DEFAULT_ANTHROPIC_MODEL,
        "base_url": DEFAULT_ANTHROPIC_BASE_URL,
        "effort": "auto",
        "refusal_fallback": True,
    },
    OPENAI_COMPATIBLE: {
        "preset": "openrouter",
        "base_url": OPENAI_PRESETS["openrouter"]["base_url"],
        "api_key": "",
        "model": DEFAULT_OPENROUTER_MODEL,
        "content_model": DEFAULT_CONTENT_MODEL,
        "tools_mode": "auto",
    },
}

SECRET_FIELDS = ("api_key",)
MASK_CHAR = "•"


class SettingsError(ValueError):
    pass


# --------------------------------------------------------------------------- paths


def _settings_path() -> Path:
    configured = os.getenv("AETHER_ASSISTANT_SETTINGS_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return ensure_storage() / "assistant-settings.json"


def _key_path() -> Path:
    return _settings_path().with_name("assistant-secret.key")


# ---------------------------------------------------------------------- encryption


def _fernet() -> Fernet:
    env_key = os.getenv("AETHER_ASSISTANT_SECRET_KEY", "").strip()
    if env_key:
        # Accept any passphrase: derive a valid Fernet key from it.
        raw = env_key.encode("utf-8")
        try:
            return Fernet(raw)
        except (ValueError, TypeError):
            import hashlib

            return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))
    path = _key_path()
    if path.exists():
        return Fernet(path.read_bytes().strip())
    key = Fernet.generate_key()
    # Create with 0600 from the start (no world-readable window).
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return "enc:" + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("enc:"):
        return value  # tolerate a hand-edited plaintext value
    try:
        return _fernet().decrypt(value[4:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Key file lost/rotated: treat as unset instead of crashing.
        return ""


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return MASK_CHAR * 8
    return f"{value[:6]}{MASK_CHAR * 8}{value[-4:]}"


# ------------------------------------------------------------------------ load/save


def _merge(base: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in data.items():
        if key not in out:
            continue
        if isinstance(out[key], dict) and isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_key in out[key] and sub_value is not None:
                    out[key][sub_key] = sub_value
        elif value is not None:
            out[key] = value
    return out


def load_settings() -> dict[str, Any]:
    """Return settings with secrets decrypted (internal use only)."""
    path = _settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            data = {}
    settings = _merge(DEFAULTS, data)
    for provider in PROVIDERS:
        for field in SECRET_FIELDS:
            settings[provider][field] = decrypt_secret(str(settings[provider].get(field) or ""))
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    stored = deepcopy(settings)
    for provider in PROVIDERS:
        for field in SECRET_FIELDS:
            stored[provider][field] = encrypt_secret(str(stored[provider].get(field) or ""))
    path = _settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stored, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _is_masked_echo(value: str) -> bool:
    return MASK_CHAR in value


def apply_patch(settings: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update. Secret semantics: ``None``/absent or a masked
    echo keeps the stored key, ``""`` clears it, anything else replaces it."""
    out = deepcopy(settings)
    if patch.get("active_provider") is not None:
        if patch["active_provider"] not in PROVIDERS:
            raise SettingsError(f"Unknown provider: {patch['active_provider']}")
        out["active_provider"] = patch["active_provider"]
    if patch.get("max_tokens") is not None:
        value = int(patch["max_tokens"])
        if not 256 <= value <= 128000:
            raise SettingsError("max_tokens must be between 256 and 128000")
        out["max_tokens"] = value
    for provider in PROVIDERS:
        section = patch.get(provider)
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if key not in out[provider] or value is None:
                continue
            if key in SECRET_FIELDS:
                text = str(value).strip()
                if _is_masked_echo(text):
                    continue
                out[provider][key] = text
                continue
            if isinstance(out[provider][key], bool):
                out[provider][key] = bool(value)
                continue
            out[provider][key] = str(value).strip()
    a = out[ANTHROPIC]
    if a["effort"] not in EFFORT_VALUES:
        raise SettingsError(f"effort must be one of {', '.join(EFFORT_VALUES)}")
    a["base_url"] = (a["base_url"] or DEFAULT_ANTHROPIC_BASE_URL).rstrip("/")
    a["model"] = a["model"] or DEFAULT_ANTHROPIC_MODEL
    o = out[OPENAI_COMPATIBLE]
    if o["tools_mode"] not in TOOLS_MODES:
        raise SettingsError(f"tools_mode must be one of {', '.join(TOOLS_MODES)}")
    if o["preset"] not in OPENAI_PRESETS:
        raise SettingsError(f"preset must be one of {', '.join(OPENAI_PRESETS)}")
    preset_section = patch.get(OPENAI_COMPATIBLE) or {}
    if "preset" in preset_section and "base_url" not in preset_section and OPENAI_PRESETS[o["preset"]]["base_url"]:
        o["base_url"] = OPENAI_PRESETS[o["preset"]]["base_url"]
    if "model" not in preset_section and o["preset"] != settings[OPENAI_COMPATIBLE]["preset"]:
        o["model"] = OPENAI_PRESETS[o["preset"]]["default_model"]
    o["base_url"] = o["base_url"].rstrip("/")
    return out


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    settings = apply_patch(load_settings(), patch)
    save_settings(settings)
    return settings


def content_model_for(settings: dict[str, Any], provider: str) -> str:
    """Model for writing content, or "" to use the provider's main model.

    Only OpenRouter has one: other OpenAI-compatible servers (Ollama, LM Studio...)
    rarely serve the same model ids.
    """
    if provider != OPENAI_COMPATIBLE:
        return ""
    section = settings[OPENAI_COMPATIBLE]
    if section.get("preset") != "openrouter":
        return ""
    return str(section.get("content_model") or "").strip()


def provider_configured(settings: dict[str, Any], provider: str) -> tuple[bool, str]:
    section = settings[provider]
    if provider == ANTHROPIC:
        if not section["api_key"]:
            return False, "Chưa nhập Anthropic API key."
        return True, ""
    if not section["base_url"]:
        return False, "Chưa nhập Base URL cho máy chủ OpenAI-compatible."
    if not section["model"]:
        return False, "Chưa chọn model cho máy chủ OpenAI-compatible."
    if OPENAI_PRESETS.get(section["preset"], {}).get("requires_key") and not section["api_key"]:
        return False, "Chưa nhập API key."
    return True, ""


def public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Settings safe to return to the UI: secrets masked."""
    out = deepcopy(settings)
    for provider in PROVIDERS:
        secret = out[provider].pop("api_key", "")
        out[provider]["api_key_set"] = bool(secret)
        out[provider]["api_key_masked"] = mask_secret(secret)
        ok, reason = provider_configured(settings, provider)
        out[provider]["configured"] = ok
        out[provider]["configured_reason"] = reason
    return out
