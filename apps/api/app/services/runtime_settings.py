from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .storage import ensure_storage


LOCAL_OMNIVOICE_URL = "http://127.0.0.1:8008"
LOCAL_DIARIZATION_URL = "http://127.0.0.1:8010"


@dataclass
class RuntimeSettings:
    tts_provider: str = "edge"
    prefer_local_gpu: bool = True
    prefer_existing_subtitles: bool = True
    nvidia_api_key: str = ""
    omnivoice_runtime: str = "auto"
    omnivoice_device: str = "auto"
    omnivoice_api_url: str = ""
    omnivoice_api_key: str = ""
    omnivoice_mode: str = ""
    omnivoice_instruct: str = ""
    omnivoice_ref_audio_url: str = ""
    omnivoice_ref_audio_path: str = ""
    omnivoice_ref_text: str = ""
    omnivoice_ref_text_path: str = ""
    translation_provider: str = ""
    translation_nvidia_model: str = ""
    local_translation_base_url: str = ""
    local_translation_model: str = ""
    local_translation_api_key: str = ""
    diarization_api_url: str = LOCAL_DIARIZATION_URL
    diarization_device: str = "auto"
    huggingface_token: str = ""
    groq_api_key: str = ""
    ngrok_authtoken: str = ""
    ngrok_domain: str = ""


def get_runtime_settings() -> RuntimeSettings:
    data = _read_settings_file()
    configured_url = str(data.get("omnivoice_api_url") or os.getenv("OMNIVOICE_API_URL", "")).strip()
    configured_runtime = str(data.get("omnivoice_runtime") or os.getenv("OMNIVOICE_RUNTIME", "")).strip()
    if not configured_runtime:
        configured_runtime = "colab" if configured_url and not _is_local_url(configured_url) else "auto"
    if configured_runtime != "colab":
        configured_url = LOCAL_OMNIVOICE_URL
    # Migrate selections for providers that are no longer shipped.
    tts_provider = str(data.get("tts_provider") or os.getenv("AETHER_TTS_PROVIDER", "edge")).strip().lower()
    if tts_provider == "vieneu":
        tts_provider = "edge"
    elif tts_provider == "nghitts":
        tts_provider = "zerotts"
    return RuntimeSettings(
        tts_provider=tts_provider,
        prefer_local_gpu=_coerce_bool(
            data.get("prefer_local_gpu"),
            os.getenv("AETHER_PREFER_LOCAL_GPU"),
            default=True,
        ),
        prefer_existing_subtitles=_coerce_bool(
            data.get("prefer_existing_subtitles"),
            os.getenv("AETHER_PREFER_EXISTING_SUBTITLES"),
            default=True,
        ),
        nvidia_api_key=_stored_secret(data, "nvidia_api_key", "NVIDIA_API_KEY"),
        omnivoice_runtime=configured_runtime,
        omnivoice_device=str(data.get("omnivoice_device") or os.getenv("OMNIVOICE_DEVICE_MAP", "auto")).strip(),
        omnivoice_api_url=configured_url,
        omnivoice_api_key=str(data.get("omnivoice_api_key") or os.getenv("OMNIVOICE_API_KEY", "")).strip(),
        omnivoice_mode=str(data.get("omnivoice_mode") or os.getenv("OMNIVOICE_MODE", "auto")).strip(),
        omnivoice_instruct=str(data.get("omnivoice_instruct") or os.getenv("OMNIVOICE_INSTRUCT", "")).strip(),
        omnivoice_ref_audio_url=str(data.get("omnivoice_ref_audio_url") or os.getenv("OMNIVOICE_REF_AUDIO_URL", "")).strip(),
        omnivoice_ref_audio_path=str(
            data.get("omnivoice_ref_audio_path") or os.getenv("OMNIVOICE_REF_AUDIO_PATH", "")
        ).strip(),
        omnivoice_ref_text=str(data.get("omnivoice_ref_text") or os.getenv("OMNIVOICE_REF_TEXT", "")).strip(),
        omnivoice_ref_text_path=str(
            data.get("omnivoice_ref_text_path") or os.getenv("OMNIVOICE_REF_TEXT_PATH", "")
        ).strip(),
        translation_provider=str(
            data.get("translation_provider") or os.getenv("AETHER_TRANSLATION_PROVIDER", "openai-compatible")
        ).strip(),
        translation_nvidia_model=str(
            data.get("translation_nvidia_model") or os.getenv("NVIDIA_MODEL", "gpt-oss-120b")
        ).strip(),
        local_translation_base_url=str(
            data.get("local_translation_base_url")
            or os.getenv("AETHER_LOCAL_TRANSLATION_BASE_URL", "http://127.0.0.1:20128/v1")
        ).strip(),
        local_translation_model=str(
            data.get("local_translation_model") or os.getenv("AETHER_LOCAL_TRANSLATION_MODEL", "translate")
        ).strip(),
        local_translation_api_key=str(
            data.get("local_translation_api_key") or os.getenv("AETHER_LOCAL_TRANSLATION_API_KEY", "")
        ).strip(),
        diarization_api_url=str(
            data.get("diarization_api_url") or os.getenv("AETHER_DIARIZATION_API_URL", LOCAL_DIARIZATION_URL)
        ).strip(),
        diarization_device=str(
            data.get("diarization_device") or os.getenv("AETHER_DIARIZATION_DEVICE", "auto")
        ).strip(),
        huggingface_token=str(
            data.get("huggingface_token") or os.getenv("HF_TOKEN", "")
        ).strip(),
        groq_api_key=_stored_secret(data, "groq_api_key", "GROQ_API_KEY"),
        ngrok_authtoken=str(
            data.get("ngrok_authtoken") or os.getenv("NGROK_AUTHTOKEN", "")
        ).strip(),
        ngrok_domain=str(
            data.get("ngrok_domain") or os.getenv("AETHER_NGROK_DOMAIN", "")
        ).strip(),
    )


def update_runtime_settings(values: dict[str, Any]) -> RuntimeSettings:
    current = asdict(get_runtime_settings())
    allowed = set(current)
    for key, value in values.items():
        if key in allowed and value is not None:
            # NVIDIA/Groq keys may be explicitly cleared from the desktop UI.
            # Keep the previous write-only behavior for unrelated secrets so a
            # masked/blank response cannot accidentally erase them.
            if key in {
                "huggingface_token",
                "omnivoice_api_key",
                "local_translation_api_key",
                "ngrok_authtoken",
            } and not str(value).strip():
                continue
            if isinstance(current[key], bool):
                current[key] = _coerce_bool(value, None, default=current[key])
                continue
            current[key] = str(value).strip()
    if "omnivoice_runtime" not in values and "omnivoice_api_url" in values:
        current["omnivoice_runtime"] = (
            "auto" if _is_local_url(current["omnivoice_api_url"]) else "colab"
        )
    # Changing the OmniVoice runtime implies the OmniVoice provider — but only when
    # the caller didn't also send an explicit tts_provider. The voice/engine screens
    # save the whole bundle (incl. omnivoice_runtime) on every change, so without this
    # guard picking Edge there would be silently reverted to OmniVoice.
    if "omnivoice_runtime" in values and "tts_provider" not in values:
        current["tts_provider"] = "omnivoice"
    if current["omnivoice_runtime"] != "colab":
        current["omnivoice_api_url"] = LOCAL_OMNIVOICE_URL
    _settings_path().write_text(json.dumps(current, indent=2), encoding="utf-8")
    settings = get_runtime_settings()
    apply_runtime_secrets_to_env(settings)
    return settings


def apply_runtime_secrets_to_env(settings: RuntimeSettings | None = None) -> None:
    """Mirror locally-configured secrets into the process environment.

    The pipeline reads keys such as ``NVIDIA_API_KEY`` via ``os.getenv`` in many
    places. The desktop Config screen stores them in runtime-settings.json, so we
    sync them into the environment on startup and after each settings update,
    keeping all existing getenv call sites working without further changes.
    """
    settings = settings or get_runtime_settings()
    _set_or_clear_env("NVIDIA_API_KEY", settings.nvidia_api_key)
    _set_or_clear_env("GROQ_API_KEY", settings.groq_api_key)


def _set_or_clear_env(name: str, value: str) -> None:
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)


def _read_settings_file() -> dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _stored_secret(data: dict[str, Any], key: str, environment_name: str) -> str:
    """Let an explicitly saved blank value override a stale process/system key."""
    value = data[key] if key in data else os.getenv(environment_name, "")
    return str(value or "").strip()


def _settings_path() -> Path:
    configured = os.getenv("AETHER_RUNTIME_SETTINGS_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return ensure_storage() / "runtime-settings.json"


def _is_local_url(value: str) -> bool:
    lowered = value.casefold()
    return "127.0.0.1" in lowered or "localhost" in lowered or "[::1]" in lowered


def _coerce_bool(*candidates_and_default: Any, default: bool = False) -> bool:
    # Accept the first non-None candidate from persisted JSON / env / patch input
    # and interpret it as a boolean. Falls back to ``default`` when nothing is set.
    candidates = candidates_and_default
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, bool):
            return candidate
        normalized = str(candidate).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default
