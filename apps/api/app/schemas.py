from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from .models import (
    MediaJobStatus,
    PlatformConnectionStatus,
    PlatformProvider,
    PublicationStatus,
    StepStatus,
    VideoStatus,
)
from .services.production_configuration import ProductionConfiguration


class JobStepOut(BaseModel):
    id: str
    name: str
    status: StepStatus
    progress: int
    runtime_seconds: int
    logs: str
    sort_order: int

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: str
    video_id: str
    status: VideoStatus
    progress: int
    current_step: str
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    steps: list[JobStepOut] = []

    model_config = {"from_attributes": True}


class VideoOut(BaseModel):
    id: str
    status: VideoStatus
    video_url: str
    thumbnail_url: str | None = None
    content: str
    source_language: str
    target_language: str
    voice: str
    platform: str
    publish_date: str | None = None
    publish_time: str | None = None
    workflow_template: str
    progress: int
    last_updated: datetime
    latest_job: JobOut | None = None

    model_config = {"from_attributes": True}


class BulkVideoCreate(BaseModel):
    urls: list[str] = Field(min_length=1)
    source_language: str = "auto"
    target_language: str = "VI"
    voice: str = "vi-VN-HoaiMyNeural"
    platform: str = "YouTube"
    workflow_template: str = "Full Localization"


class VideoPatch(BaseModel):
    status: VideoStatus | None = None
    video_url: str | None = None
    content: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    voice: str | None = None
    platform: str | None = None
    publish_date: str | None = None
    publish_time: str | None = None
    workflow_template: str | None = None


class RunVideosRequest(BaseModel):
    video_ids: list[str] = Field(min_length=1)


class ScheduleRequest(BaseModel):
    video_ids: list[str] = Field(min_length=1)
    publish_date: str
    publish_time: str
    platform: str | None = None


class MediaJobCreate(BaseModel):
    video_url: str = Field(min_length=1, max_length=1024)
    source_title: str | None = None
    thumbnail_url: str | None = None
    content: str = ""
    source_language: str = "auto"
    target_language: str = "VI"
    voice: str = "vi-VN-HoaiMyNeural"
    voice_rate: str = "+0%"
    voice_mode: str = ""
    voice_instruction: str = ""
    voice_reference_audio_url: str = ""
    voice_reference_text: str = ""
    speaker_diarization_enabled: bool = False
    speaker_min_count: int | None = Field(default=None, ge=1, le=20)
    speaker_max_count: int | None = Field(default=None, ge=1, le=20)
    speaker_voice_map: dict[str, dict[str, str]] = Field(default_factory=dict)
    download_quality: str = "reliable"
    render_quality: str = "balanced"
    render_mode: str = "auto"
    burn_subtitles: bool = True
    test_clip_seconds: int = Field(default=0, ge=0, le=3600)
    platform: str = "YouTube"
    publish_date: str | None = None
    publish_time: str | None = None

    @model_validator(mode="after")
    def validate_speaker_count_range(self):
        if self.speaker_min_count and self.speaker_max_count and self.speaker_min_count > self.speaker_max_count:
            raise ValueError("speaker_min_count cannot be greater than speaker_max_count.")
        return self


class MediaJobPatch(BaseModel):
    video_url: str | None = None
    source_title: str | None = None
    thumbnail_url: str | None = None
    content: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    voice: str | None = None
    voice_rate: str | None = None
    voice_mode: str | None = None
    voice_instruction: str | None = None
    voice_reference_audio_url: str | None = None
    voice_reference_text: str | None = None
    speaker_diarization_enabled: bool | None = None
    speaker_min_count: int | None = Field(default=None, ge=1, le=20)
    speaker_max_count: int | None = Field(default=None, ge=1, le=20)
    speaker_voice_map: dict[str, dict[str, str]] | None = None
    download_quality: str | None = None
    render_quality: str | None = None
    render_mode: str | None = None
    test_clip_seconds: int | None = Field(default=None, ge=0, le=3600)
    platform: str | None = None
    publish_date: str | None = None
    publish_time: str | None = None
    status: MediaJobStatus | None = None


class MediaJobVoiceRerenderRequest(BaseModel):
    voice_id: str = Field(min_length=1, max_length=160)


class StorageUsageOut(BaseModel):
    project_data: int
    zerotts_cache: int
    voice_data: int
    logs: int
    total: int


class StorageCleanupRequest(BaseModel):
    target: Literal["temporary", "zerotts_cache"]


class StorageCleanupOut(BaseModel):
    removed_bytes: int
    usage: StorageUsageOut


class MediaJobOut(BaseModel):
    id: str
    video_id: str | None = None
    project_id: str | None = None
    preset_id: str | None = None
    preset_version: int | None = None
    configuration_schema_version: int | None = None
    configuration_snapshot: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("configuration_snapshot", "configuration_snapshot_json"),
    )
    runtime_snapshot: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("runtime_snapshot", "runtime_snapshot_json"),
    )
    video_url: str
    source_title: str | None = None
    thumbnail_url: str | None = None
    content: str
    source_language: str
    target_language: str
    voice: str
    voice_rate: str
    voice_mode: str
    voice_instruction: str
    voice_reference_audio_url: str
    voice_reference_text: str
    speaker_diarization_enabled: bool
    speaker_min_count: int | None = None
    speaker_max_count: int | None = None
    speaker_voice_map: dict[str, dict[str, str]] = Field(default_factory=dict)
    download_quality: str
    render_quality: str
    render_mode: str
    burn_subtitles: bool = True
    test_clip_seconds: int = 0
    platform: str
    publish_date: str | None = None
    publish_time: str | None = None
    status: MediaJobStatus
    progress: int
    current_step: str
    # Live, human-friendly sub-status for the active step (e.g. "Đang tổng hợp
    # giọng…" vs "Đang kết nối lại OmniVoice…"). Populated from the in-memory
    # progress channel, not the DB, so it reflects what is happening right now.
    step_detail: str = ""
    output_url: str | None = None
    error_message: str | None = None
    logs: str
    created_at: datetime
    updated_at: datetime
    # Processing window (set by the worker); used to show a persisted, accurate
    # "completed in" duration. ``started_at`` excludes queue wait.
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_validator("speaker_voice_map", mode="before")
    @classmethod
    def parse_speaker_voice_map(cls, value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value or {}

    @field_validator("configuration_snapshot", "runtime_snapshot", mode="before")
    @classmethod
    def parse_snapshot_json(cls, value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return value


class ProductionPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=5000)
    category: str = Field(default="General", min_length=1, max_length=80)
    cover_asset_id: str | None = Field(default=None, max_length=160)
    configuration: dict[str, Any] = Field(default_factory=dict)
    change_note: str = Field(default="Initial version", max_length=1000)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "Historical drama",
                    "description": "Context-aware Vietnamese localization for historical series.",
                    "category": "Series",
                    "configuration": {
                        "voice": {"voice_id": "vi-VN-NamMinhNeural", "rate": -4},
                        "translation": {"tone": "Literary, historically aware, natural Vietnamese"},
                        "subtitle": {"style_id": "default", "position": "bottom"},
                    },
                }
            ]
        }
    }


class ProductionPresetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    cover_asset_id: str | None = Field(default=None, max_length=160)
    configuration: dict[str, Any] | None = None
    change_note: str = Field(default="Updated preset", max_length=1000)


class ProductionPresetVersionOut(BaseModel):
    version: int
    schema_version: int
    configuration: dict[str, Any]
    change_note: str
    created_at: datetime


class ProductionPresetOut(BaseModel):
    id: str
    name: str
    description: str
    category: str
    cover_asset_id: str | None = None
    schema_version: int
    current_version: int
    configuration: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    versions: list[ProductionPresetVersionOut] = Field(default_factory=list)


class ProductionPresetImport(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    preset: dict[str, Any]


class ConfigurationResolveRequest(BaseModel):
    preset_id: str | None = None
    preset_version: int | None = None
    project_id: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "preset_id": "preset-id",
                    "project_id": "project-id",
                    "overrides": {"voice": {"rate": 8}, "output": {"platform": "TikTok"}},
                }
            ]
        }
    }


class ConfigurationValidationOut(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] | None = None


class ConfigurationResolveOut(BaseModel):
    valid: bool = True
    configuration: ProductionConfiguration | None = None
    provenance: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    resolved_at: str = ""
    application_defaults_version: int = 1
    preset_id: str | None = None
    preset_version: int | None = None
    project_id: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=5000)
    cover_asset_id: str | None = Field(default=None, max_length=160)
    source_language: str = Field(default="auto", min_length=1, max_length=32)
    target_language: str = Field(default="VI", min_length=1, max_length=32)
    origin_preset_id: str | None = None
    origin_preset_version: int | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    glossary: dict[str, str] = Field(default_factory=dict)
    voice_cast: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "Chinese History Channel",
                    "description": "Reusable workspace for long-form historical videos.",
                    "source_language": "ZH",
                    "target_language": "VI",
                    "origin_preset_id": "preset-id",
                    "glossary": {"周": "Chu", "汉": "Hán"},
                    "voice_cast": {
                        "narrator": {"voice_id": "vi-VN-NamMinhNeural", "rate": -4}
                    },
                }
            ]
        }
    }


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    cover_asset_id: str | None = Field(default=None, max_length=160)
    source_language: str | None = Field(default=None, min_length=1, max_length=32)
    target_language: str | None = Field(default=None, min_length=1, max_length=32)
    defaults: dict[str, Any] | None = None
    glossary: dict[str, str] | None = None
    voice_cast: dict[str, Any] | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    cover_asset_id: str | None = None
    source_language: str
    target_language: str
    origin_preset_id: str | None = None
    origin_preset_version: int | None = None
    defaults_schema_version: int
    defaults_revision: int
    defaults: dict[str, Any]
    glossary: dict[str, str]
    voice_cast: dict[str, Any]
    video_count: int = 0
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ProjectVideoCreate(BaseModel):
    video_url: str = Field(min_length=1, max_length=1024)
    source_title: str | None = Field(default=None, max_length=500)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    content: str = ""
    platform: str = Field(default="YouTube", max_length=80)
    overrides: dict[str, Any] = Field(default_factory=dict)
    sort_order: int | None = Field(default=None, ge=0)


class ProjectVideoPatch(BaseModel):
    overrides: dict[str, Any] | None = None
    sort_order: int | None = Field(default=None, ge=0)


class ProductionVideoOut(BaseModel):
    id: str
    project_id: str | None = None
    video_url: str
    thumbnail_url: str | None = None
    content: str
    source_language: str
    target_language: str
    voice: str
    platform: str
    sort_order: int = 0
    overrides_revision: int = 1
    overrides: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QuickVideoJobCreate(BaseModel):
    source: ProjectVideoCreate
    preset_id: str | None = None
    preset_version: int | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    run: bool = False

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "source": {
                        "video_url": "https://example.com/video.mp4",
                        "source_title": "One-off video",
                        "platform": "YouTube",
                    },
                    "preset_id": "preset-id",
                    "overrides": {"languages": {"source": "auto", "target": "VI"}},
                    "run": False,
                }
            ]
        }
    }


class ProjectJobCreate(BaseModel):
    video_id: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    run: bool = False

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "video_id": "video-id",
                    "overrides": {"voice": {"rate": 5}},
                    "run": False,
                }
            ]
        }
    }


class CreatedMediaJobOut(BaseModel):
    video: ProductionVideoOut
    job: MediaJobOut


class SpeakerVoiceAssignment(BaseModel):
    voice: str = Field(min_length=1, max_length=160)
    voice_rate: str = Field(default="+0%", max_length=16)


class SpeakerMappingPatch(BaseModel):
    speakers: dict[str, SpeakerVoiceAssignment]


class SpeakerTurnOut(BaseModel):
    start: float
    end: float
    speaker_id: str
    overlap: bool = False
    text: str = ""


class SpeakerOut(BaseModel):
    id: str
    total_seconds: float
    sample_start: float
    sample_end: float
    sample_text: str = ""
    voice: str
    voice_rate: str


class SpeakerReviewOut(BaseModel):
    job_id: str
    status: MediaJobStatus
    source_video_url: str | None = None
    duration_seconds: float = 0
    model: str = ""
    device: str = ""
    speakers: list[SpeakerOut]
    turns: list[SpeakerTurnOut]


class MediaJobLogsOut(BaseModel):
    job_id: str
    logs: str


class MediaJobOutputOut(BaseModel):
    job_id: str
    status: MediaJobStatus
    output_url: str | None = None
    error_message: str | None = None


class ReviewSubtitleOut(BaseModel):
    start: str
    end: str
    original_text: str
    translated_text: str


class VideoReviewOut(BaseModel):
    job: MediaJobOut
    review_status: str
    source_video_url: str | None = None
    localized_video_url: str | None = None
    original_subtitle_url: str | None = None
    translated_subtitle_url: str | None = None
    subtitle_rows: list[ReviewSubtitleOut]
    qa_checklist: list[str]


class AudioCuePatch(BaseModel):
    id: str
    start: float = Field(ge=0)
    end: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0, le=2)
    muted: bool | None = None
    locked: bool | None = None


class AudioCuePatchRequest(BaseModel):
    cues: list[AudioCuePatch] = []
    background_volume: float | None = Field(default=None, ge=0, le=1)


class AudioCueManifestOut(BaseModel):
    job_id: str
    source_video_url: str | None = None
    translated_subtitle_url: str | None = None
    background_audio_url: str | None = None
    target_audio_preview_url: str | None = None
    duration_seconds: float
    tracks: dict


class SubtitleStyleIn(BaseModel):
    id: str | None = None
    name: str = Field(default="Subtitle Style", min_length=1, max_length=120)
    style: dict
    make_active: bool = True


class SubtitleStyleActivePatch(BaseModel):
    style_id: str


class SubtitleStylesOut(BaseModel):
    active_style_id: str
    styles: list[dict]


class SubtitleStylePreviewRequest(BaseModel):
    job_id: str
    subtitle_style: dict | None = None
    at_seconds: float | None = Field(default=None, ge=0)


class SubtitleStylePreviewOut(BaseModel):
    preview_url: str
    subtitle_style: dict


class SubtitleRegionDetectOut(BaseModel):
    detected: bool
    region: dict
    subtitle_style: dict


class RuntimeSettingsOut(BaseModel):
    tts_provider: str = "edge"
    prefer_local_gpu: bool = True
    prefer_existing_subtitles: bool = True
    nvidia_api_key: str = ""
    nvidia_api_key_configured: bool = False
    omnivoice_runtime: str = "auto"
    omnivoice_device: str = "auto"
    omnivoice_api_url: str = ""
    omnivoice_api_key: str = ""
    omnivoice_mode: str = "auto"
    omnivoice_instruct: str = ""
    omnivoice_ref_audio_url: str = ""
    omnivoice_ref_audio_path: str = ""
    omnivoice_ref_text: str = ""
    omnivoice_ref_text_path: str = ""
    translation_provider: str = "nvidia"
    translation_nvidia_model: str = "nvidia/chatgpt-oss-120b"
    local_translation_base_url: str = "http://127.0.0.1:11434/v1"
    local_translation_model: str = "qwen2.5:14b"
    local_translation_api_key: str = ""
    diarization_api_url: str = "http://127.0.0.1:8010"
    diarization_device: str = "auto"
    huggingface_token: str = ""
    huggingface_token_configured: bool = False
    groq_api_key: str = ""
    groq_api_key_configured: bool = False
    ngrok_authtoken: str = ""
    ngrok_authtoken_configured: bool = False
    ngrok_domain: str = ""


class RuntimeSettingsPatch(BaseModel):
    tts_provider: str | None = None
    prefer_local_gpu: bool | None = None
    prefer_existing_subtitles: bool | None = None
    nvidia_api_key: str | None = None
    omnivoice_runtime: str | None = None
    omnivoice_device: str | None = None
    omnivoice_api_url: str | None = None
    omnivoice_api_key: str | None = None
    omnivoice_mode: str | None = None
    omnivoice_instruct: str | None = None
    omnivoice_ref_audio_url: str | None = None
    omnivoice_ref_audio_path: str | None = None
    omnivoice_ref_text: str | None = None
    omnivoice_ref_text_path: str | None = None
    translation_provider: str | None = None
    translation_nvidia_model: str | None = None
    local_translation_base_url: str | None = None
    local_translation_model: str | None = None
    local_translation_api_key: str | None = None
    diarization_api_url: str | None = None
    diarization_device: str | None = None
    huggingface_token: str | None = None
    groq_api_key: str | None = None
    ngrok_authtoken: str | None = None
    ngrok_domain: str | None = None


class TranslationRouterStatusOut(BaseModel):
    ready: bool = False
    running: bool = False
    state: str
    message: str
    configured_model: str = ""
    models: list[str] = Field(default_factory=list)


class WorkspaceSettingsOut(BaseModel):
    default_voice_id: str = "vi-VN-HoaiMyNeural"


class WorkspaceSettingsPatch(BaseModel):
    default_voice_id: str | None = Field(default=None, min_length=1, max_length=160)


class PlatformProviderConfigPatch(BaseModel):
    client_id: str | None = Field(default=None, max_length=512)
    client_secret: str | None = Field(default=None, max_length=4096)
    defaults: dict[str, Any] | None = None


class FacebookAccessTokenConnect(BaseModel):
    access_token: str = Field(min_length=20, max_length=8192)


class PlatformProviderConfigOut(BaseModel):
    provider: PlatformProvider
    client_id: str = ""
    client_secret_configured: bool = False
    callback_url: str
    configured: bool = False
    defaults: dict[str, Any] = Field(default_factory=dict)


class PlatformDestinationPatch(BaseModel):
    is_default: bool | None = None
    active: bool | None = None
    defaults: dict[str, Any] | None = None


class PlatformDestinationOut(BaseModel):
    id: str
    connection_id: str
    provider: PlatformProvider
    provider_destination_id: str
    name: str
    avatar_url: str = ""
    is_default: bool
    active: bool
    capabilities: dict[str, Any] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class PlatformConnectionOut(BaseModel):
    id: str
    provider: PlatformProvider
    provider_account_id: str
    account_name: str
    avatar_url: str = ""
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    status: PlatformConnectionStatus
    last_error: str = ""
    destinations: list[PlatformDestinationOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class OAuthStartOut(BaseModel):
    authorization_url: str


class PublicationTargetIn(BaseModel):
    destination_id: str
    media_type: str = Field(default="auto", pattern="^(auto|video|reel)$")
    title: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=20_000)
    scheduled_at: datetime | None = None
    timezone: str = Field(default="UTC", max_length=80)
    settings: dict[str, Any] = Field(default_factory=dict)


class PublicationCreate(BaseModel):
    targets: list[PublicationTargetIn] = Field(min_length=1, max_length=2)


class PublicationOut(BaseModel):
    id: str
    job_id: str
    destination_id: str
    provider: PlatformProvider
    destination_name: str = ""
    status: PublicationStatus
    media_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: datetime | None = None
    timezone: str
    remote_media_id: str = ""
    remote_url: str = ""
    progress: int
    retry_count: int
    error_message: str = ""
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class PlatformOverviewOut(BaseModel):
    encryption_configured: bool
    providers: list[PlatformProviderConfigOut]
    connections: list[PlatformConnectionOut]
    publication_counts: dict[str, int] = Field(default_factory=dict)
    latest_publication: PublicationOut | None = None


class DiarizationLocalStatusOut(BaseModel):
    state: str
    reachable: bool = False
    managed: bool = False
    device: str = ""
    api_url: str = ""
    pid: int | None = None
    python_path: str = ""
    started_at: float | None = None
    error: str | None = None
    log_path: str = ""
    model: str | None = None
    setup_command: str | None = None
    message: str | None = None


class OmniVoiceRuntimeOptionOut(BaseModel):
    id: str
    kind: str
    label: str
    description: str
    available: bool = True
    device: str
    api_url: str = ""
    requires_url: bool = False
    recommended: bool = False
    warning: str = ""
    launch_args: list[str] = Field(default_factory=list)


class OmniVoiceRuntimeOptionsOut(BaseModel):
    recommended_id: str
    local_api_url: str
    gpu_count: int
    gpus: list[dict]
    torch: dict
    options: list[OmniVoiceRuntimeOptionOut]
    prefer_local_gpu: bool = True
    effective_tts_runtime: str = "edge"


class OmniVoiceLocalStatusOut(BaseModel):
    state: str
    reachable: bool = False
    managed: bool = False
    runtime: str = ""
    device: str = ""
    api_url: str = ""
    pid: int | None = None
    python_path: str = ""
    started_at: float | None = None
    error: str | None = None
    log_path: str = ""
    model: str | None = None
    setup_command: str | None = None
    message: str | None = None


class OmniVoiceLocalSetupOut(BaseModel):
    state: str
    busy: bool = False
    message: str = ""
    error: str = ""
    target: str = ""
    venv_path: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    log_tail: str = ""


class ColabAuthCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=2048)


class ColabAccountSwitchIn(BaseModel):
    slug: str = Field(min_length=1, max_length=200)


class ColabAccountOut(BaseModel):
    slug: str
    email: str = ""
    name: str = ""
    picture: str = ""
    active: bool = False
    last_used_at: float | None = None


class ColabAccountsOut(BaseModel):
    accounts: list[ColabAccountOut] = []
    active_email: str = ""


class OmniVoiceColabStatusOut(BaseModel):
    state: str
    reachable: bool = False
    api_url: str = ""
    device: str = ""
    gpu_name: str = ""
    gpu_memory_gb: float | None = None
    quota_state: str = "unknown"
    quota_message: str = ""
    account_hint: str = ""
    account_email: str = ""
    account_name: str = ""
    account_picture: str = ""
    session_started_at: float | None = None
    session_age_seconds: int | None = None
    updated_at: float | None = None
    error: str | None = None
    model: str | None = None
    authorization_url: str = ""
    needs_auth_code: bool = False
    wsl_available: bool = False
    distro_available: bool = False
    cli_installed: bool = False
    cli_version: str = ""
    wsl_distro: str = ""
    session_name: str = ""
    setup_command: str = ""
    log_path: str = ""


class VoiceOptionIn(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    locale: str = "custom"
    language: str = "Custom"
    type: str = "Custom"
    description: str = ""
    omnivoice_mode: str = ""
    reference_audio_url: str = ""
    reference_audio_path: str = ""
    reference_text: str = ""
    reference_text_path: str = ""
    instruction: str = ""


class VoiceOptionOut(BaseModel):
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
    engine: str = ""
    removable: bool = False


class ZeroTTSCommunityVoiceOut(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    language: str = "vi"
    preview_url: str = ""
    installed: bool = False
    installed_voice_id: str = ""


class VoiceReferenceUploadOut(BaseModel):
    filename: str
    path: str
    url: str
    duration: float = 0.0


class VoiceReferenceDownloadRequest(BaseModel):
    video_url: str = Field(min_length=1, max_length=4096)
    audio_format: Literal["wav", "mp3"] = "wav"


class VoiceReferenceTrimRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    audio_format: Literal["wav", "mp3"] = "wav"


class VoiceReferenceWaveformRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    points: int = Field(default=600, ge=180, le=1200)


class VoiceReferenceWaveformOut(BaseModel):
    duration: float
    peaks: list[float]


class SourceVideoUploadOut(BaseModel):
    filename: str
    path: str
    url: str


class SourceVideoPreviewRequest(BaseModel):
    video_url: str = Field(min_length=1, max_length=4096)
    download_quality: Literal["reliable", "best"] = "reliable"
