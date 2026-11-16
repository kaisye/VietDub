from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import MediaJob, MediaJobStatus
from .services.audio_cues import attach_background_audio, create_audio_cue_manifest
from .services.audio_bed import prepare_background_audio
from .services import editor
from .services.diarization import diarize_video, load_diarization
from .services.downloader import download_video
from .services.sentence_units import sentence_units_enabled
from .services.renderer import (
    _probe_dimensions,
    prepare_display_subtitle,
    render_video,
    source_language_subtitle_path,
)
from .services.production_configuration import (
    NO_SUBTITLE_STYLE_ID,
    ProductionConfiguration,
    parse_configuration_json,
    safe_runtime_snapshot,
    stable_json,
    validate_runtime_compatibility,
)
from .services.speech_rate import (
    analyze_speech_rate_from_srt,
    speech_rate_log_message,
    write_speech_rate_profile,
)
from .services.storage import ensure_storage
from .services.subtitle import (
    extract_subtitle,
    load_source_transcript_artifacts,
    prepare_source_transcript,
    source_transcript_log_messages,
    trim_subtitle_to_clip,
)
from .services.translator import (
    _has_untranslated_cjk,
    normalize_subtitle_for_translation,
    track_openrouter_models,
    translate_subtitle,
    translated_segments_path,
    translation_limit_label,
    translation_models_summary,
)
from .services.progress import bind_job, clear_progress, report_progress
from .services.tts import VoiceOverrides, generate_tts, tts_provider_override
from .services.voice_options import NO_VOICE_ID, VoiceOption


def _default_step_detail(status: "MediaJobStatus") -> str:
    """A friendly Vietnamese live line for each running step.

    Deeper services (notably tts.py) override this with richer detail — e.g. to
    distinguish actually synthesising voice from reconnecting the Colab runtime.
    """
    return {
        MediaJobStatus.downloading: "Đang tải video nguồn…",
        MediaJobStatus.transcribing: "Đang trích xuất / tạo phụ đề nguồn…",
        MediaJobStatus.translating: "Đang dịch phụ đề…",
        MediaJobStatus.tts_generating: "Đang chuẩn bị tổng hợp giọng…",
        MediaJobStatus.rendering: "Đang ghép video & âm thanh…",
    }.get(status, "")


_EDITOR_RENDERS: set[str] = set()

RUNNING_STATUSES = {
    MediaJobStatus.queued,
    MediaJobStatus.downloading,
    MediaJobStatus.transcribing,
    MediaJobStatus.translating,
    MediaJobStatus.tts_generating,
    MediaJobStatus.rendering,
}


class JobCancelled(Exception):
    pass


def queue_job(db: Session, job: MediaJob) -> MediaJob:
    if job.status in RUNNING_STATUSES:
        return job

    job.status = MediaJobStatus.queued
    job.progress = 0
    job.current_step = "Queued"
    job.error_message = None
    job.output_url = None
    job.logs = append_log(job.logs, "Job queued.")
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)

    thread = threading.Thread(target=process_job, args=(job.id,), daemon=True)
    thread.start()
    return job


def cancel_job(db: Session, job: MediaJob) -> MediaJob:
    if job.status not in RUNNING_STATUSES:
        return job

    job.status = MediaJobStatus.cancelled
    job.current_step = "Cancelled"
    job.error_message = None
    job.logs = append_log(
        job.logs, "Cancellation requested. Worker will stop at the next checkpoint."
    )
    job.updated_at = datetime.utcnow()
    _persist_job_log(job)
    db.commit()
    db.refresh(job)
    return job


def fail_interrupted_jobs(db: Session) -> int:
    """Mark jobs left in a running state by a previous backend process as failed.

    Workers are in-process threads, so a fresh process (app restart, or a dev
    ``--reload`` triggered by a code edit) has no thread behind any running job.
    Without this the job stays "running" forever and Retry refuses to resume it.
    """
    jobs = db.query(MediaJob).filter(MediaJob.status.in_(RUNNING_STATUSES)).all()
    for job in jobs:
        job.status = MediaJobStatus.failed
        job.error_message = (
            "Backend đã khởi động lại khi job đang chạy nên tiến trình bị dừng. "
            "Bấm Thử lại để chạy tiếp từ checkpoint gần nhất."
        )
        job.logs = append_log(
            job.logs, f"Interrupted by a backend restart during '{job.current_step}'."
        )
        job.updated_at = datetime.utcnow()
        _persist_job_log(job)
    if jobs:
        db.commit()
    return len(jobs)


def retry_job(db: Session, job: MediaJob) -> MediaJob:
    if job.status in RUNNING_STATUSES:
        return job

    checkpoint = _retry_checkpoint(job.id)
    job.error_message = None
    job.output_url = None
    job.logs = append_log(
        job.logs, f"Retry requested; resuming from {checkpoint['label']} checkpoint."
    )
    job.updated_at = datetime.utcnow()

    checkpoint_kind = str(checkpoint["kind"])
    if checkpoint_kind == "translated":
        job.status = MediaJobStatus.tts_generating
        job.progress = 70
        job.current_step = "Resuming voice generation"
    elif checkpoint_kind == "source":
        job.status = MediaJobStatus.translating
        job.progress = 50
        job.current_step = "Resuming translation"
    elif checkpoint_kind == "download":
        job.status = MediaJobStatus.transcribing
        job.progress = 30
        job.current_step = "Resuming subtitle extraction"
    else:
        job.status = MediaJobStatus.queued
        job.progress = 0
        job.current_step = "Queued"

    db.commit()
    db.refresh(job)

    if checkpoint_kind == "translated":
        _start_translation_resume(
            job.id,
            Path(str(checkpoint["raw_video_path"])),
            Path(str(checkpoint["subtitle_path"])),
        )
    elif checkpoint_kind == "source":
        _start_source_resume(
            job.id,
            Path(str(checkpoint["raw_video_path"])),
            Path(str(checkpoint["subtitle_path"])),
        )
    elif checkpoint_kind == "download":
        _start_download_resume(job.id, Path(str(checkpoint["raw_video_path"])))
    else:
        thread = threading.Thread(target=process_job, args=(job.id,), daemon=True)
        thread.start()
    return job


def continue_job(db: Session, job: MediaJob) -> MediaJob:
    if job.status in RUNNING_STATUSES:
        return job
    checkpoint = _retry_checkpoint(job.id)
    if checkpoint["kind"] != "translated":
        raise ValueError(
            "Cannot continue from the previous stage because the source video or translated "
            "subtitle artifact is missing. Use Retry to resume from the latest available checkpoint."
        )
    raw_video_path = Path(str(checkpoint["raw_video_path"]))
    translated_subtitle_path = Path(str(checkpoint["subtitle_path"]))
    job.status = MediaJobStatus.tts_generating
    job.progress = 70
    job.current_step = "Resuming voice generation"
    job.error_message = None
    job.output_url = None
    job.logs = append_log(
        job.logs,
        f"Continue requested from translated subtitle artifact: {translated_subtitle_path.name}",
    )
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    _start_translation_resume(job.id, raw_video_path, translated_subtitle_path)
    return job


def rerender_job_with_voice(
    db: Session, job: MediaJob, voice: VoiceOption
) -> MediaJob:
    """Reuse the downloaded video and translation, then regenerate only TTS/render."""
    if job.status in RUNNING_STATUSES:
        raise ValueError("The job is still running.")

    checkpoint = _retry_checkpoint(job.id)
    if checkpoint["kind"] != "translated":
        raise ValueError(
            "Cannot change voice because the downloaded video or translated subtitle checkpoint is missing."
        )

    _apply_voice_to_job(job, voice)
    configuration = _job_configuration(job)
    if configuration:
        runtime_errors = validate_runtime_compatibility(configuration)
        if runtime_errors:
            raise ValueError("Runtime is incompatible: " + " ".join(runtime_errors))
        job.runtime_snapshot_json = stable_json(safe_runtime_snapshot())
    raw_video_path = Path(str(checkpoint["raw_video_path"]))
    translated_subtitle_path = Path(str(checkpoint["subtitle_path"]))
    output_filename = f"{raw_video_path.stem}-voice-{uuid.uuid4().hex[:8]}.mp4"
    # The editor workspace points at the old takes: rebuild it from the new voice (edits kept as a draft).
    if editor.retire_for_new_voice(job.id, voice.name):
        job.logs = append_log(job.logs, "Editor workspace saved as a draft; it is rebuilt from the new voice.")

    job.status = MediaJobStatus.tts_generating
    job.progress = 70
    job.current_step = "Regenerating voice audio"
    job.error_message = None
    job.output_url = None
    job.started_at = datetime.utcnow()
    job.completed_at = None
    job.logs = append_log(
        job.logs,
        f"Voice changed to {voice.name} ({voice.id}); reusing download, transcript, and translation checkpoints.",
    )
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    _start_translation_resume(
        job.id,
        raw_video_path,
        translated_subtitle_path,
        output_filename=output_filename,
    )
    return job


def _apply_voice_to_job(job: MediaJob, voice: VoiceOption) -> None:
    provider = voice.engine if voice.engine in {"omnivoice", "zerotts"} else "auto"
    mode = voice.omnivoice_mode if voice.omnivoice_mode in {"auto", "design", "clone"} else ""
    snapshot = asdict(voice)

    job.voice = voice.id
    job.voice_mode = mode
    job.voice_instruction = voice.instruction
    job.voice_reference_audio_url = voice.reference_audio_url
    job.voice_reference_text = voice.reference_text

    speaker_map = _speaker_voice_map(job)
    for assignment in speaker_map.values():
        if isinstance(assignment, dict):
            assignment["voice_id"] = voice.id
            assignment["instruction"] = voice.instruction
            assignment["voice_snapshot"] = snapshot
    if speaker_map:
        job.speaker_voice_map = stable_json(speaker_map)

    if not job.configuration_snapshot_json:
        return
    configuration = parse_configuration_json(job.configuration_snapshot_json)
    voice_configuration = configuration.setdefault("voice", {})
    voice_configuration.update(
        {
            "voice_id": voice.id,
            "provider": provider,
            "mode": mode,
            "instruction": voice.instruction,
            "reference": {
                "url": voice.reference_audio_url,
                "path": voice.reference_audio_path,
                "text": voice.reference_text,
                "text_path": voice.reference_text_path,
            },
            "voice_snapshot": snapshot,
        }
    )
    configured_voice_map = (configuration.get("speakers") or {}).get("voice_map") or {}
    for assignment in configured_voice_map.values():
        if isinstance(assignment, dict):
            assignment["voice_id"] = voice.id
            assignment["instruction"] = voice.instruction
            assignment["voice_snapshot"] = snapshot
    validated = ProductionConfiguration.model_validate(configuration)
    job.configuration_snapshot_json = stable_json(validated.model_dump(mode="json"))


def clone_job_as_new(db: Session, source: MediaJob) -> MediaJob:
    clone_fields = (
        "video_id",
        "project_id",
        "preset_id",
        "preset_version",
        "configuration_schema_version",
        "configuration_snapshot_json",
        "runtime_snapshot_json",
        "video_url",
        "source_title",
        "thumbnail_url",
        "content",
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
    )
    clone = MediaJob(
        **{field: getattr(source, field) for field in clone_fields},
        status=MediaJobStatus.draft,
        progress=0,
        current_step="Draft",
        logs=append_log("", f"Created as a new run from job {source.id}."),
    )
    db.add(clone)
    db.flush()
    db.refresh(clone)
    return clone


def confirm_speaker_mapping(db: Session, job: MediaJob) -> MediaJob:
    if job.status != MediaJobStatus.speaker_review:
        raise ValueError("Job is not waiting for speaker mapping.")
    job.status = MediaJobStatus.translating
    job.progress = 50
    job.current_step = "Speaker voices confirmed"
    job.logs = append_log(
        job.logs, "Speaker voice mapping confirmed. Resuming translation and TTS."
    )
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    thread = threading.Thread(
        target=_resume_after_speaker_review, args=(job.id,), daemon=True
    )
    thread.start()
    return job


def process_job(job_id: str) -> None:
    bind_job(job_id)
    try:
        _run_pipeline(job_id)
    except JobCancelled:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if not job:
                return
            _mark_cancelled(db, job, "Worker stopped after cancellation request.")
    except Exception as exc:  # pragma: no cover - defensive guard for worker threads.
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if not job:
                return
            if job.status == MediaJobStatus.cancelled:
                _mark_cancelled(db, job, "Worker stopped after cancellation request.")
                return
            _mark_failed(db, job, str(exc))


def _run_pipeline(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(MediaJob, job_id)
        if not job:
            return

        # Mark the real processing start (excludes queue wait). Reset on every
        # fresh run so a retried job reports its latest run's duration. Resumes
        # after a review checkpoint go through _resume_after_* and keep this set.
        job.started_at = datetime.utcnow()
        job.completed_at = None
        db.commit()

        configuration = _job_configuration(job)
        if configuration:
            runtime_errors = validate_runtime_compatibility(configuration)
            if runtime_errors:
                raise RuntimeError(
                    "Runtime compatibility failed: " + " ".join(runtime_errors)
                )
            job.logs = append_log(
                job.logs,
                "Worker snapshot V2: "
                f"schema={configuration.schema_version}, "
                f"tts={configuration.voice.provider}, "
                f"source={configuration.source.strategy}, "
                f"subtitle={configuration.source.subtitle_strategy}, "
                f"output={configuration.output.aspect_ratio}.",
            )
            _persist_job_log(job)
            db.commit()

        download_quality = (
            configuration.source.download_quality
            if configuration
            else job.download_quality
        )
        clip_seconds = (
            configuration.source.test_clip_seconds
            if configuration
            else int(getattr(job, "test_clip_seconds", 0) or 0)
        )
        _raise_if_cancelled(job.id)
        download_step_label = f"Downloading source video ({download_quality})"
        if clip_seconds and clip_seconds > 0:
            download_step_label += f" — test clip {clip_seconds}s"
        _set_step(
            db,
            job,
            MediaJobStatus.downloading,
            10,
            download_step_label,
        )
        raw_video_path = download_video(job, download_quality=download_quality)
        if clip_seconds and clip_seconds > 0:
            job.logs = append_log(
                job.logs,
                f"Test mode: source limited to the first {clip_seconds}s for a quick quality preview.",
            )
            _persist_job_log(job)
        _warn_if_low_resolution(job, raw_video_path)
        _raise_if_cancelled(job.id)
        _log_artifact(job, raw_video_path)

        _continue_after_download(db, job, raw_video_path, configuration)


# Below this the dub looks blurry full-screen. The marker is parsed by the Progress screen.
LOW_RESOLUTION_HEIGHT = 720
LOW_RESOLUTION_MARKER = "[low-source-resolution]"


def _warn_if_low_resolution(job: MediaJob, raw_video_path: Path) -> None:
    """Logs a warning when a downloaded (not uploaded) source came back below 720p.

    YouTube serves at most 360p to signed-out clients unless cookies are configured,
    so a "best" download can silently end up tiny.
    """
    if not str(job.video_url or "").startswith(("http://", "https://")):
        return
    try:
        width, height = _probe_dimensions(raw_video_path)
    except Exception:
        return
    if 0 < min(width, height) < LOW_RESOLUTION_HEIGHT:
        job.logs = append_log(
            job.logs,
            f"WARNING {LOW_RESOLUTION_MARKER} {width}x{height}: the platform only served a low-resolution "
            "stream. For YouTube set AETHER_YTDLP_COOKIES_FROM_BROWSER (e.g. chrome) and download again.",
        )
        _persist_job_log(job)


def _continue_after_download(
    db: Session,
    job: MediaJob,
    raw_video_path: Path,
    configuration: ProductionConfiguration | None = None,
) -> None:
    source_language = (
        configuration.languages.source if configuration else job.source_language
    )
    target_language = (
        configuration.languages.target if configuration else job.target_language
    )
    _set_step(
        db,
        job,
        MediaJobStatus.transcribing,
        30,
        "Extracting subtitle track or creating source subtitles",
    )
    subtitle_path = extract_subtitle(
        raw_video_path,
        job.content,
        target_language,
        source_language,
        source_url=job.video_url,
        subtitle_strategy=configuration.source.subtitle_strategy
        if configuration
        else "auto",
        ocr_region=configuration.source.ocr_region.model_dump()
        if configuration
        else None,
    )
    _raise_if_cancelled(job.id)
    clip_seconds = (
        configuration.source.test_clip_seconds
        if configuration
        else int(getattr(job, "test_clip_seconds", 0) or 0)
    )
    if clip_seconds and clip_seconds > 0:
        # Keep cues aligned with the trimmed video so translation/TTS stay fast.
        trim_subtitle_to_clip(subtitle_path, clip_seconds)
    _log_artifact(job, subtitle_path)
    source_artifacts = prepare_source_transcript(
        raw_video_path, subtitle_path, source_language
    )
    _log_artifact(job, source_artifacts.transcript_path)
    _log_artifact(job, source_artifacts.quality_path)
    job.logs = append_log(
        job.logs,
        "Source transcript quality gate: "
        f"language={source_artifacts.dominant_language}, "
        f"timestamps={source_artifacts.timestamp_quality}, "
        f"retried={source_artifacts.retried_spans}, "
        f"replaced={source_artifacts.replaced_spans}, "
        f"needs_review={str(source_artifacts.needs_review).lower()}.",
    )
    for message in source_transcript_log_messages(source_artifacts):
        job.logs = append_log(job.logs, message)

    if not _source_first_pipeline_enabled():
        normalized_subtitle_path = normalize_subtitle_for_translation(subtitle_path)
        if normalized_subtitle_path != subtitle_path:
            subtitle_path = normalized_subtitle_path
            job.logs = append_log(
                job.logs,
                "Normalized source subtitle cues by semantic boundaries before translation.",
            )
            _log_artifact(job, subtitle_path)
    _persist_job_log(job)
    db.commit()

    speech_rate_profile = None
    if not source_artifacts.needs_review:
        speech_rate_profile = analyze_speech_rate_from_srt(
            subtitle_path,
            source_artifacts.dominant_language or source_language,
            timestamp_quality=source_artifacts.timestamp_quality,
        )
    else:
        job.logs = append_log(
            job.logs,
            "Skipped source speech-rate adjustment because transcript quality still needs review.",
        )
    if speech_rate_profile:
        speech_rate_path = write_speech_rate_profile(subtitle_path, speech_rate_profile)
        job.logs = append_log(job.logs, speech_rate_log_message(speech_rate_profile))
        _log_artifact(job, speech_rate_path)
        _persist_job_log(job)
        db.commit()

    selected_voice = configuration.voice.voice_id if configuration else job.voice
    speakers_enabled = (
        configuration.speakers.enabled
        if configuration
        else job.speaker_diarization_enabled
    ) and selected_voice != NO_VOICE_ID
    if speakers_enabled:
        min_speakers = (
            configuration.speakers.min_count if configuration else job.speaker_min_count
        )
        max_speakers = (
            configuration.speakers.max_count if configuration else job.speaker_max_count
        )
        job.logs = append_log(
            job.logs, "Detecting speakers with local pyannote runtime."
        )
        _persist_job_log(job)
        db.commit()
        diarization_path = diarize_video(
            job.id,
            raw_video_path,
            source_artifacts.transcript_path,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        _log_artifact(job, diarization_path)
        artifact = load_diarization(job.id)
        job.speaker_voice_map = json.dumps(
            {
                str(item.get("id") or ""): {
                    "voice": configuration.voice.voice_id
                    if configuration
                    else job.voice,
                    "voice_rate": _configuration_voice_rate(configuration)
                    if configuration
                    else job.voice_rate,
                }
                for item in artifact.get("speakers") or []
                if item.get("id")
            }
        )
        speaker_review = (
            configuration.workflow.speaker_review if configuration else True
        )
        if speaker_review:
            _write_speaker_checkpoint(job.id, raw_video_path, subtitle_path)
            job.status = MediaJobStatus.speaker_review
            job.progress = 45
            job.current_step = "Speaker voice review"
            job.logs = append_log(
                job.logs,
                f"Speaker diarization found {len(artifact.get('speakers') or [])} speaker(s). Choose voices to continue.",
            )
            job.updated_at = datetime.utcnow()
            _persist_job_log(job)
            db.commit()
            return
        job.logs = append_log(
            job.logs,
            f"Speaker diarization found {len(artifact.get('speakers') or [])} speaker(s); using snapshot fallback voice.",
        )
        _persist_job_log(job)
        db.commit()

    _continue_after_source(
        db,
        job,
        raw_video_path,
        subtitle_path,
        source_artifacts,
        speech_rate_profile,
        configuration,
    )


def _resume_after_speaker_review(job_id: str) -> None:
    bind_job(job_id)
    try:
        checkpoint = _read_speaker_checkpoint(job_id)
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if not job:
                return
            configuration = _job_configuration(job)
            raw_video_path = Path(str(checkpoint["raw_video_path"]))
            subtitle_path = Path(str(checkpoint["subtitle_path"]))
            source_artifacts = load_source_transcript_artifacts(subtitle_path)
            if not source_artifacts:
                raise RuntimeError("Source transcript checkpoint is missing.")
            speech_rate_profile = None
            if not source_artifacts.needs_review:
                speech_rate_profile = analyze_speech_rate_from_srt(
                    subtitle_path,
                    source_artifacts.dominant_language
                    or (
                        configuration.languages.source
                        if configuration
                        else job.source_language
                    ),
                    timestamp_quality=source_artifacts.timestamp_quality,
                )
            _continue_after_source(
                db,
                job,
                raw_video_path,
                subtitle_path,
                source_artifacts,
                speech_rate_profile,
                configuration,
            )
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if job:
                _mark_failed(db, job, str(exc))


def _continue_after_source(
    db: Session,
    job: MediaJob,
    raw_video_path: Path,
    subtitle_path: Path,
    source_artifacts,
    speech_rate_profile,
    configuration: ProductionConfiguration | None = None,
) -> None:
    _set_step(
        db, job, MediaJobStatus.translating, 50, "Preparing localized subtitle file"
    )
    limit_label = translation_limit_label()
    if limit_label:
        job.logs = append_log(job.logs, limit_label)
        _persist_job_log(job)
        db.commit()
    served_models = track_openrouter_models()
    translated_subtitle_path = translate_subtitle(
        subtitle_path,
        configuration.languages.target if configuration else job.target_language,
        configuration.languages.source if configuration else job.source_language,
        speech_rate=speech_rate_profile,
        source_artifacts=source_artifacts,
        translation_profile=configuration.translation.model_dump(mode="json")
        if configuration
        else None,
    )
    _raise_if_cancelled(job.id)
    models_line = translation_models_summary(served_models)
    if models_line:
        job.logs = append_log(job.logs, models_line)
        _persist_job_log(job)
    _log_artifact(job, translated_subtitle_path)
    segments_path = translated_segments_path(translated_subtitle_path)
    if segments_path.exists():
        _log_artifact(job, segments_path)
    source_line_path = source_language_subtitle_path(translated_subtitle_path)
    if source_line_path.exists():
        _log_artifact(job, source_line_path)

    _continue_after_translation(
        db,
        job,
        raw_video_path,
        translated_subtitle_path,
        speech_rate_profile,
        configuration,
    )


def _continue_after_translation(
    db: Session,
    job: MediaJob,
    raw_video_path: Path,
    translated_subtitle_path: Path,
    speech_rate_profile=None,
    configuration: ProductionConfiguration | None = None,
    output_filename: str | None = None,
) -> None:
    voice = configuration.voice.voice_id if configuration else job.voice
    voice_enabled = voice != NO_VOICE_ID
    subtitle_enabled = (
        configuration.subtitle.style_id != NO_SUBTITLE_STYLE_ID
        if configuration
        else True
    )
    # Job-level toggle from the desktop tool overrides to "no burned subtitles".
    if not bool(getattr(job, "burn_subtitles", True)):
        subtitle_enabled = False
    _set_step(
        db,
        job,
        MediaJobStatus.tts_generating if voice_enabled else MediaJobStatus.rendering,
        70,
        "Generating voice audio" if voice_enabled else "Voice generation disabled",
    )
    voice_overrides = _job_voice_overrides(job, configuration)
    target_language = (
        configuration.languages.target if configuration else job.target_language
    )
    voice_rate = (
        _configuration_voice_rate(configuration) if configuration else job.voice_rate
    )
    speakers_enabled = (
        configuration.speakers.enabled
        if configuration
        else job.speaker_diarization_enabled
    ) and voice_enabled
    playback_subtitle_path = translated_subtitle_path
    # Sentence mode: TTS voices whole sentences from the translated file, and the
    # display pages are built after TTS from the voice timing (below).
    sentence_mode = sentence_units_enabled() and translated_subtitle_path.name.endswith(".render.srt")
    if voice_enabled and subtitle_enabled and not speakers_enabled and not sentence_mode:
        playback_subtitle_path = prepare_display_subtitle(
            raw_video_path,
            translated_subtitle_path,
            configuration.subtitle.style_snapshot if configuration else None,
        )
        if playback_subtitle_path != translated_subtitle_path:
            _log_artifact(job, playback_subtitle_path)
    provider = configuration.voice.provider if configuration else ""
    if voice_enabled:
        resolved_provider = provider or "runtime default"
        resolved_mode = voice_overrides.mode or "standard"
        job.logs = append_log(
            job.logs,
            f"Resolved TTS voice: {voice} via {resolved_provider} ({resolved_mode}).",
        )
        _persist_job_log(job)
        db.commit()
    with tts_provider_override(provider):
        if voice_enabled and _is_manual_render_mode(job, configuration):
            manifest_path = create_audio_cue_manifest(
                job.id,
                raw_video_path,
                playback_subtitle_path,
                voice,
                target_language,
                voice_rate,
                speech_rate=speech_rate_profile,
                voice_overrides=voice_overrides,
                speaker_voice_map=_speaker_voice_map(job),
                diarization_job_id=job.id if speakers_enabled else None,
                audio_mix=configuration.audio.model_dump(mode="json")
                if configuration
                else None,
            )
        else:
            manifest_path = None
    if manifest_path:
        _raise_if_cancelled(job.id)
        _log_artifact(job, manifest_path)

        _set_step(db, job, MediaJobStatus.rendering, 82, "Preparing background sound")
        audio_bed = prepare_background_audio(raw_video_path)
        _raise_if_cancelled(job.id)
        job.logs = append_log(job.logs, audio_bed.message)
        if audio_bed.path:
            _log_artifact(job, audio_bed.path)
        manifest_path = attach_background_audio(job.id, audio_bed.path)
        _log_artifact(job, manifest_path)

        job.status = MediaJobStatus.audio_review
        job.progress = 84
        job.current_step = "Audio cue review"
        job.output_url = None
        job.error_message = None
        job.logs = append_log(
            job.logs, "Manual render mode: audio cues are ready for timeline review."
        )
        job.updated_at = datetime.utcnow()
        _persist_job_log(job)
        db.commit()
        return

    audio_path: Path | None = None
    if voice_enabled:
        with tts_provider_override(provider):
            audio_path = generate_tts(
                playback_subtitle_path,
                voice,
                target_language,
                voice_rate,
                speech_rate=speech_rate_profile,
                voice_overrides=voice_overrides,
                speaker_voice_map=_speaker_voice_map(job),
                diarization_job_id=job.id if speakers_enabled else None,
            )
        _raise_if_cancelled(job.id)
        _log_artifact(job, audio_path)
        _log_tts_chunk_manifest(job, audio_path)
    else:
        job.logs = append_log(job.logs, "Skipped TTS because Voice is set to None.")

    display_subtitle_path = playback_subtitle_path
    if sentence_mode and subtitle_enabled:
        display_subtitle_path = prepare_display_subtitle(
            raw_video_path,
            translated_subtitle_path,
            configuration.subtitle.style_snapshot if configuration else None,
            max_lines=configuration.subtitle.max_lines if configuration else 2,
            voice_spans=_voice_spans(audio_path) if audio_path and not speakers_enabled else None,
        )
        if display_subtitle_path != translated_subtitle_path:
            _log_artifact(job, display_subtitle_path)

    _set_step(db, job, MediaJobStatus.rendering, 82, "Preparing background sound")
    audio_bed = prepare_background_audio(
        raw_video_path,
        preserve_source_audio=not voice_enabled,
    )
    _raise_if_cancelled(job.id)
    job.logs = append_log(job.logs, audio_bed.message)
    if audio_bed.path:
        _log_artifact(job, audio_bed.path)
    _persist_job_log(job)
    db.commit()

    # Review the dub now, while fixing it is cheap, instead of after the render:
    # safe fixes (timing, borrowed silence, fragment merges with a new take) are
    # applied to the editor workspace, which is then what gets rendered.
    reviewed_workspace = None
    if voice_enabled:
        reviewed_workspace = _review_before_render(db, job, configuration)

    if configuration and not configuration.workflow.auto_render:
        # "Edit before rendering": everything the editor needs (source video, voice
        # chunks, subtitles, background audio) exists now, so stop here and let the
        # user render from the editor instead of encoding a video they will redo.
        job.status = MediaJobStatus.ready
        job.progress = 100
        job.current_step = "Ready for editing"
        job.output_url = None
        job.error_message = None
        job.completed_at = datetime.utcnow()
        job.logs = append_log(
            job.logs,
            "Skipped rendering (render after editing). Open the editor to review and render the video.",
        )
        job.updated_at = datetime.utcnow()
        _persist_job_log(job)
        db.commit()
        clear_progress(job.id)
        if reviewed_workspace is None:
            _start_auto_review(db, job, configuration)
        return

    render_quality = (
        configuration.output.render_quality if configuration else job.render_quality
    )
    _set_step(
        db,
        job,
        MediaJobStatus.rendering,
        90,
        f"Rendering localized video ({render_quality})",
    )
    if reviewed_workspace is not None:
        # Render what the review fixed: the editor workspace, voiced clip by clip.
        output_path = editor.render_workspace(
            reviewed_workspace,
            configuration.model_dump(mode="json") if configuration else None,
            render_quality=render_quality,
            burn_subtitles=subtitle_enabled,
            output_filename=output_filename,
        )
    else:
        output_path = render_video(
            raw_video_path,
            audio_path,
            display_subtitle_path if subtitle_enabled else None,
            audio_bed.path,
            render_quality,
            subtitle_style=(
                configuration.subtitle.style_snapshot
                if configuration and subtitle_enabled
                else None
            ),
            audio_mix=configuration.audio.model_dump(mode="json")
            if configuration
            else None,
            aspect_ratio=configuration.output.aspect_ratio if configuration else "source",
            max_lines=configuration.subtitle.max_lines if configuration else 2,
            output_filename=output_filename,
        )
    _raise_if_cancelled(job.id)
    _log_artifact(job, output_path)

    job.status = MediaJobStatus.ready
    job.progress = 100
    job.current_step = "Ready"
    job.output_url = f"/storage/rendered-outputs/{output_path.name}"
    job.error_message = None
    job.completed_at = datetime.utcnow()
    completion_message = "Job completed. Output is ready."
    if configuration and configuration.workflow.final_review:
        completion_message += " Final review is required before publishing."
    job.logs = append_log(job.logs, completion_message)
    job.updated_at = datetime.utcnow()
    _persist_job_log(job)
    db.commit()
    clear_progress(job.id)
    if reviewed_workspace is None:
        _start_auto_review(db, job, configuration)


def _review_before_render(
    db: Session, job: MediaJob, configuration: ProductionConfiguration | None
) -> dict | None:
    """Run the auto review on the fresh dub and apply its pre-render fixes.

    Returns the reviewed editor workspace to render from, or None to render the
    pipeline output as before (review off or disabled, the user already has a
    workspace with edits, or the review failed; a review never fails the job).
    """
    mode = configuration.workflow.auto_review if configuration else "propose"
    if mode == "off" or os.getenv("AETHER_REVIEW_BEFORE_RENDER", "1").strip().lower() in {"0", "false", "off"}:
        return None
    if editor.load_workspace(job.id) is not None:
        return None  # Never overwrite edits made in the editor.
    _set_step(db, job, MediaJobStatus.rendering, 86, "Auto review before render")
    try:
        from .services.auto_review import runner as auto_review

        config_dict = configuration.model_dump(mode="json") if configuration else None
        with editor.workspace_lock(job.id):
            editor.apply_carry_over(editor.build_workspace(job.id, config_dict, job.source_title or ""))
        result = auto_review.review_before_render(
            job.id,
            mode=mode,
            configuration=config_dict,
            title=job.source_title or "",
            source_language=job.source_language or "auto",
            voice_settings=editor_voice_settings(job),
        )
        with editor.workspace_lock(job.id):
            workspace = editor.load_workspace(job.id)
        job.logs = append_log(
            job.logs,
            "Auto review before render: "
            f"{result.get('applied', 0)} fixes applied, {result.get('pending', 0)} waiting for approval, "
            f"score {result.get('score_before')} -> {result.get('score')}.",
        )
        _persist_job_log(job)
        db.commit()
        return workspace
    except Exception as exc:  # noqa: BLE001 — fall back to rendering the unreviewed dub.
        job.logs = append_log(job.logs, f"Auto review before render skipped: {exc}")
        _persist_job_log(job)
        db.commit()
        return None


def _voice_spans(audio_path: Path) -> list[tuple[float, float] | None] | None:
    """Where each voiced sentence actually plays, from the TTS chunk manifest."""
    manifest = audio_path.with_suffix(".chunks.json")
    try:
        chunks = json.loads(manifest.read_text(encoding="utf-8")).get("chunks", [])
    except (OSError, ValueError):
        return None
    spans: list[tuple[float, float] | None] = []
    for chunk in chunks:
        try:
            spans.append((float(chunk["start"]), float(chunk["end"])))
        except (KeyError, TypeError, ValueError):
            spans.append(None)
    return spans or None


def _start_auto_review(db: Session, job: MediaJob, configuration: ProductionConfiguration | None) -> None:
    """Kick off the post-dub auto review in the background. Never fails the job."""
    try:
        mode = configuration.workflow.auto_review if configuration else "propose"
        if mode == "off":
            return
        from .services.auto_review import runner as auto_review

        auto_review.start_review(
            job.id,
            mode=mode,
            configuration=configuration.model_dump(mode="json") if configuration else None,
            title=job.source_title or "",
            source_language=job.source_language or "auto",
            voice_settings=editor_voice_settings(job),
            trigger="pipeline",
        )
    except Exception as exc:  # noqa: BLE001
        try:
            job.logs = append_log(job.logs, f"Auto review could not start: {exc}")
            _persist_job_log(job)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()


def _resume_after_translation(
    job_id: str,
    raw_video_path: Path,
    translated_subtitle_path: Path,
    output_filename: str | None = None,
) -> None:
    bind_job(job_id)
    try:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if not job:
                return
            configuration = _job_configuration(job)
            _continue_after_translation(
                db,
                job,
                raw_video_path,
                translated_subtitle_path,
                configuration=configuration,
                output_filename=output_filename,
            )
    except JobCancelled:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if job:
                _mark_cancelled(db, job, "Worker stopped after cancellation request.")
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if job:
                _mark_failed(db, job, str(exc))


def _start_translation_resume(
    job_id: str,
    raw_video_path: Path,
    translated_subtitle_path: Path,
    output_filename: str | None = None,
) -> None:
    thread = threading.Thread(
        target=_resume_after_translation,
        args=(job_id, raw_video_path, translated_subtitle_path, output_filename),
        daemon=True,
    )
    thread.start()


def _resume_after_source(
    job_id: str, raw_video_path: Path, subtitle_path: Path
) -> None:
    bind_job(job_id)
    try:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if not job:
                return
            configuration = _job_configuration(job)
            source_artifacts = load_source_transcript_artifacts(subtitle_path)
            if not source_artifacts:
                raise RuntimeError("Source transcript checkpoint is missing.")
            speech_rate_profile = None
            if not source_artifacts.needs_review:
                speech_rate_profile = analyze_speech_rate_from_srt(
                    subtitle_path,
                    source_artifacts.dominant_language
                    or (
                        configuration.languages.source
                        if configuration
                        else job.source_language
                    ),
                    timestamp_quality=source_artifacts.timestamp_quality,
                )
            _continue_after_source(
                db,
                job,
                raw_video_path,
                subtitle_path,
                source_artifacts,
                speech_rate_profile,
                configuration,
            )
    except JobCancelled:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if job:
                _mark_cancelled(db, job, "Worker stopped after cancellation request.")
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if job:
                _mark_failed(db, job, str(exc))


def _start_source_resume(
    job_id: str, raw_video_path: Path, subtitle_path: Path
) -> None:
    threading.Thread(
        target=_resume_after_source,
        args=(job_id, raw_video_path, subtitle_path),
        daemon=True,
    ).start()


def _resume_after_download(job_id: str, raw_video_path: Path) -> None:
    bind_job(job_id)
    try:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if not job:
                return
            _continue_after_download(db, job, raw_video_path, _job_configuration(job))
    except JobCancelled:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if job:
                _mark_cancelled(db, job, "Worker stopped after cancellation request.")
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            if job:
                _mark_failed(db, job, str(exc))


def _start_download_resume(job_id: str, raw_video_path: Path) -> None:
    threading.Thread(
        target=_resume_after_download,
        args=(job_id, raw_video_path),
        daemon=True,
    ).start()


def _job_target_language(job_id: str) -> str | None:
    with SessionLocal() as db:
        job = db.get(MediaJob, job_id)
        return job.target_language if job else None


def _retry_checkpoint(job_id: str) -> dict[str, object]:
    root = ensure_storage()
    video_suffixes = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
    raw_videos = sorted(
        (
            path
            for path in (root / "raw-videos").glob(f"{job_id}.*")
            if path.is_file() and path.suffix.lower() in video_suffixes
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not raw_videos:
        return {"kind": "start", "label": "start"}

    target_language = _job_target_language(job_id)
    translated_subtitles = sorted(
        (
            path
            for path in (root / "subtitles").glob(f"{job_id}*.render.srt")
            if path.is_file()
            and path.stat().st_size > 0
            # A translation that still carries source-script text is not a
            # usable checkpoint: resuming from it would voice untranslated lines.
            and not _has_untranslated_cjk(path.read_text(encoding="utf-8", errors="ignore"), target_language)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if translated_subtitles:
        return {
            "kind": "translated",
            "label": "translated subtitle",
            "raw_video_path": raw_videos[0],
            "subtitle_path": translated_subtitles[0],
        }

    source_subtitles = sorted(
        (
            path
            for path in (root / "subtitles").glob(f"{job_id}*.srt")
            if path.is_file()
            and path.stat().st_size > 0
            and ".render." not in path.name
            and load_source_transcript_artifacts(path) is not None
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if source_subtitles:
        return {
            "kind": "source",
            "label": "source transcript",
            "raw_video_path": raw_videos[0],
            "subtitle_path": source_subtitles[0],
        }

    return {
        "kind": "download",
        "label": "downloaded video",
        "raw_video_path": raw_videos[0],
    }


def _speaker_voice_map(job: MediaJob) -> dict[str, dict[str, str]]:
    try:
        value = json.loads(job.speaker_voice_map or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _speaker_checkpoint_path(job_id: str) -> Path:
    return ensure_storage() / "diarization" / f"{job_id}.checkpoint.json"


def _write_speaker_checkpoint(
    job_id: str, raw_video_path: Path, subtitle_path: Path
) -> Path:
    path = _speaker_checkpoint_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "raw_video_path": str(raw_video_path.resolve()),
                "subtitle_path": str(subtitle_path.resolve()),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _read_speaker_checkpoint(job_id: str) -> dict[str, object]:
    path = _speaker_checkpoint_path(job_id)
    if not path.exists():
        raise RuntimeError("Speaker review checkpoint is missing; retry the job.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Speaker review checkpoint is invalid.")
    return data


def _set_step(
    db: Session, job: MediaJob, status: MediaJobStatus, progress: int, step: str
) -> None:
    _raise_if_cancelled(job.id)
    job.status = status
    job.progress = progress
    job.current_step = step
    job.error_message = None
    job.logs = append_log(job.logs, step)
    job.updated_at = datetime.utcnow()
    _persist_job_log(job)
    db.commit()
    db.refresh(job)
    # Reset the live sub-status to this step's default; deeper services publish
    # richer detail (synthesising vs reconnecting) as the step runs.
    report_progress(_default_step_detail(status))


def _raise_if_cancelled(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(MediaJob, job_id)
        if job and job.status == MediaJobStatus.cancelled:
            raise JobCancelled()


def _mark_cancelled(db: Session, job: MediaJob, message: str) -> None:
    if job.status != MediaJobStatus.cancelled:
        job.status = MediaJobStatus.cancelled
    job.current_step = "Cancelled"
    job.error_message = None
    if message not in (job.logs or ""):
        job.logs = append_log(job.logs, message)
    job.completed_at = datetime.utcnow()
    job.updated_at = datetime.utcnow()
    _persist_job_log(job)
    db.commit()
    clear_progress(job.id)


def _mark_failed(db: Session, job: MediaJob, message: str) -> None:
    job.status = MediaJobStatus.failed
    job.current_step = "Failed"
    job.error_message = message
    job.logs = append_log(job.logs, f"Failed: {message}")
    job.completed_at = datetime.utcnow()
    job.updated_at = datetime.utcnow()
    _persist_job_log(job)
    db.commit()
    clear_progress(job.id)


def _log_artifact(job: MediaJob, artifact_path: Path) -> None:
    job.logs = append_log(job.logs, f"Created artifact: {artifact_path.name}")


def _log_tts_chunk_manifest(job: MediaJob, audio_path: Path) -> None:
    manifest_path = audio_path.with_suffix(".chunks.json")
    srt_manifest_path = audio_path.with_suffix(".srt-tts.json")
    if srt_manifest_path.exists():
        try:
            import json

            manifest = json.loads(srt_manifest_path.read_text(encoding="utf-8"))
            strategy = manifest.get("strategy") or "remote_omnivoice_srt_endpoint"
        except Exception:
            strategy = "remote_omnivoice_srt_endpoint"
        job.logs = append_log(
            job.logs, f"TTS generated by OmniVoice SRT endpoint ({strategy})."
        )
        _log_artifact(job, srt_manifest_path)
        return

    if not manifest_path.exists():
        return

    try:
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        chunk_count = int(manifest.get("chunk_count") or 0)
    except Exception:
        _log_artifact(job, manifest_path)
        return

    job.logs = append_log(
        job.logs,
        f"TTS generated {chunk_count} target-duration voice chunks and overlaid them on the SRT timeline.",
    )
    _log_artifact(job, manifest_path)


def _job_configuration(job: MediaJob) -> ProductionConfiguration | None:
    if not _worker_snapshot_enabled() or not job.configuration_snapshot_json:
        return None
    try:
        return ProductionConfiguration.model_validate(
            parse_configuration_json(job.configuration_snapshot_json)
        )
    except Exception as exc:
        raise RuntimeError(f"Job configuration snapshot is invalid: {exc}") from exc


def _job_voice_overrides(
    job: MediaJob, configuration: ProductionConfiguration | None
) -> VoiceOverrides:
    return VoiceOverrides(
        mode=configuration.voice.mode if configuration else job.voice_mode,
        instruction=configuration.voice.instruction
        if configuration
        else job.voice_instruction,
        reference_audio_url=(
            configuration.voice.reference.url
            if configuration
            else job.voice_reference_audio_url
        ),
        reference_audio_path=configuration.voice.reference.path
        if configuration
        else "",
        reference_text=(
            configuration.voice.reference.text
            or configuration.voice.reference.text_path
            if configuration
            else job.voice_reference_text
        ),
    )


def editor_voice_settings(job: MediaJob) -> dict[str, object]:
    """Voice parameters for re-voicing single editor segments like the pipeline did."""
    configuration = _job_configuration(job)
    return {
        "voice": configuration.voice.voice_id if configuration else job.voice,
        "provider": configuration.voice.provider if configuration else "",
        "voice_rate": _configuration_voice_rate(configuration)
        if configuration
        else job.voice_rate,
        "target_language": configuration.languages.target
        if configuration
        else job.target_language,
        "voice_overrides": _job_voice_overrides(job, configuration),
    }


def start_editor_render(job: MediaJob) -> None:
    """Render the editor workspace in the background without touching the job status.

    The job keeps its current output until the edited render succeeds, so a
    failed edit render never breaks a finished job.
    """
    if job.status in RUNNING_STATUSES:
        raise ValueError("Job đang chạy, chờ xong rồi mới render bản chỉnh sửa.")
    with editor.workspace_lock(job.id):
        workspace = editor.load_workspace(job.id)
        if not workspace:
            raise ValueError("Chưa có workspace để render.")
        if workspace["render"].get("status") == "running" and job.id in _EDITOR_RENDERS:
            raise ValueError("Đang render bản chỉnh sửa.")
        workspace["render"] = {
            "status": "running",
            "started_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        editor.save_workspace(workspace)
    _EDITOR_RENDERS.add(job.id)
    threading.Thread(target=_run_editor_render, args=(job.id,), daemon=True).start()


def editor_render_alive(job_id: str) -> bool:
    return job_id in _EDITOR_RENDERS


def _run_editor_render(job_id: str) -> None:
    try:
        with SessionLocal() as db:
            job = db.get(MediaJob, job_id)
            configuration = _job_configuration(job)
            config_dict = configuration.model_dump(mode="json") if configuration else None
            burn = bool(getattr(job, "burn_subtitles", True)) and (
                configuration.subtitle.style_id != NO_SUBTITLE_STYLE_ID
                if configuration
                else True
            )
            with editor.workspace_lock(job_id):
                workspace = editor.load_workspace(job_id)
            try:
                output = editor.render_workspace(
                    workspace,
                    config_dict,
                    render_quality=configuration.output.render_quality
                    if configuration
                    else job.render_quality,
                    burn_subtitles=burn,
                )
                render_state = {"status": "done", "output_url": f"/storage/rendered-outputs/{output.name}"}
                job.output_url = render_state["output_url"]
                job.logs = append_log(job.logs, f"Editor render completed: {output.name}")
            except Exception as exc:
                render_state = {"status": "failed", "error": str(exc)[-1500:]}
                job.logs = append_log(job.logs, f"Editor render failed: {exc}")
            job.updated_at = datetime.utcnow()
            _persist_job_log(job)
            db.commit()
            with editor.workspace_lock(job_id):
                workspace = editor.load_workspace(job_id)
                workspace["render"] = {
                    **render_state,
                    "finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                }
                editor.save_workspace(workspace)
    finally:
        _EDITOR_RENDERS.discard(job_id)


def _configuration_voice_rate(configuration: ProductionConfiguration | None) -> str:
    if not configuration:
        return "+0%"
    value = int(configuration.voice.rate)
    return f"{value:+d}%"


def _is_manual_render_mode(
    job: MediaJob,
    configuration: ProductionConfiguration | None = None,
) -> bool:
    if configuration:
        return (
            configuration.output.render_mode == "manual"
            or configuration.workflow.audio_review
        )
    return (job.render_mode or "auto").strip().lower() == "manual"


def _worker_snapshot_enabled() -> bool:
    return os.getenv("AETHER_WORKER_SNAPSHOT_V2", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _source_first_pipeline_enabled() -> bool:
    import os

    return os.getenv("AETHER_SOURCE_FIRST_PIPELINE", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def append_log(logs: str | None, message: str) -> str:
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    current = logs or ""
    return f"{current}\n[{timestamp}Z] {message}".strip()


def _persist_job_log(job: MediaJob) -> None:
    root = ensure_storage()
    (root / "logs" / f"{job.id}.log").write_text(job.logs or "", encoding="utf-8")
