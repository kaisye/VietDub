from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .storage import ensure_storage
from .voice_options import list_voice_options


DEFAULT_VOICE_ID = "vi-VN-HoaiMyNeural"


@dataclass
class WorkspaceSettings:
    default_voice_id: str = DEFAULT_VOICE_ID


def get_workspace_settings() -> WorkspaceSettings:
    data = _read_settings_file()
    configured_voice = str(
        data.get("default_voice_id")
        or os.getenv("AETHER_DEFAULT_VOICE_ID", DEFAULT_VOICE_ID)
    ).strip()
    known_voices = {voice.id for voice in list_voice_options()}
    if configured_voice not in known_voices:
        configured_voice = DEFAULT_VOICE_ID
    return WorkspaceSettings(default_voice_id=configured_voice)


def update_workspace_settings(values: dict[str, Any]) -> WorkspaceSettings:
    current = asdict(get_workspace_settings())
    if "default_voice_id" in values and values["default_voice_id"] is not None:
        voice_id = str(values["default_voice_id"]).strip()
        known_voices = {voice.id for voice in list_voice_options()}
        if voice_id not in known_voices:
            raise ValueError(f"Voice not found: {voice_id}.")
        current["default_voice_id"] = voice_id
    _settings_path().write_text(json.dumps(current, indent=2), encoding="utf-8")
    return get_workspace_settings()


def _read_settings_file() -> dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _settings_path() -> Path:
    configured = os.getenv("AETHER_WORKSPACE_SETTINGS_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return ensure_storage() / "workspace-settings.json"
