from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


class VideoStatus(str, enum.Enum):
    draft = "draft"
    queued = "queued"
    processing = "processing"
    needs_review = "needs_review"
    scheduled = "scheduled"
    published = "published"
    failed = "failed"


class MediaJobStatus(str, enum.Enum):
    draft = "draft"
    queued = "queued"
    downloading = "downloading"
    transcribing = "transcribing"
    translating = "translating"
    speaker_review = "speaker_review"
    tts_generating = "tts_generating"
    audio_review = "audio_review"
    rendering = "rendering"
    ready = "ready"
    cancelled = "cancelled"
    failed = "failed"


class StepStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class PlatformProvider(str, enum.Enum):
    youtube = "youtube"
    facebook = "facebook"


class PlatformConnectionStatus(str, enum.Enum):
    active = "active"
    attention = "attention"
    disconnected = "disconnected"


class PublicationStatus(str, enum.Enum):
    queued = "queued"
    uploading = "uploading"
    processing = "processing"
    scheduled = "scheduled"
    published = "published"
    failed = "failed"
    cancelled = "cancelled"


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[VideoStatus] = mapped_column(Enum(VideoStatus), default=VideoStatus.queued)
    video_url: Mapped[str] = mapped_column(String(1024))
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    source_language: Mapped[str] = mapped_column(String(16), default="auto")
    target_language: Mapped[str] = mapped_column(String(16), default="DE")
    voice: Mapped[str] = mapped_column(String(120), default="auto")
    platform: Mapped[str] = mapped_column(String(80), default="YouTube")
    publish_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    publish_time: Mapped[str | None] = mapped_column(String(20), nullable=True)
    workflow_template: Mapped[str] = mapped_column(String(120), default="Full Localization")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    jobs: Mapped[list["Job"]] = relationship(back_populates="video", cascade="all, delete-orphan")
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="video", cascade="all, delete-orphan")
    project_memberships: Mapped[list["ProjectVideo"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
    )
    media_jobs: Mapped[list["MediaJob"]] = relationship(back_populates="video")


class MediaJob(Base):
    __tablename__ = "media_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    preset_id: Mapped[str | None] = mapped_column(ForeignKey("production_presets.id"), nullable=True)
    preset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    configuration_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    configuration_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_url: Mapped[str] = mapped_column(String(1024))
    source_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    source_language: Mapped[str] = mapped_column(String(16), default="auto")
    target_language: Mapped[str] = mapped_column(String(16), default="VI")
    voice: Mapped[str] = mapped_column(String(120), default="auto")
    voice_rate: Mapped[str] = mapped_column(String(16), default="+0%")
    voice_mode: Mapped[str] = mapped_column(String(16), default="")
    voice_instruction: Mapped[str] = mapped_column(Text, default="")
    voice_reference_audio_url: Mapped[str] = mapped_column(String(2048), default="")
    voice_reference_text: Mapped[str] = mapped_column(Text, default="")
    speaker_diarization_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    speaker_min_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speaker_max_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speaker_voice_map: Mapped[str] = mapped_column(Text, default="{}")
    download_quality: Mapped[str] = mapped_column(String(32), default="reliable")
    render_quality: Mapped[str] = mapped_column(String(32), default="balanced")
    render_mode: Mapped[str] = mapped_column(String(16), default="auto")
    burn_subtitles: Mapped[bool] = mapped_column(Boolean, default=True)
    test_clip_seconds: Mapped[int] = mapped_column(Integer, default=0)
    platform: Mapped[str] = mapped_column(String(80), default="YouTube")
    publish_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    publish_time: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[MediaJobStatus] = mapped_column(Enum(MediaJobStatus), default=MediaJobStatus.draft)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str] = mapped_column(String(120), default="Draft")
    output_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Wall-clock processing window of the current/last run. ``started_at`` is set
    # when the worker actually begins processing (excludes queue wait) and reset
    # on retry; ``completed_at`` is stamped when the job reaches a terminal state.
    # Persisting both lets the UI show a stable "completed in" duration on every
    # visit instead of a client-side timer that only works while watching live.
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    video: Mapped[Video | None] = relationship(back_populates="media_jobs")
    project: Mapped["Project"] = relationship(back_populates="media_jobs")
    preset: Mapped["ProductionPreset"] = relationship()
    publications: Mapped[list["Publication"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class ProductionPreset(Base):
    __tablename__ = "production_presets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="General")
    cover_asset_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    configuration_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    versions: Mapped[list["PresetVersion"]] = relationship(
        back_populates="preset",
        cascade="all, delete-orphan",
        order_by="PresetVersion.version",
    )


class PresetVersion(Base):
    __tablename__ = "preset_versions"
    __table_args__ = (UniqueConstraint("preset_id", "version", name="uq_preset_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    preset_id: Mapped[str] = mapped_column(ForeignKey("production_presets.id"))
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    configuration_json: Mapped[str] = mapped_column(Text, default="{}")
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    preset: Mapped[ProductionPreset] = relationship(back_populates="versions")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    cover_asset_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_language: Mapped[str] = mapped_column(String(32), default="auto")
    target_language: Mapped[str] = mapped_column(String(32), default="VI")
    origin_preset_id: Mapped[str | None] = mapped_column(ForeignKey("production_presets.id"), nullable=True)
    origin_preset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    defaults_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    defaults_revision: Mapped[int] = mapped_column(Integer, default=1)
    defaults_json: Mapped[str] = mapped_column(Text, default="{}")
    glossary_json: Mapped[str] = mapped_column(Text, default="{}")
    voice_cast_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    origin_preset: Mapped[ProductionPreset | None] = relationship()
    video_memberships: Mapped[list["ProjectVideo"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectVideo.sort_order",
    )
    media_jobs: Mapped[list[MediaJob]] = relationship(back_populates="project")


class ProjectVideo(Base):
    __tablename__ = "project_videos"
    __table_args__ = (UniqueConstraint("project_id", "video_id", name="uq_project_video"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    video_overrides_json: Mapped[str] = mapped_column(Text, default="{}")
    overrides_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    video: Mapped[Video] = relationship(back_populates="project_memberships")
    project: Mapped[Project] = relationship(back_populates="video_memberships")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"))
    status: Mapped[VideoStatus] = mapped_column(Enum(VideoStatus), default=VideoStatus.queued)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str] = mapped_column(String(120), default="Queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    video: Mapped[Video] = relationship(back_populates="jobs")
    steps: Mapped[list["JobStep"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobStep(Base):
    __tablename__ = "job_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[StepStatus] = mapped_column(Enum(StepStatus), default=StepStatus.pending)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    runtime_seconds: Mapped[int] = mapped_column(Integer, default=0)
    logs: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    job: Mapped[Job] = relationship(back_populates="steps")


class Voice(Base):
    __tablename__ = "voices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    locale: Mapped[str] = mapped_column(String(16), default="en-US")
    style: Mapped[str] = mapped_column(String(80), default="Narration")


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"))
    platform: Mapped[str] = mapped_column(String(80))
    publish_date: Mapped[str] = mapped_column(String(20))
    publish_time: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), default="scheduled")

    video: Mapped[Video] = relationship(back_populates="schedules")


class PlatformProviderConfig(Base):
    __tablename__ = "platform_provider_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[PlatformProvider] = mapped_column(Enum(PlatformProvider), unique=True)
    client_id: Mapped[str] = mapped_column(String(512), default="")
    client_secret_encrypted: Mapped[str] = mapped_column(Text, default="")
    defaults_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PlatformConnection(Base):
    __tablename__ = "platform_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[PlatformProvider] = mapped_column(Enum(PlatformProvider))
    provider_account_id: Mapped[str] = mapped_column(String(255))
    account_name: Mapped[str] = mapped_column(String(500), default="")
    avatar_url: Mapped[str] = mapped_column(String(2048), default="")
    access_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[PlatformConnectionStatus] = mapped_column(
        Enum(PlatformConnectionStatus),
        default=PlatformConnectionStatus.active,
    )
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    destinations: Mapped[list["PlatformDestination"]] = relationship(
        back_populates="connection",
        cascade="all, delete-orphan",
    )


class PlatformDestination(Base):
    __tablename__ = "platform_destinations"
    __table_args__ = (
        UniqueConstraint("connection_id", "provider_destination_id", name="uq_platform_destination"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(ForeignKey("platform_connections.id"))
    provider: Mapped[PlatformProvider] = mapped_column(Enum(PlatformProvider))
    provider_destination_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500))
    avatar_url: Mapped[str] = mapped_column(String(2048), default="")
    access_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities_json: Mapped[str] = mapped_column(Text, default="{}")
    defaults_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    connection: Mapped[PlatformConnection] = relationship(back_populates="destinations")
    publications: Mapped[list["Publication"]] = relationship(back_populates="destination")


class PlatformOAuthState(Base):
    __tablename__ = "platform_oauth_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[PlatformProvider] = mapped_column(Enum(PlatformProvider))
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("job_id", "destination_id", "idempotency_key", name="uq_publication_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("media_jobs.id"))
    destination_id: Mapped[str] = mapped_column(ForeignKey("platform_destinations.id"))
    provider: Mapped[PlatformProvider] = mapped_column(Enum(PlatformProvider))
    status: Mapped[PublicationStatus] = mapped_column(Enum(PublicationStatus), default=PublicationStatus.queued)
    media_type: Mapped[str] = mapped_column(String(32), default="video")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    idempotency_key: Mapped[str] = mapped_column(String(64))
    upload_session_url_encrypted: Mapped[str] = mapped_column(Text, default="")
    remote_media_id: Mapped[str] = mapped_column(String(255), default="")
    remote_url: Mapped[str] = mapped_column(String(2048), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped[MediaJob] = relationship(back_populates="publications")
    destination: Mapped[PlatformDestination] = relationship(back_populates="publications")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# --- AI assistant (owned by the assistant feature) -------------------------
# New tables only; ``Base.metadata.create_all`` creates them on startup, so no
# ALTER TABLE migration is needed in ``ensure_schema_compatibility``.


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), default="")
    # chat | write | strategy — the mode last used; each message records its own.
    mode: Mapped[str] = mapped_column(String(32), default="chat")
    # Optional dubbing job this conversation is about (no FK: jobs may be deleted).
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["AssistantMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AssistantMessage.seq",
    )


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("assistant_conversations.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    # user | assistant | tool
    role: Mapped[str] = mapped_column(String(16))
    # Text shown in the UI.
    content: Mapped[str] = mapped_column(Text, default="")
    # Exact text sent to the model for user turns (content + mode/context header);
    # replayed verbatim so the history stays append-only for prompt caching.
    llm_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(32), default="")
    # assistant: [{"id","name","input"}]; tool: unused.
    tool_calls_json: Mapped[str] = mapped_column(Text, default="[]")
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    # Provider-native assistant content (Anthropic content blocks incl. thinking
    # signatures) so later turns can replay it unchanged.
    native_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    stop_reason: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    usage_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[AssistantConversation] = relationship(back_populates="messages")


# --- Channels (multi-channel dashboard: YouTube / Facebook Pages / TikTok) ---------
# Owned by the channels backend (app/services/channels, app/channels_api.py).
# Platform and status are plain strings (not Enums) so adding a platform never
# needs a SQLite enum migration. Tokens are Fernet-encrypted at rest
# (app/services/channels/crypto.py); they are never returned by the API.


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_channel_platform_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(32))  # youtube | facebook | tiktok
    source: Mapped[str] = mapped_column(String(16), default="manual")  # oauth | manual
    # Platform id (YouTube channel id, Facebook Page id, TikTok open_id). NULL for manual channels.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Owning login (Google account / Facebook user id) — lets one login own several channels.
    account_external_id: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(500), default="")
    handle: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(2048), default="")
    avatar_url: Mapped[str] = mapped_column(String(2048), default="")
    # connected | needs_reauth | error | disconnected | manual
    status: Mapped[str] = mapped_column(String(32), default="manual")
    last_error: Mapped[str] = mapped_column(Text, default="")
    access_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    # Google/TikTok: OAuth refresh token. Facebook: long-lived *user* token used to re-mint the Page token.
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[str] = mapped_column(Text, default="")
    connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    snapshots: Mapped[list["ChannelSnapshot"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    videos: Mapped[list["ChannelVideo"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class ChannelSnapshot(Base):
    """One row per channel per day (UTC). Later syncs on the same day overwrite it."""

    __tablename__ = "channel_snapshots"
    __table_args__ = (UniqueConstraint("channel_id", "date", name="uq_channel_snapshot_day"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="sync")  # sync | manual | csv
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    channel: Mapped[Channel] = relationship(back_populates="snapshots")


class ChannelVideo(Base):
    __tablename__ = "channel_videos"
    __table_args__ = (UniqueConstraint("channel_id", "external_id", name="uq_channel_video_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), default="video")  # video | short | post
    title: Mapped[str] = mapped_column(String(1000), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_url: Mapped[str] = mapped_column(String(2048), default="")
    url: Mapped[str] = mapped_column(String(2048), default="")
    # Optional: "this is the dubbed version of media job X" (light link, no FK cascade).
    media_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16), default="sync")  # sync | manual | csv
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    channel: Mapped[Channel] = relationship(back_populates="videos")


class ChannelOAuthState(Base):
    """Pending OAuth authorization (state + PKCE verifier), polled by the desktop UI."""

    __tablename__ = "channel_oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    code_verifier_encrypted: Mapped[str] = mapped_column(Text, default="")
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | connected | error
    error: Mapped[str] = mapped_column(Text, default="")
    channel_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --- Content calendar (owned by the calendar feature: app/calendar_api.py) ----------
# A planned / published post. VietDub does not upload to the platforms: the user posts
# manually and marks the post published (or channel sync auto-matches it, see
# app/services/content_calendar.py). New table only -> created by ``create_all``.


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(500), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags_json: Mapped[str] = mapped_column(Text, default="[]")
    # youtube | facebook | tiktok, or NULL (idea not tied to a platform yet).
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Light links (no FK cascade): jobs and synced videos may be deleted independently.
    media_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    channel_video_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Naive UTC. NULL = unscheduled (idea backlog).
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # idea | draft | scheduled | published | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_url: Mapped[str] = mapped_column(String(2048), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(16), default="")
    label: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# --- Viral discovery (owned by app/viral_api.py, services/viral/*) ----------------------
# New tables only -> created by ``create_all``. Times are naive UTC.


class ViralTopic(Base):
    """A watched topic ("chủ đề theo dõi"): keywords per platform, or a creator profile."""

    __tablename__ = "viral_topics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(16), default="keyword")  # keyword | creator
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)  # creator topics only
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    # {"youtube": ["cooking hacks", ...], "douyin": ["做饭技巧", ...]}
    keywords_json: Mapped[str] = mapped_column(Text, default="{}")
    # {"platforms": [...], "window": "7d", "region": "US", "duration": "any", "limit": 20, "min_views": 0}
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    watched: Mapped[bool] = mapped_column(Boolean, default=True)  # included in periodic re-scan
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scan_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ViralVideo(Base):
    __tablename__ = "viral_videos"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_viral_video_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(16), index=True)  # youtube | douyin
    external_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048), default="")
    title: Mapped[str] = mapped_column(String(1000), default="")
    title_vi: Mapped[str] = mapped_column(String(1000), default="")  # LLM translation (lazy)
    description: Mapped[str] = mapped_column(Text, default="")
    creator_name: Mapped[str] = mapped_column(String(500), default="")
    creator_id: Mapped[str] = mapped_column(String(255), default="")
    creator_url: Mapped[str] = mapped_column(String(2048), default="")
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_url: Mapped[str] = mapped_column(String(2048), default="")
    language: Mapped[str] = mapped_column(String(16), default="")
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collects: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tier: Mapped[str] = mapped_column(String(16), default="")
    views_per_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    breakdown_json: Mapped[str] = mapped_column(Text, default="{}")
    topic_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    # new | saved | dismissed | dubbed
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    watched: Mapped[bool] = mapped_column(Boolean, default=False)  # "danh sách theo dõi"
    media_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    calendar_post_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    analysis_json: Mapped[str] = mapped_column(Text, default="")
    analysis_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="")  # provider id
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    snapshots: Mapped[list["ViralSnapshot"]] = relationship(
        back_populates="video", cascade="all, delete-orphan", order_by="ViralSnapshot.scanned_at"
    )


class ViralSnapshot(Base):
    """Stats of one video at one scan; consecutive snapshots give real growth (momentum)."""

    __tablename__ = "viral_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("viral_videos.id", ondelete="CASCADE"), index=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collects: Mapped[int | None] = mapped_column(Integer, nullable=True)

    video: Mapped[ViralVideo] = relationship(back_populates="snapshots")
