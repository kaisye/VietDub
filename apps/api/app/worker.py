from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import MediaJob, MediaJobStatus
from .services.audio_cues import attach_background_audio, create_audio_cue_manifest
from .services.audio_bed import prepare_background_audio
from .services.diarization import diarize_video, load_diarization
from .services.downloader import download_video
from .services.renderer import render_video
from .services.production_configuration import (
    NO_SUBTITLE_STYLE_ID,
    ProductionConfiguration,
    parse_configuration_json,
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
    normalize_subtitle_for_translation,
    translate_subtitle,
    translated_segments_path,
    translation_limit_label,
)
from .services.progress import bind_job, clear_progress, report_progress
from .services.tts import VoiceOverrides, generate_tts, tts_provider_override
from .services.voice_options import NO_VOICE_ID


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
        _raise_if_cancelled(job.id)
        _log_artifact(job, raw_video_path)

        _continue_after_download(db, job, raw_video_path, configuration)


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
    _log_artifact(job, translated_subtitle_path)
    segments_path = translated_segments_path(translated_subtitle_path)
    if segments_path.exists():
        _log_artifact(job, segments_path)

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
    voice_overrides = VoiceOverrides(
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
                translated_subtitle_path,
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
                translated_subtitle_path,
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
    output_path = render_video(
        raw_video_path,
        audio_path,
        translated_subtitle_path if subtitle_enabled else None,
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


def _resume_after_translation(
    job_id: str,
    raw_video_path: Path,
    translated_subtitle_path: Path,
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
) -> None:
    thread = threading.Thread(
        target=_resume_after_translation,
        args=(job_id, raw_video_path, translated_subtitle_path),
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

    translated_subtitles = sorted(
        (
            path
            for path in (root / "subtitles").glob(f"{job_id}*.render.srt")
            if path.is_file() and path.stat().st_size > 0
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
