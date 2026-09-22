from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .runtime_settings import get_runtime_settings
from .renderer import normalize_subtitle_style
from .subtitle_styles import list_subtitle_styles
from .voice_options import NO_VOICE_ID, VoiceOption, canonical_voice_id, list_voice_options
from .workspace_settings import get_workspace_settings


CONFIGURATION_SCHEMA_VERSION = 1
APPLICATION_DEFAULTS_VERSION = 1
RESET_MARKER = {"$reset": True}
MAX_CONFIGURATION_BYTES = 256 * 1024
NO_SUBTITLE_STYLE_ID = "none"


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OcrRegionConfiguration(ConfigurationModel):
    # Center-based fractions of the frame (same convention as hard_sub_blur_*),
    # so the desktop region picker maps 1:1 onto this. Only consumed when the
    # subtitle strategy is "ocr"; the default covers the usual bottom caption band.
    x: float = Field(default=0.5, ge=0.0, le=1.0)
    y: float = Field(default=0.85, ge=0.0, le=1.0)
    width: float = Field(default=0.72, ge=0.05, le=1.0)
    height: float = Field(default=0.18, ge=0.03, le=1.0)


class SourceConfiguration(ConfigurationModel):
    strategy: Literal["url", "upload", "asset"] = "url"
    subtitle_strategy: Literal["auto", "embedded", "speech", "ocr"] = "auto"
    ocr_region: OcrRegionConfiguration = Field(default_factory=OcrRegionConfiguration)
    download_quality: Literal["reliable", "best"] = "reliable"
    allow_runtime_fallback: bool = True
    # Test/preview mode: when > 0, limit processing to the first N seconds of the
    # source video so users can evaluate quality before running a long video.
    test_clip_seconds: int = Field(default=0, ge=0, le=3600)


class LanguagesConfiguration(ConfigurationModel):
    source: str = Field(default="auto", min_length=1, max_length=32)
    target: str = Field(default="VI", min_length=1, max_length=32)


class VoiceReferenceSnapshot(ConfigurationModel):
    asset_id: str = ""
    url: str = ""
    path: str = ""
    text: str = ""
    text_path: str = ""
    sha256: str = ""


class VoiceConfiguration(ConfigurationModel):
    voice_id: str = Field(default="vi-VN-HoaiMyNeural", min_length=1, max_length=160)
    provider: Literal["auto", "edge", "omnivoice", "zerotts"] = "auto"
    mode: Literal["", "auto", "design", "clone"] = ""
    rate: int = Field(default=0, ge=-100, le=100)
    instruction: str = Field(default="", max_length=4000)
    reference: VoiceReferenceSnapshot = Field(default_factory=VoiceReferenceSnapshot)
    voice_snapshot: dict[str, Any] = Field(default_factory=dict)


class TranslationConfiguration(ConfigurationModel):
    tone: str = Field(default="Natural and context-aware", max_length=500)
    context: str = Field(default="", max_length=20_000)
    glossary: dict[str, str] = Field(default_factory=dict)
    proper_names: Literal["preserve", "transliterate", "localize"] = "preserve"
    remove_sound_tags: bool = True
    semantic_fidelity: Literal["strict", "balanced", "natural"] = "balanced"


class SubtitleConfiguration(ConfigurationModel):
    style_id: str = Field(default="default", min_length=1, max_length=120)
    style_snapshot: dict[str, Any] = Field(default_factory=dict)
    style_overrides: dict[str, Any] = Field(default_factory=dict)
    cue_density: Literal["compact", "balanced", "relaxed"] = "balanced"
    max_lines: int = Field(default=2, ge=1, le=2)
    position: Literal["bottom", "middle", "top"] = "bottom"


class AudioConfiguration(ConfigurationModel):
    original_volume: float = Field(default=28, ge=0, le=200)
    dubbed_volume: float = Field(default=100, ge=0, le=200)
    ducking: bool = True
    normalize: bool = True


class SpeakerVoiceConfiguration(ConfigurationModel):
    voice_id: str = Field(min_length=1, max_length=160)
    rate: int = Field(default=0, ge=-100, le=100)
    instruction: str = Field(default="", max_length=4000)
    voice_snapshot: dict[str, Any] = Field(default_factory=dict)


class SpeakersConfiguration(ConfigurationModel):
    enabled: bool = False
    min_count: int | None = Field(default=None, ge=1, le=20)
    max_count: int | None = Field(default=None, ge=1, le=20)
    unknown_voice_id: str = ""
    voice_map: dict[str, SpeakerVoiceConfiguration] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_count_range(self):
        if self.min_count and self.max_count and self.min_count > self.max_count:
            raise ValueError("speakers.min_count cannot be greater than speakers.max_count")
        return self


class WorkflowConfiguration(ConfigurationModel):
    transcript_review: bool = False
    speaker_review: bool = False
    audio_review: bool = False
    final_review: bool = True
    auto_render: bool = True


class OutputConfiguration(ConfigurationModel):
    render_mode: Literal["auto", "manual"] = "auto"
    render_quality: Literal["fast", "balanced", "quality"] = "balanced"
    aspect_ratio: Literal["source", "16:9", "9:16", "1:1"] = "source"
    platform: str = Field(default="YouTube", max_length=80)
    format: Literal["mp4", "webm"] = "mp4"


class PublishConfiguration(ConfigurationModel):
    platforms: list[str] = Field(default_factory=list)
    destination_ids: list[str] = Field(default_factory=list)
    publish_date: str | None = None
    publish_time: str | None = None
    schedule_default: str = Field(default="Manual", max_length=120)
    metadata_defaults: dict[str, Any] = Field(default_factory=dict)


class ProductionConfiguration(ConfigurationModel):
    schema_version: Literal[CONFIGURATION_SCHEMA_VERSION] = CONFIGURATION_SCHEMA_VERSION
    source: SourceConfiguration = Field(default_factory=SourceConfiguration)
    languages: LanguagesConfiguration = Field(default_factory=LanguagesConfiguration)
    voice: VoiceConfiguration = Field(default_factory=VoiceConfiguration)
    translation: TranslationConfiguration = Field(default_factory=TranslationConfiguration)
    subtitle: SubtitleConfiguration = Field(default_factory=SubtitleConfiguration)
    audio: AudioConfiguration = Field(default_factory=AudioConfiguration)
    speakers: SpeakersConfiguration = Field(default_factory=SpeakersConfiguration)
    workflow: WorkflowConfiguration = Field(default_factory=WorkflowConfiguration)
    output: OutputConfiguration = Field(default_factory=OutputConfiguration)
    publish: PublishConfiguration = Field(default_factory=PublishConfiguration)


class ResolvedConfiguration(ConfigurationModel):
    configuration: ProductionConfiguration
    provenance: dict[str, str]
    resolved_at: str
    application_defaults_version: int = APPLICATION_DEFAULTS_VERSION


def application_defaults() -> dict[str, Any]:
    defaults = ProductionConfiguration().model_dump(mode="json")
    default_voice_id = get_workspace_settings().default_voice_id
    defaults["voice"]["voice_id"] = default_voice_id
    defaults["speakers"]["unknown_voice_id"] = default_voice_id
    return defaults


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_configuration_json(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Configuration JSON is invalid.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Configuration must be a JSON object.")
    return parsed


def validate_configuration_size(configuration: dict[str, Any]) -> None:
    if len(stable_json(configuration).encode("utf-8")) > MAX_CONFIGURATION_BYTES:
        raise ValueError(f"Configuration exceeds {MAX_CONFIGURATION_BYTES} bytes.")


def migrate_configuration_document(configuration: dict[str, Any]) -> dict[str, Any]:
    """Return a detached document upgraded to the current configuration schema."""
    if not isinstance(configuration, dict):
        raise ValueError("Configuration layer must be an object.")
    migrated = copy.deepcopy(configuration)
    version = migrated.get("schema_version", CONFIGURATION_SCHEMA_VERSION)
    if version != CONFIGURATION_SCHEMA_VERSION:
        raise ValueError(f"Unsupported configuration schema version: {version}.")
    voice = migrated.get("voice")
    if isinstance(voice, dict) and voice.get("voice_id"):
        voice["voice_id"] = canonical_voice_id(str(voice["voice_id"]))
        if voice.get("provider") == "nghitts":
            voice["provider"] = "zerotts"
    speakers = migrated.get("speakers")
    voice_map = speakers.get("voice_map") if isinstance(speakers, dict) else None
    if isinstance(voice_map, dict):
        for assignment in voice_map.values():
            if isinstance(assignment, dict) and assignment.get("voice_id"):
                assignment["voice_id"] = canonical_voice_id(str(assignment["voice_id"]))
    return migrated


def resolve_configuration(
    *,
    preset: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    snapshot_references: bool = True,
) -> ResolvedConfiguration:
    defaults = application_defaults()
    merged = copy.deepcopy(defaults)
    provenance = {path: "application" for path in _leaf_paths(defaults)}

    for source_name, layer in (
        ("preset", preset),
        ("project", project),
        ("video", overrides),
    ):
        if not layer:
            continue
        layer = migrate_configuration_document(layer)
        validate_configuration_layer(layer)
        validate_configuration_size(layer)
        merged = _merge_layer(merged, layer, defaults, provenance, source_name)

    if snapshot_references:
        _snapshot_voice(merged)
        _snapshot_speaker_voices(merged)
        _snapshot_subtitle_style(merged)
    try:
        configuration = ProductionConfiguration.model_validate(merged)
    except ValidationError as exc:
        raise ValueError(_validation_message(exc)) from exc
    return ResolvedConfiguration(
        configuration=configuration,
        provenance=provenance,
        resolved_at=datetime.now(timezone.utc).isoformat(),
    )


def validate_configuration_layer(layer: dict[str, Any]) -> None:
    migrated = migrate_configuration_document(layer)
    _validate_layer_keys(migrated, ProductionConfiguration, "")


def safe_runtime_snapshot() -> dict[str, Any]:
    settings = get_runtime_settings()
    return {
        "tts": {
            "provider": settings.tts_provider,
            "runtime": settings.omnivoice_runtime if settings.tts_provider == "omnivoice" else "",
            "device": settings.omnivoice_device if settings.tts_provider == "omnivoice" else "",
            "endpoint_category": _endpoint_category(settings.omnivoice_api_url),
        },
        "translation": {
            "provider": settings.translation_provider,
            "model": (
                settings.translation_nvidia_model
                if settings.translation_provider == "nvidia"
                else settings.local_translation_model
            ),
            "endpoint_category": _endpoint_category(settings.local_translation_base_url),
        },
        "diarization": {
            "device": settings.diarization_device,
            "endpoint_category": _endpoint_category(settings.diarization_api_url),
        },
        "service_version": "0.1.0",
    }


def sanitize_export(value: Any) -> Any:
    secret_terms = ("token", "secret", "password", "api_key", "authorization", "credential")
    if isinstance(value, dict):
        return {
            str(key): sanitize_export(item)
            for key, item in value.items()
            if not any(term in str(key).casefold() for term in secret_terms)
        }
    if isinstance(value, list):
        return [sanitize_export(item) for item in value]
    return value


def validate_runtime_compatibility(configuration: ProductionConfiguration) -> list[str]:
    settings = get_runtime_settings()
    errors: list[str] = []
    requires_omnivoice = (
        configuration.voice.voice_id != NO_VOICE_ID
        and (
            configuration.voice.provider == "omnivoice"
            or configuration.voice.mode in {"design", "clone"}
        )
    )
    if requires_omnivoice and settings.tts_provider != "omnivoice":
        if not configuration.source.allow_runtime_fallback:
            errors.append("The selected voice requires OmniVoice, but the active TTS runtime is not OmniVoice.")
    if configuration.speakers.enabled and not settings.diarization_api_url:
        errors.append("Speaker detection is enabled, but no diarization runtime is configured.")
    return errors


def _merge_layer(
    base: dict[str, Any],
    layer: dict[str, Any],
    defaults: dict[str, Any],
    provenance: dict[str, str],
    source_name: str,
    path: str = "",
) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, incoming in layer.items():
        current_path = f"{path}.{key}" if path else key
        if _is_reset(incoming):
            if key in defaults:
                result[key] = copy.deepcopy(defaults[key])
                for leaf in _leaf_paths(defaults[key], current_path):
                    provenance[leaf] = f"{source_name}:reset"
            else:
                result.pop(key, None)
            continue

        current = result.get(key)
        default_value = defaults.get(key)
        if isinstance(incoming, dict) and isinstance(current, dict):
            if current_path == "translation.glossary":
                result[key] = _merge_glossary(current, incoming)
                for leaf in _leaf_paths(incoming, current_path):
                    provenance[leaf] = source_name
            else:
                result[key] = _merge_layer(
                    current,
                    incoming,
                    default_value if isinstance(default_value, dict) else {},
                    provenance,
                    source_name,
                    current_path,
                )
            continue

        result[key] = copy.deepcopy(incoming)
        for leaf in _leaf_paths(incoming, current_path):
            provenance[leaf] = source_name
    return result


def _merge_glossary(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    normalized = {str(key).strip().casefold(): (str(key), value) for key, value in base.items()}
    for key, value in incoming.items():
        normalized[str(key).strip().casefold()] = (str(key), value)
    return {original: value for original, value in normalized.values()}


def _validate_layer_keys(layer: dict[str, Any], model: type[BaseModel], path: str) -> None:
    fields = model.model_fields
    for key, value in layer.items():
        current_path = f"{path}.{key}" if path else key
        field = fields.get(key)
        if not field:
            raise ValueError(f"Unknown configuration field: {current_path}.")
        if _is_reset(value) or value is None or not isinstance(value, dict):
            continue
        nested_model = _nested_model(field.annotation)
        if nested_model:
            _validate_layer_keys(value, nested_model, current_path)


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    except TypeError:
        return None
    return None


def _snapshot_voice(configuration: dict[str, Any]) -> None:
    voice_data = configuration.setdefault("voice", {})
    voice_id = str(voice_data.get("voice_id") or "")
    voice = next((item for item in list_voice_options() if item.id == voice_id), None)
    if not voice:
        existing = voice_data.get("voice_snapshot")
        if isinstance(existing, dict) and str(existing.get("id") or "") == voice_id:
            return
        raise ValueError(f"Voice not found: {voice_id}.")
    voice_data["voice_snapshot"] = _voice_snapshot(voice)
    if voice_id == NO_VOICE_ID:
        voice_data["provider"] = "auto"
        voice_data["mode"] = ""
        voice_data["instruction"] = ""
        voice_data["reference"] = {}
        configuration.setdefault("speakers", {})["enabled"] = False
        configuration.setdefault("workflow", {})["speaker_review"] = False
        configuration.setdefault("workflow", {})["audio_review"] = False
        return

    # Voice-dependent values can be inherited from a preset that selected a
    # different voice. The selected library profile is authoritative for its
    # provider, synthesis mode, and clone reference.
    # ZeroTTS community packs may include a preview WAV. That file is only for
    # auditioning the installed latent voice; it is not an OmniVoice cloning
    # reference. An explicit engine always wins over reference-like metadata.
    uses_omnivoice = voice.engine == "omnivoice" or (
        voice.engine not in {"edge", "zerotts"}
        and bool(
            voice.omnivoice_mode
            or voice.reference_audio_url
            or voice.reference_audio_path
            or voice.reference_text
            or voice.reference_text_path
        )
    )
    voice_data["provider"] = "omnivoice" if uses_omnivoice else (voice.engine or "auto")
    voice_data["mode"] = (
        voice.omnivoice_mode or "clone"
        if uses_omnivoice
        else ""
    )
    voice_data["reference"] = (
        {
            "asset_id": "",
            "url": voice.reference_audio_url,
            "path": voice.reference_audio_path,
            "text": voice.reference_text,
            "text_path": voice.reference_text_path,
            "sha256": "",
        }
        if uses_omnivoice
        else {}
    )
    if not voice_data.get("instruction"):
        voice_data["instruction"] = voice.instruction


def _snapshot_speaker_voices(configuration: dict[str, Any]) -> None:
    if str((configuration.get("voice") or {}).get("voice_id") or "") == NO_VOICE_ID:
        configuration.setdefault("speakers", {})["voice_map"] = {}
        return
    voice_options = {item.id: item for item in list_voice_options()}
    voice_map = ((configuration.get("speakers") or {}).get("voice_map") or {})
    for role, assignment in voice_map.items():
        if not isinstance(assignment, dict):
            raise ValueError(f"Invalid speaker voice assignment: {role}.")
        voice_id = str(assignment.get("voice_id") or "")
        voice = voice_options.get(voice_id)
        if voice:
            assignment["voice_snapshot"] = _voice_snapshot(voice)
            if not assignment.get("instruction"):
                assignment["instruction"] = voice.instruction
            continue
        existing = assignment.get("voice_snapshot")
        if not isinstance(existing, dict) or str(existing.get("id") or "") != voice_id:
            raise ValueError(f"Voice not found for speaker role {role}: {voice_id}.")


def _voice_snapshot(voice: VoiceOption) -> dict[str, Any]:
    reference_fingerprint = "|".join(
        (
            voice.reference_audio_url,
            voice.reference_audio_path,
            voice.reference_text,
            voice.reference_text_path,
        )
    )
    return {
        "id": voice.id,
        "name": voice.name,
        "locale": voice.locale,
        "language": voice.language,
        "type": voice.type,
        "omnivoice_mode": voice.omnivoice_mode,
        "reference_sha256": hashlib.sha256(reference_fingerprint.encode("utf-8")).hexdigest(),
    }


def _snapshot_subtitle_style(configuration: dict[str, Any]) -> None:
    subtitle = configuration.setdefault("subtitle", {})
    style_id = str(subtitle.get("style_id") or "default")
    if style_id == NO_SUBTITLE_STYLE_ID:
        subtitle["style_snapshot"] = {}
        return
    styles = list_subtitle_styles().get("styles") or []
    selected = next((item for item in styles if str(item.get("id")) == style_id), None)
    if not selected:
        raise ValueError(f"Subtitle style not found: {style_id}.")
    # A draft copied from an existing job can carry that job's immutable
    # snapshot. When the user selects another preset, the selected ID must win
    # and a fresh snapshot must be taken for the new job.
    style = copy.deepcopy(selected.get("style") or {})
    overrides = subtitle.get("style_overrides")
    if isinstance(overrides, dict):
        style.update(overrides)
    subtitle["style_snapshot"] = normalize_subtitle_style(style)


def _leaf_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict) and value:
        paths: list[str] = []
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_leaf_paths(item, child))
        return paths
    return [prefix] if prefix else []


def _is_reset(value: Any) -> bool:
    return isinstance(value, dict) and value == RESET_MARKER


def _validation_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc") or ())
        messages.append(f"{location}: {error.get('msg')}")
    return "Invalid production configuration: " + "; ".join(messages)


def _endpoint_category(value: str) -> str:
    lowered = (value or "").casefold()
    if "127.0.0.1" in lowered or "localhost" in lowered or "[::1]" in lowered:
        return "local"
    return "remote" if lowered else "not_configured"
