from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .nghitts_tts import NGHITTS_PRESETS
from .storage import ensure_storage

NO_VOICE_ID = "none"
BUNDLED_VOICE_DIR = Path(__file__).resolve().parents[1] / "assets" / "defaults" / "voice-library"
LEGACY_BUNDLED_VOICE_IDS = {
    "CDTeam": "omnivoice_clone_01",
    "Best": "omnivoice_clone_02",
    "vocie01": "omnivoice_clone_03",
    "Voice03": "omnivoice_clone_04",
    "Vocie04": "omnivoice_clone_05",
    "voice05": "omnivoice_clone_06",
}


@dataclass
class VoiceOption:
    id: str
    name: str
    locale: str
    language: str
    type: str
    description: str = ""
    omnivoice_mode: str = ""
    reference_audio_url: str = ""
    reference_audio_path: str = ""
    reference_text: str = ""
    reference_text_path: str = ""
    instruction: str = ""
    # Which TTS engine speaks this voice: "" / "edge" (Microsoft neural, default),
    # "omnivoice" (clone/design on GPU), or "nghitts" (offline CPU Piper voice).
    engine: str = ""


# NGHI-TTS offline preset voices (CPU, Piper/ONNX, torch-free). Built from a single
# source of truth so the catalog and the engine never drift apart. Grouped with Edge
# under the "Edge TTS" provider group in the UI.
_NGHITTS_VOICE_OPTIONS: list[VoiceOption] = [
    VoiceOption(
        id=preset_id,
        name=display_name,
        locale="vi-VN",
        language="Vietnamese",
        type="NGHI-TTS (offline)",
        description="NGHI-TTS · giọng Việt offline · CPU",
        engine="nghitts",
    )
    for preset_id, _model_name, display_name in NGHITTS_PRESETS
]


def _bundled_omnivoice_options() -> list[VoiceOption]:
    manifest = BUNDLED_VOICE_DIR / "voices.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []

    options: list[VoiceOption] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        audio_name = Path(str(item.get("reference_audio_file") or "")).name
        audio_path = BUNDLED_VOICE_DIR / audio_name
        if not audio_name or not audio_path.is_file():
            continue
        voice_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not voice_id or not name:
            continue
        options.append(
            VoiceOption(
                id=voice_id,
                name=name,
                locale=str(item.get("locale") or "vi-VN").strip(),
                language=str(item.get("language") or "Vietnamese").strip(),
                type=str(item.get("type") or "OmniVoice Clone").strip(),
                description=str(item.get("description") or "").strip(),
                omnivoice_mode="clone",
                reference_audio_path=str(audio_path.resolve()),
                reference_text=str(item.get("reference_text") or "").strip(),
                instruction=str(item.get("instruction") or "").strip(),
                engine="omnivoice",
            )
        )
    return options


DEFAULT_VOICE_OPTIONS: list[VoiceOption] = [
    VoiceOption(NO_VOICE_ID, "None", "none", "None", "Disabled", "Keep source audio without generating a dubbed voice."),
    VoiceOption("vi-VN-HoaiMyNeural", "Hoai My", "vi-VN", "Vietnamese", "Narration", "Vietnamese female narration voice for localized videos.", engine="edge"),
    VoiceOption("vi-VN-NamMinhNeural", "Nam Minh", "vi-VN", "Vietnamese", "Narration", "Vietnamese male narration voice for localized videos.", engine="edge"),
    *_NGHITTS_VOICE_OPTIONS,
    *_bundled_omnivoice_options(),
]


def list_voice_options() -> list[VoiceOption]:
    custom = _read_custom_options()
    merged: dict[str, VoiceOption] = {voice.id: voice for voice in DEFAULT_VOICE_OPTIONS}
    for voice in custom:
        if voice.id in LEGACY_BUNDLED_VOICE_IDS:
            continue
        merged[voice.id] = voice
    return list(merged.values())


def canonical_voice_id(voice_id: str) -> str:
    normalized = (voice_id or "").strip()
    return LEGACY_BUNDLED_VOICE_IDS.get(normalized, normalized)


def resolve_voice_option(voice_id: str) -> VoiceOption | None:
    canonical = canonical_voice_id(voice_id)
    return next((voice for voice in list_voice_options() if voice.id == canonical), None)


def save_voice_option(option: VoiceOption) -> VoiceOption:
    cleaned = VoiceOption(
        id=option.id.strip(),
        name=option.name.strip(),
        locale=option.locale.strip(),
        language=option.language.strip(),
        type=option.type.strip(),
        description=option.description.strip(),
        omnivoice_mode=option.omnivoice_mode.strip(),
        reference_audio_url=option.reference_audio_url.strip(),
        reference_audio_path=option.reference_audio_path.strip(),
        reference_text=option.reference_text.strip(),
        reference_text_path=option.reference_text_path.strip(),
        instruction=option.instruction.strip(),
        engine=option.engine.strip(),
    )
    if not cleaned.id:
        raise ValueError("Voice ID is required.")
    if cleaned.id == NO_VOICE_ID:
        raise ValueError("Voice ID 'none' is reserved.")
    if not cleaned.name:
        raise ValueError("Voice name is required.")

    custom = {voice.id: voice for voice in _read_custom_options()}
    custom[cleaned.id] = cleaned
    _write_custom_options(list(custom.values()))
    return cleaned


def delete_voice_option(voice_id: str) -> bool:
    custom = _read_custom_options()
    next_options = [voice for voice in custom if voice.id != voice_id]
    if len(next_options) == len(custom):
        return False
    _write_custom_options(next_options)
    return True


def _read_custom_options() -> list[VoiceOption]:
    path = _options_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    options: list[VoiceOption] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        option = _voice_from_dict(item)
        if option:
            options.append(option)
    return options


def _write_custom_options(options: list[VoiceOption]) -> None:
    path = _options_path()
    path.write_text(json.dumps([asdict(option) for option in options], indent=2, ensure_ascii=False), encoding="utf-8")


def _voice_from_dict(data: dict[str, Any]) -> VoiceOption | None:
    voice_id = str(data.get("id") or "").strip()
    name = str(data.get("name") or "").strip()
    if not voice_id or not name:
        return None
    return VoiceOption(
        id=voice_id,
        name=name,
        locale=str(data.get("locale") or "custom").strip(),
        language=str(data.get("language") or "Custom").strip(),
        type=str(data.get("type") or "Custom").strip(),
        description=str(data.get("description") or "").strip(),
        omnivoice_mode=str(data.get("omnivoice_mode") or "").strip(),
        reference_audio_url=str(data.get("reference_audio_url") or "").strip(),
        reference_audio_path=str(data.get("reference_audio_path") or "").strip(),
        reference_text=str(data.get("reference_text") or "").strip(),
        reference_text_path=str(data.get("reference_text_path") or "").strip(),
        instruction=str(data.get("instruction") or "").strip(),
        engine=str(data.get("engine") or "").strip(),
    )


def _options_path() -> Path:
    configured = os.getenv("AETHER_VOICE_OPTIONS_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return ensure_storage() / "voice-options.json"
