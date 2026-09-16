from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session, selectinload

from .database import Base, engine, get_db
from .job_runner import create_job, start_mock_job
from .models import (
    AuditLog,
    Job,
    MediaJob,
    MediaJobStatus,
    Schedule,
    Video,
    VideoStatus,
)
from .production_api import (
    ensure_builtin_production_preset,
    router as production_router,
)
from .schemas import (
    AudioCueManifestOut,
    AudioCuePatchRequest,
    BulkVideoCreate,
    DiarizationLocalStatusOut,
    JobOut,
    MediaJobCreate,
    MediaJobLogsOut,
    MediaJobOut,
    MediaJobOutputOut,
    MediaJobPatch,
    MediaJobVoiceRerenderRequest,
    ColabAccountSwitchIn,
    ColabAccountsOut,
    ColabAuthCodeIn,
    OmniVoiceLocalSetupOut,
    OmniVoiceLocalStatusOut,
    OmniVoiceColabStatusOut,
    OmniVoiceRuntimeOptionsOut,
    RunVideosRequest,
    RuntimeSettingsOut,
    RuntimeSettingsPatch,
    ScheduleRequest,
    SpeakerMappingPatch,
    SpeakerOut,
    SpeakerReviewOut,
    SpeakerTurnOut,
    SourceVideoPreviewRequest,
    SourceVideoUploadOut,
    SubtitleStyleActivePatch,
    SubtitleStyleIn,
    SubtitleRegionDetectOut,
    SubtitleStylePreviewOut,
    SubtitleStylePreviewRequest,
    SubtitleStylesOut,
    VideoOut,
    VideoPatch,
    VideoReviewOut,
    VoiceOptionIn,
    VoiceOptionOut,
    VoiceReferenceDownloadRequest,
    VoiceReferenceTrimRequest,
    VoiceReferenceUploadOut,
    VoiceReferenceWaveformOut,
    VoiceReferenceWaveformRequest,
    WorkspaceSettingsOut,
    WorkspaceSettingsPatch,
    ZeroTTSCommunityVoiceOut,
)
from .services.audio_cues import (
    load_audio_cue_manifest,
    patch_audio_cue_manifest,
    preview_audio_cue_manifest,
    render_audio_cue_manifest,
)
from .services.diarization import load_diarization
from .services.downloader import (
    download_reference_audio,
    download_video,
    reference_audio_duration,
    reference_audio_waveform,
    trim_reference_audio,
)
from .services.diarization_runtime import (
    local_diarization_status,
    start_local_diarization,
    stop_local_diarization,
)
from .seed import seed_database
from .services.metadata import detect_video_platform, extract_video_metadata
from .services.production_configuration import (
    ProductionConfiguration,
    parse_configuration_json,
    safe_runtime_snapshot,
    stable_json,
    validate_runtime_compatibility,
)
from .services.renderer import (
    detect_subtitle_region,
    normalize_subtitle_style,
    render_subtitle_preview,
)
from .services.subtitle_styles import (
    list_subtitle_styles,
    save_subtitle_style,
    set_active_subtitle_style,
)
from .services.review import build_review_artifacts, review_status
from .services.runtime_settings import (
    apply_runtime_secrets_to_env,
    get_runtime_settings,
    update_runtime_settings,
)
from .services.runtime_hardware import detect_omnivoice_runtime_options
from .services.tts_runtime import resolve_effective_tts_runtime
from .services.omnivoice_local_runtime import (
    local_omnivoice_status,
    start_local_omnivoice,
    stop_local_omnivoice,
)
from .services.omnivoice_local_setup import (
    local_omnivoice_setup_status,
    start_local_omnivoice_setup,
)
from .services.omnivoice_colab_runtime import (
    get_colab_runtime_status,
    install_colab_cli,
    list_colab_accounts,
    remove_colab_account,
    start_colab_runtime,
    stop_colab_runtime,
    submit_colab_auth_code,
    switch_colab_account,
    switch_to_saved_colab_account,
)
from .services.storage import ensure_storage
from .services.progress import get_progress_detail
from .services.voice_options import (
    VoiceOption,
    delete_voice_option,
    list_voice_options,
    resolve_voice_option,
    save_voice_option,
)
from .services.zerotts_tts import (
    fetch_zerotts_community_catalog,
    install_zerotts_catalog_voice,
    install_zerotts_community_archive,
)
from .services.workspace_settings import (
    get_workspace_settings,
    update_workspace_settings,
)
from .worker import (
    append_log,
    cancel_job,
    clone_job_as_new,
    confirm_speaker_mapping,
    continue_job,
    queue_job,
    rerender_job_with_voice,
    retry_job,
)


def serialize_video(video: Video) -> VideoOut:
    latest_job = (
        sorted(video.jobs, key=lambda item: item.created_at, reverse=True)[0]
        if video.jobs
        else None
    )
    return VideoOut.model_validate({**video.__dict__, "latest_job": latest_job})


@asynccontextmanager
async def lifespan(app: FastAPI):
    _backup_sqlite_before_platform_migration()
    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility()
    apply_runtime_secrets_to_env()
    with next(get_db()) as db:
        seed_database(db)
        ensure_builtin_production_preset(db)
    yield


def ensure_schema_compatibility() -> None:
    """Apply lightweight dev migrations until Alembic is introduced."""
    inspector = inspect(engine)
    if "media_jobs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("media_jobs")}
    phase_four_columns = {
        "video_id",
        "project_id",
        "preset_id",
        "preset_version",
        "configuration_schema_version",
        "configuration_snapshot_json",
        "runtime_snapshot_json",
    }
    if phase_four_columns - columns:
        _backup_sqlite_before_domain_migration()
    if "voice_rate" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN voice_rate VARCHAR(16) NOT NULL DEFAULT '+0%'"
                )
            )
    if "voice_mode" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN voice_mode VARCHAR(16) NOT NULL DEFAULT ''"
                )
            )
    if "voice_instruction" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN voice_instruction TEXT NOT NULL DEFAULT ''"
                )
            )
    if "voice_reference_audio_url" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN voice_reference_audio_url VARCHAR(2048) NOT NULL DEFAULT ''"
                )
            )
    if "voice_reference_text" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN voice_reference_text TEXT NOT NULL DEFAULT ''"
                )
            )
    if "download_quality" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN download_quality VARCHAR(32) NOT NULL DEFAULT 'reliable'"
                )
            )
    if "render_quality" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN render_quality VARCHAR(32) NOT NULL DEFAULT 'balanced'"
                )
            )
    if "render_mode" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN render_mode VARCHAR(16) NOT NULL DEFAULT 'auto'"
                )
            )
    if "burn_subtitles" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN burn_subtitles BOOLEAN NOT NULL DEFAULT 1"
                )
            )
    if "test_clip_seconds" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN test_clip_seconds INTEGER NOT NULL DEFAULT 0"
                )
            )
    if "source_title" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN source_title VARCHAR(500)")
            )
    if "thumbnail_url" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN thumbnail_url VARCHAR(2048)")
            )
    if "speaker_diarization_enabled" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN speaker_diarization_enabled BOOLEAN NOT NULL DEFAULT 0"
                )
            )
    if "speaker_min_count" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN speaker_min_count INTEGER")
            )
    if "speaker_max_count" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN speaker_max_count INTEGER")
            )
    if "speaker_voice_map" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN speaker_voice_map TEXT NOT NULL DEFAULT '{}'"
                )
            )
    if "video_id" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN video_id VARCHAR(36)")
            )
    if "project_id" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN project_id VARCHAR(36)")
            )
    if "preset_id" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN preset_id VARCHAR(36)")
            )
    if "preset_version" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN preset_version INTEGER")
            )
    if "configuration_schema_version" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN configuration_schema_version INTEGER"
                )
            )
    if "configuration_snapshot_json" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE media_jobs ADD COLUMN configuration_snapshot_json TEXT"
                )
            )
    if "runtime_snapshot_json" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN runtime_snapshot_json TEXT")
            )
    if "started_at" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN started_at DATETIME")
            )
    if "completed_at" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE media_jobs ADD COLUMN completed_at DATETIME")
            )


def _backup_sqlite_before_domain_migration() -> None:
    if engine.url.get_backend_name() != "sqlite":
        return
    database = engine.url.database
    if not database or database == ":memory:":
        return
    source = Path(database)
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.exists():
        return
    backup_dir = ensure_storage() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    existing = list(backup_dir.glob(f"{source.stem}.pre-phase4.*{source.suffix}"))
    if existing:
        return
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(
        source, backup_dir / f"{source.stem}.pre-phase4.{timestamp}{source.suffix}"
    )


def _backup_sqlite_before_platform_migration() -> None:
    if engine.url.get_backend_name() != "sqlite":
        return
    existing_tables = set(inspect(engine).get_table_names())
    required_tables = {
        "platform_provider_configs",
        "platform_connections",
        "platform_destinations",
        "platform_oauth_states",
        "publications",
    }
    if not existing_tables or required_tables.issubset(existing_tables):
        return
    database = engine.url.database
    if not database or database == ":memory:":
        return
    source = Path(database)
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.exists():
        return
    backup_dir = ensure_storage() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if list(backup_dir.glob(f"{source.stem}.pre-platforms.*{source.suffix}")):
        return
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(
        source, backup_dir / f"{source.stem}.pre-platforms.{timestamp}{source.suffix}"
    )


app = FastAPI(title="Aether Studio API", version="0.1.0", lifespan=lifespan)
app.mount("/storage", StaticFiles(directory=str(ensure_storage())), name="storage")
app.include_router(production_router)

web_origin = os.getenv("WEB_ORIGIN", "http://localhost:3000")
_extra_origins = [
    "http://localhost:5173",   # Vite desktop dev server
    "http://127.0.0.1:5173",
    "http://tauri.localhost",  # Tauri production WebView on Windows
    "tauri://localhost",       # Tauri production WebView on macOS
    "https://tauri.localhost",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[web_origin, "http://127.0.0.1:3000"] + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug/status")
def debug_status(db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_runtime_settings()
    storage_root = ensure_storage()
    db_status = _debug_database_status(db)

    return {
        "checked_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "api": {"status": "ok", "version": app.version},
        "database": db_status,
        "storage": {
            "root": str(storage_root.resolve()),
            "exists": storage_root.exists(),
            "folders": {
                name: _debug_folder_stats(storage_root / name)
                for name in (
                    "raw-videos",
                    "subtitles",
                    "audio",
                    "audio-bed",
                    "audio-cues",
                    "rendered-outputs",
                    "thumbnails",
                    "logs",
                    "source-uploads",
                    "voice-references",
                )
            },
        },
        "tools": {
            "ffmpeg": _debug_executable("ffmpeg"),
            "ffprobe": _debug_executable("ffprobe"),
            "yt_dlp": _debug_executable("yt-dlp"),
        },
        "runtime": _debug_runtime_config(settings),
        "services": {
            "omnivoice_health": _debug_http_json(settings.omnivoice_api_url, "/health"),
            "omnivoice_ready": _debug_http_json(settings.omnivoice_api_url, "/ready"),
            "local_translation_models": _debug_http_json(
                settings.local_translation_base_url, "/models"
            ),
        },
        "jobs": {
            "counts": {
                row.status.value
                if hasattr(row.status, "value")
                else str(row.status): row.count
                for row in db.query(
                    MediaJob.status, func.count(MediaJob.id).label("count")
                )
                .group_by(MediaJob.status)
                .all()
            },
            "recent": [
                {
                    "id": job.id,
                    "status": job.status.value,
                    "progress": job.progress,
                    "current_step": job.current_step,
                    "video_url": job.video_url,
                    "output_url": job.output_url,
                    "error_message": job.error_message,
                    "updated_at": job.updated_at.isoformat(timespec="seconds")
                    if job.updated_at
                    else None,
                }
                for job in db.query(MediaJob)
                .order_by(MediaJob.updated_at.desc())
                .limit(8)
                .all()
            ],
        },
    }


def _debug_database_status(db: Session) -> dict[str, object]:
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "url": _sanitize_database_url(str(engine.url))}
    except Exception as exc:
        return {
            "status": "error",
            "url": _sanitize_database_url(str(engine.url)),
            "error": str(exc),
        }


def _sanitize_database_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


def _debug_folder_stats(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0}
    files = [item for item in path.rglob("*") if item.is_file()]
    return {
        "exists": True,
        "files": len(files),
        "bytes": sum(item.stat().st_size for item in files),
    }


def _debug_executable(name: str) -> dict[str, object]:
    found = shutil.which(name)
    return {"status": "ok" if found else "missing", "path": found}


def _debug_runtime_config(settings) -> dict[str, object]:
    return {
        "translation_provider": settings.translation_provider
        or os.getenv("AETHER_TRANSLATION_PROVIDER", "nvidia"),
        "translation_nvidia_model": settings.translation_nvidia_model
        or os.getenv("NVIDIA_MODEL", ""),
        "local_translation_base_url": settings.local_translation_base_url,
        "local_translation_model": settings.local_translation_model,
        "local_translation_api_key_configured": bool(
            settings.local_translation_api_key
        ),
        "tts_provider": settings.tts_provider,
        "tts_unit_mode": os.getenv("AETHER_TTS_UNIT_MODE", ""),
        "tts_semantic_max_seconds": os.getenv("AETHER_TTS_SEMANTIC_MAX_SECONDS", "14"),
        "tts_semantic_pause_threshold": os.getenv(
            "AETHER_TTS_SEMANTIC_PAUSE_THRESHOLD", "0.8"
        ),
        "tts_chunk_target_seconds": os.getenv("AETHER_TTS_CHUNK_TARGET_SECONDS", "45"),
        "tts_chunk_min_seconds": os.getenv("AETHER_TTS_CHUNK_MIN_SECONDS", "30"),
        "tts_chunk_max_seconds": os.getenv("AETHER_TTS_CHUNK_MAX_SECONDS", "60"),
        "speech_rate_detection_enabled": os.getenv(
            "AETHER_SPEECH_RATE_DETECTION_ENABLED", "1"
        ),
        "speech_rate_tts_adjustment_enabled": os.getenv(
            "AETHER_SPEECH_RATE_TTS_ADJUSTMENT_ENABLED", "1"
        ),
        "speech_rate_fast_threshold": os.getenv(
            "AETHER_SPEECH_RATE_FAST_THRESHOLD", "3.4"
        ),
        "speech_rate_very_fast_threshold": os.getenv(
            "AETHER_SPEECH_RATE_VERY_FAST_THRESHOLD", "4.5"
        ),
        "omnivoice_timeout_seconds": os.getenv(
            "AETHER_OMNIVOICE_TIMEOUT_SECONDS", "300"
        ),
        "omnivoice_runtime": settings.omnivoice_runtime,
        "omnivoice_device": settings.omnivoice_device,
        "omnivoice_api_url": settings.omnivoice_api_url,
        "omnivoice_api_key_configured": bool(settings.omnivoice_api_key),
        "omnivoice_mode": settings.omnivoice_mode,
        "omnivoice_ref_audio_url_configured": bool(settings.omnivoice_ref_audio_url),
        "omnivoice_ref_audio_path": settings.omnivoice_ref_audio_path,
        "omnivoice_ref_text_configured": bool(settings.omnivoice_ref_text),
        "omnivoice_ref_text_path": settings.omnivoice_ref_text_path,
        "default_download_quality": os.getenv("AETHER_YTDLP_QUALITY", "reliable"),
        "default_render_quality": os.getenv("AETHER_RENDER_QUALITY", "balanced"),
    }


def _debug_http_json(base_url: str, path: str) -> dict[str, object]:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"status": "not_configured", "url": ""}
    url = f"{base}{path}"
    started = time.perf_counter()
    try:
        response = httpx.get(url, timeout=4)
        latency_ms = round((time.perf_counter() - started) * 1000)
        body: object
        try:
            body = response.json()
        except Exception:
            body = response.text[:500]
        return {
            "status": "ok" if response.status_code < 400 else "error",
            "url": url,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "body": body,
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000)
        return {
            "status": "error",
            "url": url,
            "latency_ms": latency_ms,
            "error": str(exc),
        }


@app.get("/settings/runtime", response_model=RuntimeSettingsOut)
def read_runtime_settings() -> RuntimeSettingsOut:
    return _runtime_settings_response()


@app.get("/settings/runtime/options", response_model=OmniVoiceRuntimeOptionsOut)
def read_runtime_options() -> OmniVoiceRuntimeOptionsOut:
    settings = get_runtime_settings()
    options = detect_omnivoice_runtime_options()
    options["prefer_local_gpu"] = settings.prefer_local_gpu
    options["effective_tts_runtime"] = resolve_effective_tts_runtime(settings)
    return OmniVoiceRuntimeOptionsOut.model_validate(options)


@app.get("/settings/runtime/omnivoice/local", response_model=OmniVoiceLocalStatusOut)
def read_local_omnivoice_status() -> OmniVoiceLocalStatusOut:
    return OmniVoiceLocalStatusOut.model_validate(
        local_omnivoice_status(get_runtime_settings())
    )


@app.post(
    "/settings/runtime/omnivoice/local/start", response_model=OmniVoiceLocalStatusOut
)
def start_local_omnivoice_runtime() -> OmniVoiceLocalStatusOut:
    settings = get_runtime_settings()
    try:
        status = start_local_omnivoice(settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OmniVoiceLocalStatusOut.model_validate(status)


@app.delete("/settings/runtime/omnivoice/local", response_model=OmniVoiceLocalStatusOut)
def stop_local_omnivoice_runtime() -> OmniVoiceLocalStatusOut:
    return OmniVoiceLocalStatusOut.model_validate(stop_local_omnivoice())


@app.get(
    "/settings/runtime/omnivoice/local/setup", response_model=OmniVoiceLocalSetupOut
)
def read_local_omnivoice_setup() -> OmniVoiceLocalSetupOut:
    return OmniVoiceLocalSetupOut.model_validate(local_omnivoice_setup_status())


@app.post(
    "/settings/runtime/omnivoice/local/setup", response_model=OmniVoiceLocalSetupOut
)
def start_local_omnivoice_setup_runtime() -> OmniVoiceLocalSetupOut:
    return OmniVoiceLocalSetupOut.model_validate(start_local_omnivoice_setup())


@app.get("/settings/runtime/omnivoice/colab", response_model=OmniVoiceColabStatusOut)
def read_colab_omnivoice_status() -> OmniVoiceColabStatusOut:
    return OmniVoiceColabStatusOut.model_validate(get_colab_runtime_status())


@app.post("/settings/runtime/omnivoice/colab/launch")
def launch_colab_omnivoice() -> OmniVoiceColabStatusOut:
    return OmniVoiceColabStatusOut.model_validate(start_colab_runtime())


@app.post(
    "/settings/runtime/omnivoice/colab/setup", response_model=OmniVoiceColabStatusOut
)
def setup_colab_omnivoice_cli() -> OmniVoiceColabStatusOut:
    return OmniVoiceColabStatusOut.model_validate(install_colab_cli())


@app.post("/settings/runtime/omnivoice/colab/auth-code")
def submit_colab_omnivoice_auth_code(payload: ColabAuthCodeIn) -> OmniVoiceColabStatusOut:
    return OmniVoiceColabStatusOut.model_validate(submit_colab_auth_code(payload.code))


@app.post("/settings/runtime/omnivoice/colab/switch-account")
def switch_colab_omnivoice_account() -> OmniVoiceColabStatusOut:
    return OmniVoiceColabStatusOut.model_validate(switch_colab_account())


@app.get(
    "/settings/runtime/omnivoice/colab/accounts", response_model=ColabAccountsOut
)
def list_colab_omnivoice_accounts() -> ColabAccountsOut:
    return ColabAccountsOut.model_validate(list_colab_accounts())


@app.post(
    "/settings/runtime/omnivoice/colab/accounts/switch",
    response_model=OmniVoiceColabStatusOut,
)
def switch_colab_omnivoice_saved_account(
    payload: ColabAccountSwitchIn,
) -> OmniVoiceColabStatusOut:
    return OmniVoiceColabStatusOut.model_validate(
        switch_to_saved_colab_account(payload.slug)
    )


@app.delete(
    "/settings/runtime/omnivoice/colab/accounts/{slug}",
    response_model=ColabAccountsOut,
)
def remove_colab_omnivoice_account(slug: str) -> ColabAccountsOut:
    return ColabAccountsOut.model_validate(remove_colab_account(slug))


@app.delete("/settings/runtime/omnivoice/colab", response_model=OmniVoiceColabStatusOut)
def stop_colab_omnivoice_runtime() -> OmniVoiceColabStatusOut:
    return OmniVoiceColabStatusOut.model_validate(stop_colab_runtime())


@app.get(
    "/settings/runtime/diarization/local", response_model=DiarizationLocalStatusOut
)
def read_local_diarization_status() -> DiarizationLocalStatusOut:
    return DiarizationLocalStatusOut.model_validate(
        local_diarization_status(get_runtime_settings())
    )


@app.post(
    "/settings/runtime/diarization/local/start",
    response_model=DiarizationLocalStatusOut,
)
def start_local_diarization_runtime() -> DiarizationLocalStatusOut:
    return DiarizationLocalStatusOut.model_validate(
        start_local_diarization(get_runtime_settings())
    )


@app.delete(
    "/settings/runtime/diarization/local", response_model=DiarizationLocalStatusOut
)
def stop_local_diarization_runtime() -> DiarizationLocalStatusOut:
    return DiarizationLocalStatusOut.model_validate(stop_local_diarization())


@app.patch("/settings/runtime", response_model=RuntimeSettingsOut)
def patch_runtime_settings(payload: RuntimeSettingsPatch) -> RuntimeSettingsOut:
    settings = update_runtime_settings(payload.model_dump(exclude_unset=True))
    if (
        settings.tts_provider == "omnivoice"
        and settings.omnivoice_runtime != "colab"
        and os.getenv("AETHER_OMNIVOICE_AUTOSTART", "1") != "0"
    ):
        start_local_omnivoice(settings)
    return _runtime_settings_response(settings)


@app.get("/settings/workspace", response_model=WorkspaceSettingsOut)
def read_workspace_settings() -> WorkspaceSettingsOut:
    return WorkspaceSettingsOut.model_validate(get_workspace_settings().__dict__)


@app.patch("/settings/workspace", response_model=WorkspaceSettingsOut)
def patch_workspace_settings(payload: WorkspaceSettingsPatch) -> WorkspaceSettingsOut:
    try:
        settings = update_workspace_settings(payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceSettingsOut.model_validate(settings.__dict__)


def _runtime_settings_response(settings=None) -> RuntimeSettingsOut:
    settings = settings or get_runtime_settings()
    data = dict(settings.__dict__)
    data["huggingface_token_configured"] = bool(settings.huggingface_token)
    data["huggingface_token"] = ""
    data["nvidia_api_key_configured"] = bool(settings.nvidia_api_key)
    data["nvidia_api_key"] = ""
    data["groq_api_key_configured"] = bool(settings.groq_api_key)
    data["groq_api_key"] = ""
    data["ngrok_authtoken_configured"] = bool(settings.ngrok_authtoken)
    data["ngrok_authtoken"] = ""
    return RuntimeSettingsOut.model_validate(data)


@app.get("/voice-options", response_model=list[VoiceOptionOut])
def get_voice_options() -> list[VoiceOptionOut]:
    return [
        VoiceOptionOut.model_validate(option.__dict__)
        for option in list_voice_options()
    ]


@app.post("/voice-options", response_model=VoiceOptionOut)
def create_voice_option(payload: VoiceOptionIn) -> VoiceOptionOut:
    try:
        option = save_voice_option(VoiceOption(**payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return VoiceOptionOut.model_validate(option.__dict__)


@app.post("/voice-options/zerotts/import", response_model=list[VoiceOptionOut])
def import_zerotts_voice(file: UploadFile = File(...)) -> list[VoiceOptionOut]:
    if Path(file.filename or "").suffix.lower() != ".zip":
        raise HTTPException(
            status_code=400,
            detail="ZeroTTS community voices must be imported from a .zip pack.",
        )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            total = 0
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > 16 * 1024 * 1024:
                    raise ValueError("ZeroTTS voice archive is larger than 16 MB.")
                temporary.write(chunk)
        installed = install_zerotts_community_archive(temporary_path)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        file.file.close()

    return [
        VoiceOptionOut.model_validate(option.__dict__)
        for option in list_voice_options()
        if any(option.id == voice.id for voice in installed)
    ]


@app.get(
    "/voice-options/zerotts/community",
    response_model=list[ZeroTTSCommunityVoiceOut],
)
def get_zerotts_community_voices() -> list[ZeroTTSCommunityVoiceOut]:
    try:
        voices = fetch_zerotts_community_catalog()
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to load the ZeroTTS community library: {exc}",
        ) from exc
    return [ZeroTTSCommunityVoiceOut.model_validate(voice) for voice in voices]


@app.post(
    "/voice-options/zerotts/community/{voice_id}/install",
    response_model=VoiceOptionOut,
)
def install_zerotts_community_voice(voice_id: str) -> VoiceOptionOut:
    try:
        installed = install_zerotts_catalog_voice(voice_id)
    except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    option = next(
        (voice for voice in list_voice_options() if voice.id == installed.id),
        None,
    )
    if not option:
        raise HTTPException(status_code=500, detail="Installed voice was not registered.")
    return VoiceOptionOut.model_validate(option.__dict__)


@app.delete("/voice-options/{voice_id}")
def remove_voice_option(voice_id: str) -> dict[str, str]:
    deleted = delete_voice_option(voice_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Custom voice option not found.")
    return {"status": "deleted"}


@app.get("/voice-options/{voice_id}/preview")
async def preview_voice_option(voice_id: str):
    if voice_id == "none":
        raise HTTPException(
            status_code=400,
            detail="Voice preview is unavailable when voice generation is disabled.",
        )
    voice = next((item for item in list_voice_options() if item.id == voice_id), None)
    if voice and voice.reference_audio_url:
        return RedirectResponse(voice.reference_audio_url)

    reference_path = _resolve_reference_audio_path(
        voice.reference_audio_path if voice else ""
    )
    if reference_path:
        return FileResponse(reference_path)

    sample_text = (
        "Xin chào, đây là bản nghe thử giọng tiếng Việt trong VietDub."
    )

    if voice and voice.engine == "zerotts":
        preview_path = (
            ensure_storage() / "voice-previews" / f"{_safe_filename(voice.id)}.mp3"
        )
        if not preview_path.exists() or preview_path.stat().st_size == 0:
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                import asyncio

                from .services.zerotts_tts import synthesize_zerotts

                # First use downloads ~900 MB of weights; keep it off the event loop.
                await asyncio.to_thread(
                    synthesize_zerotts, sample_text, voice.id, preview_path
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Unable to generate ZeroTTS voice preview: {exc}",
                ) from exc
        return FileResponse(preview_path, media_type="audio/mpeg")

    voice_id_for_tts = (
        voice.id if voice and voice.id.endswith("Neural") else "vi-VN-HoaiMyNeural"
    )
    preview_path = (
        ensure_storage() / "voice-previews" / f"{_safe_filename(voice_id_for_tts)}.mp3"
    )
    if not preview_path.exists() or preview_path.stat().st_size == 0:
        try:
            import edge_tts

            preview_path.parent.mkdir(parents=True, exist_ok=True)
            communicate = edge_tts.Communicate(
                sample_text, voice=voice_id_for_tts, rate="+0%"
            )
            await communicate.save(str(preview_path))
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Unable to generate voice preview: {exc}"
            ) from exc

    return FileResponse(preview_path, media_type="audio/mpeg")


@app.post("/voice-references/upload", response_model=VoiceReferenceUploadOut)
def upload_voice_reference(file: UploadFile = File(...)) -> VoiceReferenceUploadOut:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
        raise HTTPException(
            status_code=400,
            detail="Reference audio must be WAV, MP3, FLAC, M4A, or OGG.",
        )

    storage_root = ensure_storage()
    safe_name = f"{uuid.uuid4()}{suffix}"
    destination = storage_root / "voice-references" / safe_name
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    return VoiceReferenceUploadOut(
        filename=file.filename or safe_name,
        path=str(destination.resolve()),
        url=f"/storage/voice-references/{safe_name}",
        duration=reference_audio_duration(destination),
    )


@app.post("/voice-references/download", response_model=VoiceReferenceUploadOut)
async def download_voice_reference(
    payload: VoiceReferenceDownloadRequest,
) -> VoiceReferenceUploadOut:
    try:
        destination = await asyncio.to_thread(
            download_reference_audio, payload.video_url, payload.audio_format
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Unable to download reference audio: {exc}"
        ) from exc
    return _voice_reference_response(destination)


@app.post("/voice-references/trim", response_model=VoiceReferenceUploadOut)
async def trim_voice_reference(
    payload: VoiceReferenceTrimRequest,
) -> VoiceReferenceUploadOut:
    try:
        source = _resolve_stored_voice_reference(payload.path)
        destination = await asyncio.to_thread(
            trim_reference_audio,
            source,
            payload.start_seconds,
            payload.end_seconds,
            payload.audio_format,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Unable to trim reference audio: {exc}"
        ) from exc
    return _voice_reference_response(destination)


def _resolve_stored_voice_reference(path_value: str) -> Path:
    root = (ensure_storage() / "voice-references").resolve()
    raw = path_value.strip()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        normalized = raw.replace("\\", "/")
        prefix = "storage/voice-references/"
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
        candidate = root / normalized
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Reference audio must be inside voice-references storage.") from exc
    if not resolved.exists() or not resolved.is_file():
        raise ValueError("Reference audio file was not found.")
    return resolved


def _voice_reference_response(path: Path) -> VoiceReferenceUploadOut:
    root = ensure_storage().resolve()
    relative = path.resolve().relative_to(root)
    return VoiceReferenceUploadOut(
        filename=path.name,
        path=str(path.resolve()),
        url=f"/storage/{relative.as_posix()}",
        duration=reference_audio_duration(path),
    )


@app.post("/voice-references/waveform", response_model=VoiceReferenceWaveformOut)
async def get_voice_reference_waveform(
    payload: VoiceReferenceWaveformRequest,
) -> VoiceReferenceWaveformOut:
    try:
        source = _resolve_stored_voice_reference(payload.path)
        peaks = await asyncio.to_thread(reference_audio_waveform, source, payload.points)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to build waveform: {exc}") from exc
    return VoiceReferenceWaveformOut(
        duration=reference_audio_duration(source),
        peaks=peaks,
    )


@app.post("/source-videos/upload", response_model=SourceVideoUploadOut)
def upload_source_video(file: UploadFile = File(...)) -> SourceVideoUploadOut:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
        raise HTTPException(
            status_code=400, detail="Source video must be MP4, MOV, M4V, WEBM, or MKV."
        )

    storage_root = ensure_storage()
    safe_name = f"{uuid.uuid4()}{suffix}"
    destination = storage_root / "source-uploads" / safe_name
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    if destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded source video is empty.")

    return SourceVideoUploadOut(
        filename=file.filename or safe_name,
        path=str(destination.resolve()),
        url=f"/storage/source-uploads/{safe_name}",
    )


@app.post("/source-videos/preview", response_model=SourceVideoUploadOut)
async def download_source_video_preview(
    payload: SourceVideoPreviewRequest,
) -> SourceVideoUploadOut:
    preview_id = f"preview-{uuid.uuid4()}"
    preview_job = SimpleNamespace(
        id=preview_id,
        video_url=payload.video_url,
        source_language="auto",
        download_quality=payload.download_quality,
    )
    try:
        destination = await asyncio.to_thread(
            download_video,
            preview_job,
            payload.download_quality,
            False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Unable to prepare video preview: {exc}"
        ) from exc

    storage_root = ensure_storage().resolve()
    resolved = destination.resolve()
    try:
        relative = resolved.relative_to(storage_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="Downloaded preview is outside workspace storage."
        ) from exc
    return SourceVideoUploadOut(
        filename=resolved.name,
        path=str(resolved),
        url=f"/storage/{relative.as_posix()}",
    )


def _resolve_reference_audio_path(path_value: str) -> Path | None:
    raw = (path_value or "").strip().strip('"').strip("'")
    if not raw:
        return None
    candidates = [Path(raw).expanduser()]
    if not candidates[0].is_absolute():
        candidates.extend([Path.cwd() / raw, ensure_storage() / raw])
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _safe_filename(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "_" for char in value
    )


@app.post("/jobs", response_model=MediaJobOut)
def create_media_job(
    payload: MediaJobCreate, db: Session = Depends(get_db)
) -> MediaJobOut:
    values = payload.model_dump()
    values["speaker_voice_map"] = json.dumps(values.get("speaker_voice_map") or {})
    job = MediaJob(
        **values, status=MediaJobStatus.draft, progress=0, current_step="Draft"
    )
    apply_video_metadata(job)
    db.add(job)
    db.flush()
    db.add(
        AuditLog(
            entity_type="media_job",
            entity_id=job.id,
            action="created",
            message="Job row created.",
        )
    )
    db.commit()
    db.refresh(job)
    return MediaJobOut.model_validate(job)


def _media_job_out(job: MediaJob) -> MediaJobOut:
    out = MediaJobOut.model_validate(job)
    # Merge the live sub-status from the in-memory progress channel (not stored on
    # the ORM row) so the UI can show what is happening right now within the step.
    out.step_detail = get_progress_detail(job.id)
    return out


@app.get("/jobs", response_model=list[MediaJobOut])
def list_media_jobs(db: Session = Depends(get_db)) -> list[MediaJobOut]:
    jobs = db.query(MediaJob).order_by(MediaJob.updated_at.desc()).all()
    hydrate_missing_metadata(db, jobs)
    return [_media_job_out(job) for job in jobs]


@app.get("/jobs/{job_id}", response_model=MediaJobOut)
def get_media_job(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _media_job_out(job)


@app.patch("/jobs/{job_id}", response_model=MediaJobOut)
def patch_media_job(
    job_id: str, payload: MediaJobPatch, db: Session = Depends(get_db)
) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    patch_data = payload.model_dump(exclude_unset=True)
    immutable_configuration_fields = {
        "video_url",
        "source_language",
        "target_language",
        "voice",
        "voice_rate",
        "voice_mode",
        "voice_instruction",
        "voice_reference_audio_url",
        "voice_reference_text",
        "speaker_diarization_enabled",
        "speaker_min_count",
        "speaker_max_count",
        "speaker_voice_map",
        "download_quality",
        "render_quality",
        "render_mode",
        "platform",
        "publish_date",
        "publish_time",
    }
    if job.configuration_snapshot_json and immutable_configuration_fields.intersection(
        patch_data
    ):
        raise HTTPException(
            status_code=409,
            detail="This job has an immutable configuration snapshot. Create a new run with current settings instead.",
        )
    speaker_min_count = patch_data.get("speaker_min_count", job.speaker_min_count)
    speaker_max_count = patch_data.get("speaker_max_count", job.speaker_max_count)
    if (
        speaker_min_count
        and speaker_max_count
        and speaker_min_count > speaker_max_count
    ):
        raise HTTPException(
            status_code=400,
            detail="speaker_min_count cannot be greater than speaker_max_count.",
        )
    if "speaker_voice_map" in patch_data:
        patch_data["speaker_voice_map"] = json.dumps(
            patch_data["speaker_voice_map"] or {}
        )
    for key, value in patch_data.items():
        setattr(job, key, value)
    if "video_url" in patch_data:
        apply_video_metadata(job)
    job.updated_at = datetime.utcnow()
    db.add(
        AuditLog(
            entity_type="media_job",
            entity_id=job.id,
            action="updated",
            message="Job row updated.",
        )
    )
    db.commit()
    db.refresh(job)
    return MediaJobOut.model_validate(job)


def apply_video_metadata(job: MediaJob) -> None:
    platform = detect_video_platform(job.video_url)
    if platform and (not job.platform or job.platform == "YouTube"):
        job.platform = platform
    metadata = extract_video_metadata(job.video_url)
    if metadata.title:
        job.source_title = metadata.title
    if metadata.thumbnail_url:
        job.thumbnail_url = metadata.thumbnail_url


def hydrate_missing_metadata(db: Session, jobs: list[MediaJob]) -> None:
    changed = False
    for job in jobs[:12]:
        if job.source_title and job.thumbnail_url:
            continue
        before = (job.source_title, job.thumbnail_url)
        apply_video_metadata(job)
        if before != (job.source_title, job.thumbnail_url):
            changed = True
    if changed:
        db.commit()


@app.post("/jobs/{job_id}/run", response_model=MediaJobOut)
def run_media_job(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    _prepare_snapshotted_job_for_dispatch(job)
    job = queue_job(db, job)
    return MediaJobOut.model_validate(job)


@app.post("/jobs/{job_id}/retry", response_model=MediaJobOut)
def retry_media_job(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    _prepare_snapshotted_job_for_dispatch(job)
    job = retry_job(db, job)
    return MediaJobOut.model_validate(job)


@app.post("/jobs/{job_id}/rerender-voice", response_model=MediaJobOut)
def rerender_media_job_voice(
    job_id: str,
    payload: MediaJobVoiceRerenderRequest,
    db: Session = Depends(get_db),
) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    voice = resolve_voice_option(payload.voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found.")
    try:
        job = rerender_job_with_voice(db, job, voice)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MediaJobOut.model_validate(job)


@app.post("/jobs/{job_id}/run-as-new", response_model=MediaJobOut)
def run_media_job_as_new(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    source = db.get(MediaJob, job_id)
    if not source:
        raise HTTPException(status_code=404, detail="Job not found.")
    clone = clone_job_as_new(db, source)
    _prepare_snapshotted_job_for_dispatch(clone)
    db.add(
        AuditLog(
            entity_type="media_job",
            entity_id=clone.id,
            action="created_from_job",
            message=f"Created as a new run from job {source.id}.",
        )
    )
    clone = queue_job(db, clone)
    return MediaJobOut.model_validate(clone)


@app.post("/jobs/{job_id}/continue", response_model=MediaJobOut)
def continue_media_job(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in {MediaJobStatus.failed, MediaJobStatus.cancelled}:
        raise HTTPException(
            status_code=409,
            detail="Continue is only available for failed or cancelled jobs.",
        )
    _prepare_snapshotted_job_for_dispatch(job)
    try:
        job = continue_job(db, job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MediaJobOut.model_validate(job)


def _prepare_snapshotted_job_for_dispatch(job: MediaJob) -> None:
    if not job.configuration_snapshot_json:
        return
    try:
        configuration = ProductionConfiguration.model_validate(
            json.loads(job.configuration_snapshot_json)
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail=f"Job configuration snapshot is invalid: {exc}"
        ) from exc
    compatibility_errors = validate_runtime_compatibility(configuration)
    if compatibility_errors:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Runtime is incompatible.",
                "errors": compatibility_errors,
            },
        )
    job.runtime_snapshot_json = stable_json(safe_runtime_snapshot())


@app.post("/jobs/{job_id}/cancel", response_model=MediaJobOut)
def cancel_media_job(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    job = cancel_job(db, job)
    return MediaJobOut.model_validate(job)


@app.delete("/jobs/{job_id}")
def delete_media_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    db.delete(job)
    db.add(
        AuditLog(
            entity_type="media_job",
            entity_id=job_id,
            action="deleted",
            message="Job row deleted.",
        )
    )
    db.commit()
    return {"status": "deleted"}


@app.post("/jobs/{job_id}/reveal")
def reveal_job_output(job_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """Open Explorer / Finder and select the output file."""
    import os
    import platform
    import subprocess

    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not job.output_url:
        raise HTTPException(status_code=409, detail="Job has no output yet.")

    from app.services.storage import STORAGE_ROOT

    # output_url = "/storage/rendered-outputs/filename.mp4"
    url_path = job.output_url.lstrip("/")
    prefix = "storage/"
    if url_path.startswith(prefix):
        url_path = url_path[len(prefix):]
    output_file = STORAGE_ROOT / url_path

    if not output_file.exists():
        raise HTTPException(status_code=404, detail=f"Output file not found: {output_file}")

    system = platform.system()
    try:
        if system == "Windows":
            # shell=True lets Windows handle path quoting correctly for /select
            subprocess.Popen(
                f'explorer /select,"{str(output_file)}"',
                shell=True,
            )
        elif system == "Darwin":
            subprocess.Popen(["open", "-R", str(output_file)])
        else:
            subprocess.Popen(["xdg-open", str(output_file.parent)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open folder: {exc}")

    return {"status": "ok", "folder": str(output_file.parent)}


@app.get("/jobs/{job_id}/logs", response_model=MediaJobLogsOut)
def get_media_job_logs(job_id: str, db: Session = Depends(get_db)) -> MediaJobLogsOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return MediaJobLogsOut(job_id=job.id, logs=job.logs or "")


@app.get("/jobs/{job_id}/output", response_model=MediaJobOutputOut)
def get_media_job_output(
    job_id: str, db: Session = Depends(get_db)
) -> MediaJobOutputOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return MediaJobOutputOut(
        job_id=job.id,
        status=job.status,
        output_url=job.output_url,
        error_message=job.error_message,
    )


@app.get("/jobs/{job_id}/speakers", response_model=SpeakerReviewOut)
def get_job_speakers(job_id: str, db: Session = Depends(get_db)) -> SpeakerReviewOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != MediaJobStatus.speaker_review:
        raise HTTPException(
            status_code=409,
            detail="Speaker mapping is only available during speaker_review.",
        )
    try:
        artifact = load_diarization(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    mapping = _speaker_voice_map(job)
    samples = _speaker_transcript_samples(artifact)
    speakers = []
    for item in artifact.get("speakers") or []:
        speaker_id = str(item.get("id") or "")
        assignment = mapping.get(speaker_id) or {}
        sample = samples.get(speaker_id) or {}
        speakers.append(
            SpeakerOut(
                id=speaker_id,
                total_seconds=float(item.get("total_seconds") or 0),
                sample_start=float(sample.get("start") or 0),
                sample_end=float(sample.get("end") or 0),
                sample_text=str(sample.get("text") or ""),
                voice=str(assignment.get("voice") or job.voice),
                voice_rate=str(assignment.get("voice_rate") or job.voice_rate),
            )
        )
    return SpeakerReviewOut(
        job_id=job.id,
        status=job.status,
        source_video_url=_storage_url_from_path(str(artifact.get("video_path") or "")),
        duration_seconds=float(artifact.get("duration_seconds") or 0),
        model=str(artifact.get("model") or ""),
        device=str(artifact.get("device") or ""),
        speakers=speakers,
        turns=[
            SpeakerTurnOut(
                start=float(turn.get("start") or 0),
                end=float(turn.get("end") or 0),
                speaker_id=str(turn.get("speaker_id") or ""),
                overlap=bool(turn.get("overlap")),
                text=str(
                    (samples.get(str(turn.get("speaker_id") or "")) or {}).get("text")
                    or ""
                ),
            )
            for turn in artifact.get("turns") or []
        ],
    )


@app.patch("/jobs/{job_id}/speakers", response_model=SpeakerReviewOut)
def patch_job_speakers(
    job_id: str, payload: SpeakerMappingPatch, db: Session = Depends(get_db)
) -> SpeakerReviewOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != MediaJobStatus.speaker_review:
        raise HTTPException(
            status_code=409, detail="Job is not waiting for speaker mapping."
        )
    artifact = load_diarization(job_id)
    expected = {str(item.get("id") or "") for item in artifact.get("speakers") or []}
    unknown = set(payload.speakers) - expected
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown speaker IDs: {', '.join(sorted(unknown))}"
        )
    job.speaker_voice_map = json.dumps(
        {
            speaker_id: assignment.model_dump()
            for speaker_id, assignment in payload.speakers.items()
        }
    )
    job.updated_at = datetime.utcnow()
    db.commit()
    return get_job_speakers(job_id, db)


@app.post("/jobs/{job_id}/speakers/confirm", response_model=MediaJobOut)
def confirm_job_speakers(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    artifact = load_diarization(job_id)
    expected = {str(item.get("id") or "") for item in artifact.get("speakers") or []}
    mapping = _speaker_voice_map(job)
    missing = [
        speaker_id
        for speaker_id in sorted(expected)
        if not (mapping.get(speaker_id) or {}).get("voice")
    ]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"Choose a voice for: {', '.join(missing)}"
        )
    confirm_speaker_mapping(db, job)
    db.refresh(job)
    return MediaJobOut.model_validate(job)


def _speaker_voice_map(job: MediaJob) -> dict[str, dict[str, str]]:
    try:
        value = json.loads(job.speaker_voice_map or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _speaker_transcript_samples(
    artifact: dict[str, object],
) -> dict[str, dict[str, object]]:
    path = Path(str(artifact.get("transcript_path") or ""))
    if not path.exists():
        return {}
    try:
        transcript = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    samples: dict[str, dict[str, object]] = {}
    for span in transcript.get("spans") or []:
        speaker_id = str(span.get("speaker_id") or "")
        if not speaker_id or speaker_id in samples:
            continue
        samples[speaker_id] = {
            "start": float(span.get("start") or 0),
            "end": float(span.get("end") or 0),
            "text": str(span.get("reconstructed_text") or span.get("raw_text") or ""),
        }
    return samples


def _storage_url_from_path(value: str) -> str | None:
    if not value:
        return None
    try:
        relative = Path(value).resolve().relative_to(ensure_storage().resolve())
    except (ValueError, OSError):
        return None
    return f"/storage/{relative.as_posix()}"


@app.get("/jobs/{job_id}/audio-cues", response_model=AudioCueManifestOut)
def get_audio_cues(job_id: str, db: Session = Depends(get_db)) -> AudioCueManifestOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    try:
        manifest = load_audio_cue_manifest(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AudioCueManifestOut.model_validate(manifest)


@app.patch("/jobs/{job_id}/audio-cues", response_model=AudioCueManifestOut)
def patch_audio_cues(
    job_id: str, payload: AudioCuePatchRequest, db: Session = Depends(get_db)
) -> AudioCueManifestOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in {MediaJobStatus.audio_review, MediaJobStatus.ready}:
        raise HTTPException(
            status_code=409,
            detail="Audio cues can only be edited after the job reaches audio_review.",
        )
    try:
        manifest = patch_audio_cue_manifest(
            job_id,
            [cue.model_dump(exclude_unset=True) for cue in payload.cues],
            payload.background_volume,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    job.logs = append_log(
        job.logs, f"Saved manual audio cue timing edits ({len(payload.cues)} cues)."
    )
    job.updated_at = datetime.utcnow()
    db.commit()
    return AudioCueManifestOut.model_validate(manifest)


@app.get("/subtitle-styles", response_model=SubtitleStylesOut)
def get_subtitle_styles() -> SubtitleStylesOut:
    return SubtitleStylesOut.model_validate(list_subtitle_styles())


@app.post("/subtitle-styles", response_model=dict)
def create_subtitle_style(payload: SubtitleStyleIn) -> dict:
    return save_subtitle_style(
        payload.name, payload.style, payload.id, payload.make_active
    )


@app.patch("/subtitle-styles/active", response_model=dict)
def patch_active_subtitle_style(payload: SubtitleStyleActivePatch) -> dict:
    try:
        return set_active_subtitle_style(payload.style_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/subtitle-styles/detect", response_model=SubtitleRegionDetectOut)
def detect_subtitle_style(
    payload: SubtitleStylePreviewRequest, db: Session = Depends(get_db)
) -> SubtitleRegionDetectOut:
    source_video, _subtitle = _subtitle_preview_paths(payload.job_id, db)
    result = detect_subtitle_region(source_video)
    return SubtitleRegionDetectOut.model_validate(result)


@app.post("/subtitle-styles/preview", response_model=SubtitleStylePreviewOut)
def preview_subtitle_style(
    payload: SubtitleStylePreviewRequest, db: Session = Depends(get_db)
) -> SubtitleStylePreviewOut:
    source_video, subtitle = _subtitle_preview_paths(payload.job_id, db)
    style = normalize_subtitle_style(payload.subtitle_style)
    preview_path = render_subtitle_preview(
        source_video, subtitle, style, payload.at_seconds
    )
    return SubtitleStylePreviewOut(
        preview_url=f"{_storage_url(preview_path)}?v={int(preview_path.stat().st_mtime)}",
        subtitle_style=style,
    )


@app.post("/jobs/{job_id}/audio-cues/preview", response_model=AudioCueManifestOut)
def preview_audio_cues(
    job_id: str, db: Session = Depends(get_db)
) -> AudioCueManifestOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in {MediaJobStatus.audio_review, MediaJobStatus.ready}:
        raise HTTPException(
            status_code=409,
            detail="Audio cue preview is only available after the job reaches audio_review.",
        )
    try:
        manifest = preview_audio_cue_manifest(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    job.logs = append_log(
        job.logs, "Generated manual target-audio preview from current cue timeline."
    )
    job.updated_at = datetime.utcnow()
    db.commit()
    return AudioCueManifestOut.model_validate(manifest)


@app.post("/jobs/{job_id}/audio-cues/render", response_model=MediaJobOut)
def render_audio_cues(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in {MediaJobStatus.audio_review, MediaJobStatus.ready}:
        raise HTTPException(
            status_code=409,
            detail="Only jobs in audio_review can render from manual audio cues.",
        )

    job.status = MediaJobStatus.rendering
    job.progress = 90
    job.current_step = f"Rendering localized video ({job.render_quality})"
    job.error_message = None
    job.logs = append_log(job.logs, "Manual cue render requested.")
    job.updated_at = datetime.utcnow()
    db.commit()

    try:
        configuration = (
            ProductionConfiguration.model_validate(
                parse_configuration_json(job.configuration_snapshot_json)
            )
            if job.configuration_snapshot_json
            and os.getenv("AETHER_WORKER_SNAPSHOT_V2", "1").strip().lower()
            not in {"0", "false", "off", "no"}
            else None
        )
        output_path = render_audio_cue_manifest(
            job_id,
            configuration.output.render_quality
            if configuration
            else job.render_quality,
            subtitle_style=(
                configuration.subtitle.style_snapshot
                if configuration and configuration.subtitle.style_id != "none"
                else None
            ),
            audio_mix=configuration.audio.model_dump(mode="json")
            if configuration
            else None,
            aspect_ratio=configuration.output.aspect_ratio
            if configuration
            else "source",
            include_subtitles=(
                configuration.subtitle.style_id != "none" if configuration else True
            ),
            max_lines=configuration.subtitle.max_lines if configuration else 2,
        )
    except Exception as exc:
        job.status = MediaJobStatus.failed
        job.current_step = "Failed"
        job.error_message = str(exc)
        job.logs = append_log(job.logs, f"Failed: {exc}")
        job.updated_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    job.status = MediaJobStatus.ready
    job.progress = 100
    job.current_step = "Ready"
    job.output_url = f"/storage/rendered-outputs/{output_path.name}"
    job.error_message = None
    job.logs = append_log(job.logs, "Manual cue render completed. Output is ready.")
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return MediaJobOut.model_validate(job)


@app.get("/jobs/{job_id}/review", response_model=VideoReviewOut)
def get_video_review(job_id: str, db: Session = Depends(get_db)) -> VideoReviewOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    artifacts = build_review_artifacts(job)
    return VideoReviewOut(
        job=MediaJobOut.model_validate(job),
        review_status=review_status(job),
        source_video_url=artifacts.source_video_url,
        localized_video_url=artifacts.localized_video_url,
        original_subtitle_url=artifacts.original_subtitle_url,
        translated_subtitle_url=artifacts.translated_subtitle_url,
        subtitle_rows=[asdict(row) for row in artifacts.subtitle_rows],
        qa_checklist=_review_checklist(job, artifacts.subtitle_rows),
    )


@app.post("/jobs/{job_id}/review/approve", response_model=VideoReviewOut)
def approve_video_review(job_id: str, db: Session = Depends(get_db)) -> VideoReviewOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != MediaJobStatus.ready or not job.output_url:
        raise HTTPException(
            status_code=409, detail="Only ready jobs with output video can be approved."
        )
    job.current_step = "Review approved"
    job.logs = f"{job.logs}\n[{datetime.utcnow().isoformat(timespec='seconds')}Z] Review approved.".strip()
    job.updated_at = datetime.utcnow()
    db.add(
        AuditLog(
            entity_type="media_job",
            entity_id=job.id,
            action="review_approved",
            message="Video review approved.",
        )
    )
    db.commit()
    db.refresh(job)
    return get_video_review(job_id, db)


@app.post("/jobs/{job_id}/review/regenerate", response_model=MediaJobOut)
def regenerate_video_review(job_id: str, db: Session = Depends(get_db)) -> MediaJobOut:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    job.logs = f"{job.logs}\n[{datetime.utcnow().isoformat(timespec='seconds')}Z] Review regeneration requested.".strip()
    job = retry_job(db, job)
    return MediaJobOut.model_validate(job)


def _review_checklist(job: MediaJob, subtitle_rows: list[object]) -> list[str]:
    checklist: list[str] = []
    checklist.append(
        "Source video available" if job.video_url else "Source video missing"
    )
    checklist.append(
        "Localized output ready" if job.output_url else "Localized output not ready"
    )
    checklist.append(
        "Subtitle rows available" if subtitle_rows else "Subtitle rows missing"
    )
    checklist.append(
        "No processing error"
        if not job.error_message
        else f"Error: {job.error_message}"
    )
    return checklist


def _storage_url(path: Path | None) -> str | None:
    if not path:
        return None
    try:
        relative = path.resolve().relative_to(ensure_storage().resolve())
    except ValueError:
        return None
    return "/storage/" + relative.as_posix()


def _path_from_storage_url(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("/storage/"):
        relative = value[len("/storage/") :]
        return ensure_storage() / relative
    path = Path(value)
    return path if path.exists() else None


def _subtitle_preview_paths(job_id: str, db: Session) -> tuple[Path, Path]:
    job = db.get(MediaJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    source_video: Path | None = None
    translated_subtitle: Path | None = None
    try:
        manifest = load_audio_cue_manifest(job_id)
        source_video = _path_from_storage_url(manifest.get("source_video_url"))
        translated_subtitle = _path_from_storage_url(
            manifest.get("translated_subtitle_url")
        )
    except FileNotFoundError:
        pass

    storage_root = ensure_storage()
    if not source_video:
        direct_source = storage_root / "raw-videos" / f"{job_id}.mp4"
        source_video = (
            direct_source
            if direct_source.exists()
            else _path_from_storage_url(job.video_url)
        )

    if not translated_subtitle:
        candidates = sorted(
            (storage_root / "subtitles").glob(f"{job_id}*.render.srt"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        translated_subtitle = candidates[0] if candidates else None

    if not source_video or not source_video.exists():
        raise HTTPException(
            status_code=404, detail="Source video for subtitle preview is missing."
        )
    if not translated_subtitle or not translated_subtitle.exists():
        raise HTTPException(
            status_code=404,
            detail="Translated subtitle for subtitle preview is missing.",
        )
    return source_video, translated_subtitle


@app.get("/api/videos", response_model=list[VideoOut])
def list_videos(db: Session = Depends(get_db)) -> list[VideoOut]:
    videos = (
        db.query(Video)
        .options(selectinload(Video.jobs).selectinload(Job.steps))
        .order_by(Video.last_updated.desc())
        .all()
    )
    return [serialize_video(video) for video in videos]


@app.post("/api/videos/bulk", response_model=list[VideoOut])
def create_videos(
    payload: BulkVideoCreate, db: Session = Depends(get_db)
) -> list[VideoOut]:
    videos: list[Video] = []
    for raw_url in payload.urls:
        url = raw_url.strip()
        if not url:
            continue
        video = Video(
            status=VideoStatus.queued,
            video_url=url,
            content="",
            source_language=payload.source_language,
            target_language=payload.target_language,
            voice=payload.voice,
            platform=payload.platform,
            workflow_template=payload.workflow_template,
            progress=0,
        )
        db.add(video)
        videos.append(video)

    if not videos:
        raise HTTPException(status_code=400, detail="No valid URLs were provided.")

    db.flush()
    for video in videos:
        db.add(
            AuditLog(
                entity_type="video",
                entity_id=video.id,
                action="created",
                message="Video row created.",
            )
        )
    db.commit()

    for video in videos:
        db.refresh(video)
    return [serialize_video(video) for video in videos]


@app.get("/api/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: str, db: Session = Depends(get_db)) -> VideoOut:
    video = (
        db.query(Video)
        .options(selectinload(Video.jobs).selectinload(Job.steps))
        .filter(Video.id == video_id)
        .first()
    )
    if not video:
        raise HTTPException(status_code=404, detail="Video not found.")
    return serialize_video(video)


@app.patch("/api/videos/{video_id}", response_model=VideoOut)
def patch_video(
    video_id: str, payload: VideoPatch, db: Session = Depends(get_db)
) -> VideoOut:
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found.")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(video, key, value)
    video.last_updated = datetime.utcnow()
    db.add(
        AuditLog(
            entity_type="video",
            entity_id=video.id,
            action="updated",
            message="Video row updated.",
        )
    )
    db.commit()
    db.refresh(video)
    return serialize_video(video)


@app.post("/api/videos/run", response_model=list[JobOut])
def run_videos(
    payload: RunVideosRequest, db: Session = Depends(get_db)
) -> list[JobOut]:
    jobs: list[Job] = []
    for video_id in payload.video_ids:
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            raise HTTPException(status_code=404, detail=f"Video not found: {video_id}")
        job = create_job(db, video)
        jobs.append(job)
        db.add(
            AuditLog(
                entity_type="job",
                entity_id=job.id,
                action="started",
                message="Localization run started.",
            )
        )
        start_mock_job(job.id)

    return [JobOut.model_validate(job) for job in jobs]


@app.post("/api/videos/schedule", response_model=list[VideoOut])
def schedule_videos(
    payload: ScheduleRequest, db: Session = Depends(get_db)
) -> list[VideoOut]:
    videos: list[Video] = []
    for video_id in payload.video_ids:
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            raise HTTPException(status_code=404, detail=f"Video not found: {video_id}")
        video.status = VideoStatus.scheduled
        video.publish_date = payload.publish_date
        video.publish_time = payload.publish_time
        if payload.platform:
            video.platform = payload.platform
        video.last_updated = datetime.utcnow()
        db.add(
            Schedule(
                video_id=video.id,
                platform=video.platform,
                publish_date=payload.publish_date,
                publish_time=payload.publish_time,
            )
        )
        videos.append(video)

    db.commit()
    return [serialize_video(video) for video in videos]


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobOut:
    job = (
        db.query(Job).options(selectinload(Job.steps)).filter(Job.id == job_id).first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    job.steps = sorted(job.steps, key=lambda step: step.sort_order)
    return JobOut.model_validate(job)


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    async def event_stream():
        last_payload = ""
        while True:
            with next(get_db()) as db:
                job = (
                    db.query(Job)
                    .options(selectinload(Job.steps))
                    .filter(Job.id == job_id)
                    .first()
                )
                if not job:
                    yield 'event: error\ndata: {"detail":"Job not found"}\n\n'
                    return
                job.steps = sorted(job.steps, key=lambda step: step.sort_order)
                payload = JobOut.model_validate(job).model_dump(mode="json")
                text = json.dumps(payload)
                if text != last_payload:
                    yield f"event: job\ndata: {text}\n\n"
                    last_payload = text
                if job.status in {
                    VideoStatus.needs_review,
                    VideoStatus.published,
                    VideoStatus.failed,
                }:
                    return
            await asyncio.sleep(0.7)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
