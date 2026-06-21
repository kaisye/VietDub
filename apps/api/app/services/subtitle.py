from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from .caption_text import remove_non_speech_tags
from .storage import ensure_storage

logger = logging.getLogger(__name__)


@dataclass
class TranscriptWord:
    text: str
    start: float
    end: float


@dataclass
class TranscriptSpan:
    id: str
    start: float
    end: float
    raw_text: str
    reconstructed_text: str
    detected_language: str
    timestamp_quality: Literal["observed", "approximate"]
    words: list[TranscriptWord] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    retry_status: Literal["not_needed", "not_attempted", "kept_original", "replaced", "failed"] = "not_needed"
    retry_language: str | None = None
    retry_text: str | None = None


@dataclass(frozen=True)
class SourceTranscriptArtifacts:
    subtitle_path: Path
    transcript_path: Path
    quality_path: Path
    dominant_language: str
    needs_review: bool
    timestamp_quality: Literal["observed", "approximate", "mixed"]
    retried_spans: int
    replaced_spans: int


def extract_subtitle(
    video_path: Path,
    brief: str | None = None,
    target_language: str = "EN",
    source_language: str | None = None,
    source_url: str | None = None,
    subtitle_strategy: str = "auto",
    ocr_region: dict | None = None,
) -> Path:
    """Return a subtitle file that becomes the source for TTS.

    Order of operations:
    1. Extract embedded subtitles from the video if present.
    2. Generate subtitles with NVIDIA Integrate from the supplied brief if configured.
    3. Use deterministic local generation only when explicitly enabled for tests/dev.
    """
    strategy = (subtitle_strategy or "auto").strip().lower()
    if strategy not in {"auto", "embedded", "speech", "ocr"}:
        strategy = "auto"
    # When the user disables "prefer existing subtitles" in Config, an auto job
    # ignores sidecar/embedded tracks and always transcribes from speech.
    if strategy == "auto" and not _prefer_existing_subtitles():
        strategy = "speech"
    # Hard-subtitle OCR is an explicit choice: read the burned-in captions
    # directly instead of trusting sidecar/embedded tracks or guessing from audio.
    if strategy == "ocr":
        return _extract_hardsub_subtitle_or_raise(
            video_path, target_language, source_language, ocr_region
        )
    if strategy != "speech":
        extracted = _find_sidecar_subtitle(video_path, target_language, source_language)
        if extracted:
            return extracted

        extracted = _extract_embedded_subtitle(video_path, target_language)
        if extracted:
            return extracted
        if strategy == "embedded":
            raise RuntimeError("No embedded or sidecar subtitle track was found.")

    normalized_brief = _normalize_text(brief)
    if normalized_brief and os.getenv("AETHER_ALLOW_LOCAL_SUBTITLE_GENERATION") == "1":
        return _generate_subtitle_locally(video_path, normalized_brief, target_language)

    stt_degraded_error: RuntimeError | None = None
    if _stt_fallback_enabled() and _nvidia_configured():
        try:
            return _generate_subtitle_with_nvidia_whisper(video_path, target_language, source_language, source_url)
        except RuntimeError as exc:
            if _is_riva_degraded_error(exc):
                logger.warning(
                    "NVIDIA Riva STT is temporarily DEGRADED for job %s. "
                    "Trying Groq Whisper fallback next. Original error: %s",
                    video_path.stem,
                    exc,
                )
                stt_degraded_error = exc
            else:
                raise

    # Groq Whisper fallback — used when Riva is degraded or not configured.
    if _groq_stt_enabled() and _groq_stt_configured():
        try:
            logger.info("Running Groq Whisper STT for job %s.", video_path.stem)
            return _generate_subtitle_with_groq_whisper(video_path, target_language, source_language)
        except RuntimeError as exc:
            logger.warning(
                "Groq Whisper STT failed for job %s: %s. Falling back to LLM brief.",
                video_path.stem,
                exc,
            )
            if stt_degraded_error is None:
                stt_degraded_error = exc

    # Only use LLM brief generation when the brief is long enough to be meaningful.
    # Short job titles (e.g. "Video1", "test") produce hallucinated content that has
    # nothing to do with the actual video. The threshold is configurable so users who
    # deliberately provide short keywords can lower it via AETHER_BRIEF_MIN_LENGTH.
    brief_min_length = int(os.getenv("AETHER_BRIEF_MIN_LENGTH", "30"))
    if normalized_brief and len(normalized_brief) >= brief_min_length and _nvidia_configured():
        if stt_degraded_error is not None:
            logger.warning(
                "Riva STT unavailable — generating subtitles from LLM brief (%d chars). "
                "The result reflects the brief, not the actual audio. "
                "Retry the job when Riva recovers, or attach an SRT sidecar instead.",
                len(normalized_brief),
            )
        return _generate_subtitle_with_nvidia(video_path, normalized_brief, target_language, source_url)

    if stt_degraded_error is not None:
        if normalized_brief:
            raise RuntimeError(
                "NVIDIA Riva STT is temporarily DEGRADED and the provided brief is too short "
                f"({len(normalized_brief)} chars, minimum {brief_min_length}) to generate reliable subtitles. "
                "Retry later, or attach a proper .srt sidecar file to your upload. "
                f"Original error: {stt_degraded_error}"
            )
        raise RuntimeError(
            "NVIDIA Riva STT is temporarily DEGRADED and no LLM brief fallback is available. "
            "Retry later, or provide an SRT file manually. "
            f"Original error: {stt_degraded_error}"
        )

    raise ValueError(_missing_subtitle_message(source_url, bool(normalized_brief)))


def _extract_hardsub_subtitle_or_raise(
    video_path: Path,
    target_language: str,
    source_language: str | None,
    ocr_region: dict | None = None,
) -> Path:
    # Imported lazily so the heavy OCR stack (rapidocr/cv2) only loads when the
    # user actually selects the hard-subtitle strategy, and to avoid an import
    # cycle (hardsub_ocr reuses helpers from this module).
    from .hardsub_ocr import extract_hardsub_subtitle

    path = extract_hardsub_subtitle(video_path, target_language, source_language, ocr_region)
    if path is None or not subtitle_to_plain_text(path):
        raise RuntimeError(
            "Hard subtitle OCR did not find readable captions in the sampled region. "
            "The video may not have burned-in subtitles, or they sit outside the default "
            "bottom band — adjust AETHER_OCR_REGION_Y/AETHER_OCR_REGION_H, or choose the "
            "Speech recognition source instead."
        )
    return path


def _prefer_existing_subtitles() -> bool:
    from .runtime_settings import get_runtime_settings

    try:
        return get_runtime_settings().prefer_existing_subtitles
    except Exception:
        return True


def subtitle_to_plain_text(subtitle_path: Path) -> str:
    return subtitle_to_plain_text_from_text(subtitle_path.read_text(encoding="utf-8"))


def subtitle_to_plain_text_from_text(subtitle_text: str) -> str:
    lines = subtitle_text.splitlines()
    text_lines = [
        cleaned
        for line in lines
        if line.strip() and not line.strip().isdigit() and "-->" not in line
        if (cleaned := remove_non_speech_tags(line.strip()))
    ]
    return " ".join(text_lines).strip()


def _find_sidecar_subtitle(video_path: Path, target_language: str, source_language: str | None) -> Path | None:
    candidates = list(video_path.parent.glob(f"{video_path.stem}*.srt")) + list(video_path.parent.glob(f"{video_path.stem}*.vtt"))
    candidates = [path for path in candidates if path.is_file() and path.name != video_path.name]
    if not candidates:
        return None

    ordered_languages = _language_candidates(target_language, source_language)
    for language in ordered_languages:
        match = next((path for path in candidates if f".{language.lower()}." in path.name.lower() or path.name.lower().endswith(f".{language.lower()}{path.suffix.lower()}")), None)
        if match:
            return _normalize_sidecar_to_srt(match, target_language)

    return _normalize_sidecar_to_srt(candidates[0], target_language)


def _normalize_sidecar_to_srt(path: Path, target_language: str) -> Path:
    if path.suffix.lower() == ".srt":
        destination = ensure_storage() / "subtitles" / f"{path.stem}.{target_language.lower()}.sidecar.srt"
        destination.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        return destination

    destination = ensure_storage() / "subtitles" / f"{path.stem}.{target_language.lower()}.sidecar.srt"
    command = ["ffmpeg", "-y", "-i", str(path), str(destination)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode == 0 and destination.exists() and subtitle_to_plain_text(destination):
        return destination

    text = _vtt_to_srt_text(path.read_text(encoding="utf-8", errors="ignore"))
    destination.write_text(text, encoding="utf-8")
    return destination


def _vtt_to_srt_text(text: str) -> str:
    text = re.sub(r"^\ufeff?WEBVTT.*?(\r?\n){2}", "", text, flags=re.DOTALL)
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if "-->" in block]
    normalized: list[str] = []
    for index, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and "-->" not in lines[0]:
            lines = lines[1:]
        if lines:
            lines[0] = re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", lines[0])
            normalized.append(f"{index}\n" + "\n".join(lines))
    return "\n\n".join(normalized)


def _language_candidates(target_language: str | None, source_language: str | None) -> list[str]:
    candidates: list[str] = []
    source = (source_language or "").strip()
    common_candidates = ["en", "zh-hans", "zh-hant", "zh", "vi", "ja", "ko", "es", "fr", "de"]
    values: list[str]
    if not source or source.lower() in {"auto", "detect", "unknown"}:
        values = [*common_candidates, target_language or ""]
    else:
        values = [source, source.split("-", 1)[0], target_language or "", *common_candidates]

    for value in values:
        language = (value or "").strip().lower()
        if language and language not in candidates:
            candidates.append(language)
    return candidates


def _extract_embedded_subtitle(video_path: Path, target_language: str) -> Path | None:
    subtitle_stream = _first_subtitle_stream(video_path)
    if subtitle_stream is None:
        return None

    root = ensure_storage()
    path = root / "subtitles" / f"{video_path.stem}.{target_language.lower()}.extracted.srt"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-map",
        f"0:{subtitle_stream}",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or not path.exists() or not subtitle_to_plain_text(path):
        return None
    return path


def _first_subtitle_stream(video_path: Path) -> int | None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        return None
    if not streams:
        return None
    return int(streams[0]["index"])


def _generate_subtitle_with_nvidia(video_path: Path, brief: str, target_language: str, source_url: str | None = None) -> Path:
    root = ensure_storage()
    duration = max(_probe_duration(video_path), 6.0)
    model = os.getenv("NVIDIA_MODEL", "openai/gpt-oss-120b")
    api_key = _nvidia_api_key()
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is required for LLM subtitle generation.")

    prompt = (
        "Create production-ready SRT subtitles for a localized video.\n"
        f"Target language: {target_language}\n"
        f"Video duration: {duration:.1f} seconds\n"
        "Return only valid SRT. Do not include markdown.\n"
        "Use the brief below as source material, but write natural subtitle lines rather than copying it verbatim.\n\n"
        f"Brief:\n{brief}"
    )
    try:
        response = httpx.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You generate concise, valid SRT subtitle files for media localization."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 16384,
                "temperature": 0.3,
                "top_p": 0.95,
                "top_k": 20,
                "presence_penalty": 0,
                "repetition_penalty": 1,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": True},
            },
            timeout=60,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(_subtitle_generation_error_message(exc, model, source_url)) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Subtitle fallback failed while contacting the LLM provider. "
            f"Source captions were not found in the downloaded video. Provider={model}. Error={exc}"
        ) from exc
    content = _extract_srt_content(response.json()["choices"][0]["message"]["content"].strip())
    if "-->" not in content:
        raise RuntimeError("LLM response did not contain valid SRT timing markers.")

    path = root / "subtitles" / f"{video_path.stem}.{target_language.lower()}.generated.srt"
    path.write_text(content, encoding="utf-8")
    return path


def _generate_subtitle_with_nvidia_whisper(
    video_path: Path,
    target_language: str,
    source_language: str | None,
    source_url: str | None = None,
) -> Path:
    audio_path = _extract_stt_audio(video_path)
    try:
        duration = _probe_duration(video_path)
        spans = _request_nvidia_whisper_spans(audio_path, source_language, duration)
        dominant_language = _dominant_transcript_language(spans, source_language)
        _apply_span_quality_flags(spans, dominant_language)
        if _source_first_pipeline_enabled():
            _recover_suspicious_spans(audio_path, spans, dominant_language)
            _apply_span_quality_flags(spans, dominant_language)
        srt_text = _transcript_spans_to_srt(spans)
    except ImportError as exc:
        raise RuntimeError(
            "No subtitle track was found, and NVIDIA Whisper STT fallback is enabled, but nvidia-riva-client is not installed. "
            "Install it with `pip install -r apps/api/requirements.txt`, then retry the job."
        ) from exc
    except Exception as exc:
        raise RuntimeError(_stt_generation_error_message(exc, source_url)) from exc

    if "-->" not in srt_text or not subtitle_to_plain_text_from_text(srt_text):
        raise RuntimeError("NVIDIA Whisper STT returned no readable transcript.")

    path = ensure_storage() / "subtitles" / f"{video_path.stem}.{target_language.lower()}.stt.srt"
    path.write_text(srt_text, encoding="utf-8")
    _write_source_transcript_artifacts(path, spans, dominant_language, source_kind="stt")
    return path


def _request_nvidia_whisper_srt(audio_path: Path, source_language: str | None, duration: float) -> str:
    return _transcript_spans_to_srt(_request_nvidia_whisper_spans(audio_path, source_language, duration))


def _request_nvidia_whisper_spans(
    audio_path: Path,
    source_language: str | None,
    duration: float,
) -> list[TranscriptSpan]:
    chunk_seconds = _stt_chunk_seconds()
    if duration <= chunk_seconds * 1.2:
        response = _request_nvidia_whisper_transcription(audio_path, source_language)
        return [_riva_response_to_transcript_span(response, 0.0, duration, "span-0001")]

    spans: list[TranscriptSpan] = []
    with tempfile.TemporaryDirectory(prefix="aether_stt_chunks_") as directory:
        chunks = _split_audio_for_stt(audio_path, Path(directory), chunk_seconds)
        for index, (chunk_path, offset, chunk_duration) in enumerate(chunks, start=1):
            response = _request_nvidia_whisper_transcription(chunk_path, source_language)
            spans.append(
                _riva_response_to_transcript_span(
                    response,
                    offset,
                    chunk_duration,
                    f"span-{index:04d}",
                )
            )
    return spans


def prepare_source_transcript(
    video_path: Path,
    subtitle_path: Path,
    source_language: str | None = None,
) -> SourceTranscriptArtifacts:
    """Return canonical source artifacts for the source-first pipeline.

    STT paths already have raw response metadata and adaptive recovery. Sidecar
    and embedded subtitles are wrapped here so every downstream stage consumes
    the same source contract.
    """
    existing = load_source_transcript_artifacts(subtitle_path)
    if existing:
        return existing

    spans = _srt_to_transcript_spans(subtitle_path.read_text(encoding="utf-8", errors="ignore"))
    dominant_language = _dominant_transcript_language(spans, source_language)
    _apply_span_quality_flags(spans, dominant_language, flag_missing_words=False)
    source_kind = "sidecar_or_embedded"
    return _write_source_transcript_artifacts(subtitle_path, spans, dominant_language, source_kind)


def load_source_transcript_artifacts(subtitle_path: Path) -> SourceTranscriptArtifacts | None:
    transcript_path, quality_path = _source_artifact_paths(subtitle_path)
    if not transcript_path.exists() or not quality_path.exists():
        return None
    try:
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return SourceTranscriptArtifacts(
        subtitle_path=subtitle_path,
        transcript_path=transcript_path,
        quality_path=quality_path,
        dominant_language=str(quality.get("dominant_language") or "unknown"),
        needs_review=bool(quality.get("needs_review")),
        timestamp_quality=_normalized_timestamp_quality(quality.get("timestamp_quality")),
        retried_spans=int(quality.get("retried_spans") or 0),
        replaced_spans=int(quality.get("replaced_spans") or 0),
    )


def source_transcript_log_messages(artifacts: SourceTranscriptArtifacts) -> list[str]:
    try:
        quality = json.loads(artifacts.quality_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    messages: list[str] = []
    for span in quality.get("spans") or []:
        if not isinstance(span, dict):
            continue
        flags = [str(flag) for flag in span.get("quality_flags") or []]
        retry_status = str(span.get("retry_status") or "not_needed")
        if retry_status == "not_needed" and not flags:
            continue
        messages.append(
            "Source span "
            f"{span.get('id')} ({float(span.get('start') or 0):.2f}-{float(span.get('end') or 0):.2f}s): "
            f"flags={','.join(flags) or 'none'}, retry={retry_status}, "
            f"language={span.get('retry_language') or span.get('detected_language') or 'unknown'}."
        )
    return messages


def _riva_response_to_transcript_span(
    response: object,
    offset: float,
    duration: float,
    span_id: str,
) -> TranscriptSpan:
    raw_words = _riva_response_words(response)
    words = [
        TranscriptWord(
            text=str(word["word"]),
            start=offset + float(word["start"]),
            end=offset + float(word["end"]),
        )
        for word in raw_words
    ]
    text = _normalize_text(
        " ".join(word.text for word in words)
        if words
        else _riva_response_transcript(response)
    )
    return TranscriptSpan(
        id=span_id,
        start=max(0.0, offset),
        end=max(offset + 0.2, offset + duration),
        raw_text=text,
        reconstructed_text=text,
        detected_language=_detect_text_language(text),
        timestamp_quality="observed" if words else "approximate",
        words=words,
    )


def _recover_suspicious_spans(
    audio_path: Path,
    spans: list[TranscriptSpan],
    dominant_language: str,
) -> None:
    if dominant_language in {"", "unknown", "mixed"}:
        return

    overlap = _optional_float("AETHER_STT_RECOVERY_OVERLAP_SECONDS", 1.0, 0.0, 5.0)
    with tempfile.TemporaryDirectory(prefix="aether_stt_recovery_") as directory:
        output_dir = Path(directory)
        for index, span in enumerate(spans):
            if not _span_needs_retry(span, dominant_language):
                span.retry_status = "not_needed"
                continue

            span.retry_status = "not_attempted"
            span.retry_language = dominant_language
            start = max(0.0, span.start - overlap)
            end = min(spans[-1].end, span.end + overlap)
            retry_path = output_dir / f"retry_{index:04d}.wav"
            try:
                _extract_audio_span(audio_path, retry_path, start, end)
                response = _request_nvidia_whisper_transcription(retry_path, dominant_language)
                retry = _riva_response_to_transcript_span(
                    response,
                    start,
                    end - start,
                    f"{span.id}-retry",
                )
                retry = _trim_retry_span(retry, span.start, span.end)
                span.retry_text = retry.reconstructed_text
                retry_flags = _quality_flags_for_span(retry, dominant_language)
                if _should_accept_recovery(span, retry, dominant_language, retry_flags):
                    span.reconstructed_text = retry.reconstructed_text
                    span.detected_language = retry.detected_language
                    span.timestamp_quality = retry.timestamp_quality
                    span.words = retry.words
                    span.retry_status = "replaced"
                else:
                    span.retry_status = "kept_original"
            except Exception:
                span.retry_status = "failed"


def _trim_retry_span(retry: TranscriptSpan, start: float, end: float) -> TranscriptSpan:
    words = [
        word
        for word in retry.words
        if word.end > start and word.start < end
    ]
    text = _normalize_text(" ".join(word.text for word in words)) if words else retry.reconstructed_text
    return TranscriptSpan(
        id=retry.id,
        start=start,
        end=end,
        raw_text=text,
        reconstructed_text=text,
        detected_language=_detect_text_language(text),
        timestamp_quality="observed" if words else "approximate",
        words=words,
    )


def _extract_audio_span(audio_path: Path, destination: Path, start: float, end: float) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=_stt_ffmpeg_timeout_seconds())
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError(f"Unable to extract STT recovery span: {result.stderr[-500:]}")


def _span_needs_retry(span: TranscriptSpan, dominant_language: str) -> bool:
    flags = set(span.quality_flags)
    return bool(
        "language_mismatch" in flags
        or "hallucination_marker" in flags
        or "repeated_phrase" in flags
        or "empty_transcript" in flags
    )


def _should_accept_recovery(
    original: TranscriptSpan,
    retry: TranscriptSpan,
    dominant_language: str,
    retry_flags: list[str] | None = None,
) -> bool:
    retry_flags = retry_flags if retry_flags is not None else _quality_flags_for_span(retry, dominant_language)
    original_flags = set(original.quality_flags)
    severe_original = bool(
        original_flags.intersection({"hallucination_marker", "repeated_phrase", "empty_transcript"})
    )
    if not severe_original:
        # A coherent code-switch is preserved even when it differs from the
        # dominant language. Retry metadata remains available for review.
        return False
    if not retry.reconstructed_text.strip():
        return False
    if set(retry_flags).intersection({"hallucination_marker", "repeated_phrase", "empty_transcript"}):
        return False
    original_fit = _language_fit_score(original.reconstructed_text, dominant_language)
    retry_fit = _language_fit_score(retry.reconstructed_text, dominant_language)
    return retry_fit >= 0.55 and retry_fit >= original_fit + 0.25


def _apply_span_quality_flags(
    spans: list[TranscriptSpan],
    dominant_language: str,
    *,
    flag_missing_words: bool = True,
) -> None:
    for span in spans:
        preserved_retry_flags = [
            flag for flag in span.quality_flags if flag.startswith("retry_")
        ]
        span.quality_flags = _quality_flags_for_span(
            span,
            dominant_language,
            flag_missing_words=flag_missing_words,
        )
        if span.retry_status == "failed":
            preserved_retry_flags.append("retry_failed")
        elif span.retry_status == "kept_original":
            preserved_retry_flags.append("retry_not_selected")
        span.quality_flags.extend(flag for flag in preserved_retry_flags if flag not in span.quality_flags)


def _quality_flags_for_span(
    span: TranscriptSpan,
    dominant_language: str,
    *,
    flag_missing_words: bool = True,
) -> list[str]:
    text = _normalize_text(span.reconstructed_text)
    flags: list[str] = []
    if not text:
        flags.append("empty_transcript")
        return flags
    if flag_missing_words and span.timestamp_quality != "observed":
        flags.append("missing_word_timestamps")
    if dominant_language not in {"", "unknown", "mixed"}:
        fit = _language_fit_score(text, dominant_language)
        if fit < 0.2 and _text_unit_length(text) >= 8:
            flags.append("language_mismatch")
    if _has_hallucination_marker(text):
        flags.append("hallucination_marker")
    if _has_repeated_phrase(text):
        flags.append("repeated_phrase")
    duration = max(0.1, span.end - span.start)
    if _text_unit_length(text) / duration < 0.15:
        flags.append("sparse_transcript")
    return flags


def _has_hallucination_marker(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.casefold()).strip(" .,!?:;")
    if normalized in {"you", "thank you", "thanks for watching", "subscribe"}:
        return True
    if re.search(r"(?:^|[.!?]\s*)(?:thank you|thanks for watching)(?:[.!?]|$)", normalized):
        return True
    if re.search(r"(?:[.!?]\s*|^)(?:you)(?:[.!?]\s*|$)", normalized):
        return True
    return normalized.endswith(" you") and len(normalized.split()) > 8


def _has_repeated_phrase(text: str) -> bool:
    tokens = re.findall(r"[\w\u3400-\u9fff]+", text.casefold())
    if len(tokens) < 8:
        return False
    counts: dict[str, int] = {}
    for token in tokens:
        if len(token) < 3:
            continue
        counts[token] = counts.get(token, 0) + 1
    return any(count >= 3 and count / len(tokens) >= 0.12 for count in counts.values())


def _dominant_transcript_language(
    spans: list[TranscriptSpan],
    source_language: str | None,
) -> str:
    configured = _normalized_source_language(source_language)
    if configured:
        return configured
    weights: dict[str, float] = {}
    for span in spans:
        language = _detect_text_language(span.reconstructed_text)
        if language == "unknown":
            continue
        duration = max(0.2, span.end - span.start)
        # Duration is harder for an ASR hallucination to inflate than text
        # length. Text units are only a small tie-breaker.
        score = duration * 1000 + min(500, _text_unit_length(span.reconstructed_text))
        weights[language] = weights.get(language, 0.0) + score
    if not weights:
        return "unknown"
    return max(weights, key=weights.get)


def _normalized_source_language(source_language: str | None) -> str | None:
    value = (source_language or "").strip().lower().replace("_", "-")
    if not value or value in {"auto", "unknown", "detect", "multi"}:
        return None
    aliases = {
        "chinese": "zh",
        "mandarin": "zh",
        "zh-cn": "zh",
        "zh-tw": "zh",
        "english": "en",
        "vietnamese": "vi",
        "japanese": "ja",
        "korean": "ko",
    }
    return aliases.get(value, value.split("-", 1)[0])


def _detect_text_language(text: str) -> str:
    if not text.strip():
        return "unknown"
    counts = {
        "ja": len(re.findall(r"[\u3040-\u30ff]", text)),
        "ko": len(re.findall(r"[\uac00-\ud7af]", text)),
        "zh": len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text)),
        "vi": len(re.findall(r"[ăâđêôơưĂÂĐÊÔƠƯàáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũỳýỵỷỹ]", text)),
        "en": len(re.findall(r"[A-Za-z]", text)),
    }
    if counts["ja"]:
        counts["ja"] += counts["zh"]
    if counts["ko"]:
        counts["ko"] += counts["zh"]
    if counts["vi"]:
        counts["vi"] += counts["en"]
    language, count = max(counts.items(), key=lambda item: item[1])
    if count <= 0:
        return "unknown"
    return language


def _language_fit_score(text: str, language: str) -> float:
    compact = re.sub(r"[\s\d\W_]+", "", text, flags=re.UNICODE)
    if not compact:
        return 0.0
    if language == "zh":
        matched = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", compact))
    elif language == "ja":
        matched = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", compact))
    elif language == "ko":
        matched = len(re.findall(r"[\uac00-\ud7af\u3400-\u9fff]", compact))
    elif language in {"en", "vi", "latin"}:
        matched = len(re.findall(r"[A-Za-zÀ-ỹ]", compact))
    else:
        return 0.0
    return matched / max(1, len(compact))


def _transcript_spans_to_srt(spans: list[TranscriptSpan]) -> str:
    blocks: list[str] = []
    next_index = 1
    for span in spans:
        if span.words:
            local_words = [
                {
                    "word": word.text,
                    "start": max(0.0, word.start - span.start),
                    "end": max(0.0, word.end - span.start),
                }
                for word in span.words
            ]
            chunk_srt = _word_timings_to_srt(local_words, offset=span.start)
        else:
            chunk_srt = _plain_transcript_to_srt(
                span.reconstructed_text,
                max(0.2, span.end - span.start),
                offset=span.start,
            )
        for block in _split_srt_blocks(chunk_srt):
            lines = block.splitlines()
            if len(lines) < 3:
                continue
            blocks.append(f"{next_index}\n" + "\n".join(lines[1:]).strip())
            next_index += 1
    return "\n\n".join(blocks).strip() + "\n" if blocks else ""


def _srt_to_transcript_spans(srt_text: str) -> list[TranscriptSpan]:
    spans: list[TranscriptSpan] = []
    for index, block in enumerate(_split_srt_blocks(srt_text), start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((position for position, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_text, end_text = lines[timing_index].split("-->", 1)
        start = _srt_timestamp_to_seconds(start_text.strip())
        end = _srt_timestamp_to_seconds(end_text.strip().split()[0])
        text = remove_non_speech_tags(_normalize_text(" ".join(lines[timing_index + 1 :])))
        if text and end > start:
            spans.append(
                TranscriptSpan(
                    id=f"span-{index:04d}",
                    start=start,
                    end=end,
                    raw_text=text,
                    reconstructed_text=text,
                    detected_language=_detect_text_language(text),
                    timestamp_quality="observed",
                )
            )
    return spans


def _write_source_transcript_artifacts(
    subtitle_path: Path,
    spans: list[TranscriptSpan],
    dominant_language: str,
    source_kind: str,
) -> SourceTranscriptArtifacts:
    transcript_path, quality_path = _source_artifact_paths(subtitle_path)
    timestamp_quality = _aggregate_timestamp_quality(spans)
    severe_flags = {"hallucination_marker", "repeated_phrase", "empty_transcript", "retry_failed"}
    needs_review = any(severe_flags.intersection(span.quality_flags) for span in spans)
    retried_spans = sum(span.retry_status not in {"not_needed", "not_attempted"} for span in spans)
    replaced_spans = sum(span.retry_status == "replaced" for span in spans)
    transcript_payload = {
        "version": 1,
        "source_kind": source_kind,
        "subtitle_filename": subtitle_path.name,
        "dominant_language": dominant_language,
        "timestamp_quality": timestamp_quality,
        "needs_review": needs_review,
        "spans": [
            {
                **{
                    key: value
                    for key, value in asdict(span).items()
                    if key != "words"
                },
                "words": [asdict(word) for word in span.words],
            }
            for span in spans
        ],
    }
    quality_payload = {
        "version": 1,
        "dominant_language": dominant_language,
        "timestamp_quality": timestamp_quality,
        "needs_review": needs_review,
        "span_count": len(spans),
        "retried_spans": retried_spans,
        "replaced_spans": replaced_spans,
        "flag_counts": _quality_flag_counts(spans),
        "spans": [
            {
                "id": span.id,
                "start": span.start,
                "end": span.end,
                "detected_language": span.detected_language,
                "timestamp_quality": span.timestamp_quality,
                "quality_flags": span.quality_flags,
                "retry_status": span.retry_status,
                "retry_language": span.retry_language,
            }
            for span in spans
        ],
    }
    transcript_path.write_text(json.dumps(transcript_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    quality_path.write_text(json.dumps(quality_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return SourceTranscriptArtifacts(
        subtitle_path=subtitle_path,
        transcript_path=transcript_path,
        quality_path=quality_path,
        dominant_language=dominant_language,
        needs_review=needs_review,
        timestamp_quality=timestamp_quality,
        retried_spans=retried_spans,
        replaced_spans=replaced_spans,
    )


def _source_artifact_paths(subtitle_path: Path) -> tuple[Path, Path]:
    root = ensure_storage()
    transcript_path = root / "source-transcripts" / f"{subtitle_path.stem}.source-transcript.json"
    quality_path = root / "reports" / f"{subtitle_path.stem}.source-quality.json"
    return transcript_path, quality_path


def _quality_flag_counts(spans: list[TranscriptSpan]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in spans:
        for flag in span.quality_flags:
            counts[flag] = counts.get(flag, 0) + 1
    return counts


def _aggregate_timestamp_quality(
    spans: list[TranscriptSpan],
) -> Literal["observed", "approximate", "mixed"]:
    qualities = {span.timestamp_quality for span in spans}
    if qualities == {"observed"}:
        return "observed"
    if qualities == {"approximate"} or not qualities:
        return "approximate"
    return "mixed"


def _normalized_timestamp_quality(value: object) -> Literal["observed", "approximate", "mixed"]:
    return str(value) if value in {"observed", "approximate", "mixed"} else "approximate"


def _source_first_pipeline_enabled() -> bool:
    return os.getenv("AETHER_SOURCE_FIRST_PIPELINE", "1").strip().lower() not in {"0", "false", "off", "no"}


def _split_audio_for_stt(audio_path: Path, output_dir: Path, chunk_seconds: float) -> list[tuple[Path, float, float]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "chunk_%04d.wav"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-f",
        "segment",
        "-segment_time",
        f"{chunk_seconds:.3f}",
        "-reset_timestamps",
        "1",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(pattern),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=_stt_ffmpeg_timeout_seconds())
    if result.returncode != 0:
        raise RuntimeError(f"Unable to split audio for STT: {result.stderr[-500:] or audio_path.name}")

    chunks: list[tuple[Path, float, float]] = []
    offset = 0.0
    for chunk_path in sorted(output_dir.glob("chunk_*.wav")):
        chunk_duration = _probe_duration(chunk_path)
        if chunk_duration <= 0:
            continue
        chunks.append((chunk_path, offset, chunk_duration))
        offset += chunk_duration
    if not chunks:
        raise RuntimeError("Unable to split audio for STT: no audio chunks were created.")
    return chunks


def _split_srt_blocks(srt_text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", srt_text.strip()) if "-->" in block]


def _extract_stt_audio(video_path: Path) -> Path:
    path = ensure_storage() / "audio" / f"{video_path.stem}.stt.wav"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=_stt_ffmpeg_timeout_seconds())
    if result.returncode != 0 or not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"Unable to extract audio for STT: {result.stderr[-500:] or video_path.name}")
    return path


def _request_nvidia_whisper_transcription(audio_path: Path, source_language: str | None):
    try:
        import riva.client
    except ImportError:
        raise

    api_key = _nvidia_api_key()
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is required for NVIDIA Whisper STT.")

    max_message_length = _optional_positive_int("AETHER_STT_GRPC_MAX_MESSAGE_BYTES") or 256 * 1024 * 1024
    auth = riva.client.Auth(
        use_ssl=True,
        uri=os.getenv("AETHER_STT_RIVA_SERVER", "grpc.nvcf.nvidia.com:443").strip() or "grpc.nvcf.nvidia.com:443",
        metadata_args=[
            ["function-id", _stt_function_id()],
            ["authorization", f"Bearer {api_key}"],
        ],
        options=[
            ("grpc.max_receive_message_length", max_message_length),
            ("grpc.max_send_message_length", max_message_length),
        ],
    )
    asr_service = riva.client.ASRService(auth)
    config = riva.client.RecognitionConfig(
        language_code=_stt_language_code(source_language),
        model=os.getenv("AETHER_STT_RIVA_MODEL", "").strip(),
        max_alternatives=1,
        profanity_filter=False,
        enable_automatic_punctuation=True,
        verbatim_transcripts=True,
        enable_word_time_offsets=_stt_word_timestamps_enabled(),
    )
    riva.client.add_audio_file_specs_to_config(config, audio_path)

    custom_configuration = os.getenv("AETHER_STT_RIVA_CUSTOM_CONFIGURATION", "").strip()
    if custom_configuration:
        riva.client.add_custom_configuration_to_config(config, custom_configuration)

    with audio_path.open("rb") as file:
        return asr_service.offline_recognize(file.read(), config)


def _riva_asr_response_to_srt(response: object, duration: float, offset: float = 0.0) -> str:
    words = _riva_response_words(response)
    if words:
        return _word_timings_to_srt(words, offset=offset)

    transcript = _riva_response_transcript(response)
    if not transcript:
        return ""
    return _plain_transcript_to_srt(transcript, duration, offset=offset)


def _riva_response_words(response: object) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    for result in getattr(response, "results", []) or []:
        alternatives = getattr(result, "alternatives", []) or []
        if not alternatives:
            continue
        for word in getattr(alternatives[0], "words", []) or []:
            text = str(getattr(word, "word", "") or "").strip()
            if not text:
                continue
            start = _riva_timestamp_to_seconds(getattr(word, "start_time", 0))
            end = _riva_timestamp_to_seconds(getattr(word, "end_time", 0))
            if end <= start:
                continue
            collected.append({"word": text, "start": start, "end": end})
    return collected


def _riva_response_transcript(response: object) -> str:
    chunks: list[str] = []
    for result in getattr(response, "results", []) or []:
        alternatives = getattr(result, "alternatives", []) or []
        if alternatives:
            transcript = str(getattr(alternatives[0], "transcript", "") or "").strip()
            if transcript:
                chunks.append(transcript)
    return " ".join(chunks).strip()


def _word_timings_to_srt(words: list[dict[str, object]], offset: float = 0.0) -> str:
    max_words = _optional_positive_int("AETHER_STT_SRT_MAX_WORDS") or 14
    max_duration = _optional_float("AETHER_STT_SRT_MAX_DURATION", 5.5, 1.0, 20.0)
    max_gap = _optional_float("AETHER_STT_SRT_MAX_GAP", 0.8, 0.0, 5.0)

    blocks: list[str] = []
    current: list[dict[str, object]] = []
    for word in words:
        if current:
            duration = float(word["end"]) - float(current[0]["start"])
            gap = float(word["start"]) - float(current[-1]["end"])
            sentence_end = _looks_like_sentence_end(str(current[-1]["word"]))
            if len(current) >= max_words or duration >= max_duration or gap > max_gap or sentence_end:
                blocks.append(_timed_words_to_srt_block(len(blocks) + 1, current, offset=offset))
                current = []
        current.append(word)

    if current:
        blocks.append(_timed_words_to_srt_block(len(blocks) + 1, current, offset=offset))

    return "\n\n".join(blocks).strip() + "\n"


def _timed_words_to_srt_block(index: int, words: list[dict[str, object]], offset: float = 0.0) -> str:
    text = _normalize_text(" ".join(str(word["word"]) for word in words))
    start = max(0.0, offset + float(words[0]["start"]))
    end = max(start + 0.2, offset + float(words[-1]["end"]))
    return f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}"


def _plain_transcript_to_srt(transcript: str, duration: float, offset: float = 0.0) -> str:
    transcript = _normalize_text(transcript)
    if not transcript:
        return ""

    mode = os.getenv("AETHER_STT_PLAIN_TRANSCRIPT_MODE", "raw").strip().lower()
    if mode not in {"split", "chunked"}:
        start = max(0.0, offset)
        end = max(start + 0.5, offset + duration)
        return f"1\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{transcript}\n"

    max_chars = _optional_positive_int("AETHER_STT_SRT_MAX_CHARS") or 96
    max_duration = _optional_float("AETHER_STT_SRT_MAX_PLAIN_DURATION", 6.0, 1.0, 30.0)
    min_chunks_for_duration = max(1, math.ceil(duration / max_duration))
    if min_chunks_for_duration > 1:
        max_chars = min(max_chars, max(8, math.ceil(_text_unit_length(transcript) / min_chunks_for_duration)))

    chunks = _chunk_text(transcript, max_chars=max_chars)
    chunks = _ensure_minimum_text_chunks(chunks, min_chunks_for_duration, max_chars)
    if not chunks:
        return ""
    segment_duration = duration / len(chunks)
    blocks: list[str] = []
    cursor = 0.0
    for index, chunk in enumerate(chunks, start=1):
        start = offset + cursor
        relative_end = min(duration, cursor + min(segment_duration, max_duration))
        end = offset + relative_end
        blocks.append(f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(max(end, start + 0.5))}\n{chunk}")
        cursor = min(duration, cursor + segment_duration)
    return "\n\n".join(blocks).strip() + "\n"


def _generate_subtitle_locally(video_path: Path, brief: str, target_language: str) -> Path:
    root = ensure_storage()
    duration = max(_probe_duration(video_path), 6.0)
    chunks = _chunk_text(brief)
    segment_duration = max(duration / max(len(chunks), 1), 2.0)

    path = root / "subtitles" / f"{video_path.stem}.{target_language.lower()}.generated.srt"
    entries: list[str] = []
    cursor = 0.0
    for index, chunk in enumerate(chunks, start=1):
        start = cursor
        end = min(duration, cursor + segment_duration)
        if index == len(chunks):
            end = max(end, duration)
        entries.append(f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{chunk}\n")
        cursor = end

    path.write_text("\n".join(entries), encoding="utf-8")
    return path


def _nvidia_configured() -> bool:
    return bool(_nvidia_api_key())


def _stt_fallback_enabled() -> bool:
    return os.getenv("AETHER_STT_FALLBACK_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}


def _is_riva_degraded_error(exc: Exception) -> bool:
    msg = str(exc)
    return "DEGRADED" in msg or ("INVALID_ARGUMENT" in msg and "degraded" in msg.lower())


# ── Groq Whisper STT ─────────────────────────────────────────────────────────

def _groq_api_key() -> str | None:
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    return key or None


def _groq_stt_configured() -> bool:
    return bool(_groq_api_key())


def _groq_stt_enabled() -> bool:
    return os.getenv("AETHER_GROQ_STT_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}


def _groq_iso_language_code(source_language: str | None) -> str | None:
    """Return ISO 639-1 code for Groq, or None to let Whisper auto-detect."""
    language = (source_language or "").strip().lower().replace("_", "-")
    if not language or language in {"auto", "unknown", "multi"}:
        return None
    aliases = {
        "english": "en", "en-us": "en", "en-gb": "en",
        "vietnamese": "vi", "vn": "vi",
        "chinese": "zh", "mandarin": "zh", "zh-cn": "zh", "zh-tw": "zh",
        "japanese": "ja", "korean": "ko", "french": "fr", "german": "de",
        "spanish": "es", "portuguese": "pt", "russian": "ru",
        "arabic": "ar", "thai": "th", "indonesian": "id",
    }
    return aliases.get(language, language.split("-", 1)[0]) or None


def _generate_subtitle_with_groq_whisper(
    video_path: Path,
    target_language: str,
    source_language: str | None,
) -> Path:
    audio_path = _extract_stt_audio(video_path)
    try:
        duration = _probe_duration(video_path)
        spans = _request_groq_whisper_spans(audio_path, source_language, duration)
        dominant_language = _dominant_transcript_language(spans, source_language)
        _apply_span_quality_flags(spans, dominant_language, flag_missing_words=False)
        srt_text = _transcript_spans_to_srt(spans)
    except Exception as exc:
        raise RuntimeError(f"Groq Whisper STT failed: {exc}") from exc

    if "-->" not in srt_text or not subtitle_to_plain_text_from_text(srt_text):
        raise RuntimeError("Groq Whisper STT returned no readable transcript.")

    path = ensure_storage() / "subtitles" / f"{video_path.stem}.{target_language.lower()}.groq.srt"
    path.write_text(srt_text, encoding="utf-8")
    _write_source_transcript_artifacts(path, spans, dominant_language, source_kind="stt")
    return path


def _request_groq_whisper_spans(
    audio_path: Path,
    source_language: str | None,
    duration: float,
) -> list[TranscriptSpan]:
    # WAV 16kHz mono 16-bit ≈ 32 KB/s → 25 MB Groq limit ≈ 780s. Use 600s.
    chunk_seconds = _optional_float("AETHER_GROQ_STT_CHUNK_SECONDS", 600.0, 30.0, 780.0)
    if duration <= chunk_seconds * 1.2:
        payload = _request_groq_transcription(audio_path, source_language)
        return _groq_payload_to_spans(payload, offset=0.0, span_id_start=1)

    spans: list[TranscriptSpan] = []
    with tempfile.TemporaryDirectory(prefix="aether_groq_chunks_") as directory:
        chunks = _split_audio_for_stt(audio_path, Path(directory), chunk_seconds)
        span_counter = 1
        for chunk_path, offset, _chunk_duration in chunks:
            payload = _request_groq_transcription(chunk_path, source_language)
            chunk_spans = _groq_payload_to_spans(payload, offset, span_counter)
            spans.extend(chunk_spans)
            span_counter += max(1, len(chunk_spans))
    return spans


def _request_groq_transcription(audio_path: Path, source_language: str | None) -> dict:
    api_key = _groq_api_key()
    model = (os.getenv("AETHER_GROQ_STT_MODEL") or "whisper-large-v3").strip()
    timeout = _optional_positive_int("AETHER_GROQ_STT_TIMEOUT_SECONDS") or 300

    with audio_path.open("rb") as audio_file:
        audio_bytes = audio_file.read()

    files = {"file": (audio_path.name, audio_bytes, "audio/wav")}
    data: dict[str, str] = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    iso_lang = _groq_iso_language_code(source_language)
    if iso_lang:
        data["language"] = iso_lang

    response = httpx.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files=files,
        data=data,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _groq_payload_to_spans(
    payload: dict,
    offset: float,
    span_id_start: int,
) -> list[TranscriptSpan]:
    segments = payload.get("segments") or []
    if not segments:
        text = _normalize_text(str(payload.get("text") or ""))
        if not text:
            return []
        chunk_duration = float(payload.get("duration") or 1.0)
        return [TranscriptSpan(
            id=f"span-{span_id_start:04d}",
            start=max(0.0, offset),
            end=max(offset + 0.2, offset + chunk_duration),
            raw_text=text,
            reconstructed_text=text,
            detected_language=_detect_text_language(text),
            timestamp_quality="approximate",
        )]

    result: list[TranscriptSpan] = []
    for i, seg in enumerate(segments):
        text = _normalize_text(str(seg.get("text") or "").strip())
        if not text:
            continue
        seg_start = offset + float(seg.get("start") or 0.0)
        seg_end = offset + float(seg.get("end") or (seg.get("start") or 0.0) + 1.0)
        result.append(TranscriptSpan(
            id=f"span-{span_id_start + i:04d}",
            start=max(0.0, seg_start),
            end=max(seg_start + 0.1, seg_end),
            raw_text=text,
            reconstructed_text=text,
            detected_language=_detect_text_language(text),
            timestamp_quality="observed",
        ))
    return result


def _stt_word_timestamps_enabled() -> bool:
    return os.getenv("AETHER_STT_WORD_TIMESTAMPS", "1").strip().lower() not in {"0", "false", "off", "no"}


def _stt_chunk_seconds() -> float:
    return _optional_float("AETHER_STT_CHUNK_SECONDS", 30.0, 5.0, 120.0)


def _stt_function_id() -> str:
    return (
        os.getenv("AETHER_STT_NVIDIA_FUNCTION_ID", "").strip()
        or os.getenv("NVIDIA_WHISPER_FUNCTION_ID", "").strip()
        or "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"
    )


def _stt_language_code(source_language: str | None) -> str:
    language = (source_language or "").strip().lower().replace("_", "-")
    if not language or language in {"auto", "unknown"}:
        return os.getenv("AETHER_STT_LANGUAGE_CODE", "multi").strip() or "multi"

    aliases = {
        "english": "en",
        "en-us": "en",
        "en-gb": "en",
        "vietnamese": "vi",
        "vn": "vi",
        "zh-cn": "zh",
        "zh-tw": "zh",
    }
    return aliases.get(language, language.split("-", 1)[0])


def _stt_ffmpeg_timeout_seconds() -> int:
    return _optional_positive_int("AETHER_STT_FFMPEG_TIMEOUT_SECONDS") or 600


def _stt_generation_error_message(exc: Exception, source_url: str | None) -> str:
    source_prefix = ""
    if _looks_like_facebook_url(source_url):
        source_prefix = "No subtitle track was found in the Facebook download, so Aether tried NVIDIA Whisper STT. "
    return (
        f"{source_prefix}NVIDIA Whisper STT failed before a source SRT could be created. "
        "Retry later, check NVIDIA_API_KEY/Riva access, or provide an SRT file manually. "
        f"Error: {exc}"
    )


def _riva_timestamp_to_seconds(value: object) -> float:
    if hasattr(value, "seconds") or hasattr(value, "nanos"):
        return float(getattr(value, "seconds", 0) or 0) + float(getattr(value, "nanos", 0) or 0) / 1_000_000_000

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric > 10_000:
        return numeric / 1000.0
    return numeric


def _looks_like_sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?。！？…]['\")\]]?\s*$", text.strip()))


def _optional_positive_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _optional_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _missing_subtitle_message(source_url: str | None, has_brief: bool) -> str:
    source_label = _source_label(source_url)
    if _looks_like_facebook_url(source_url):
        return (
            "No subtitle track was found for this Facebook video. Facebook downloads often include only the video/audio, "
            "not the creator captions. Upload/provide an SRT file, use a source that exposes captions, or add real audio STT "
            "before translation/TTS."
        )
    if has_brief:
        return (
            f"No subtitle track was found for this {source_label} video, and LLM subtitle fallback is not configured. "
            "Configure NVIDIA_API_KEY for brief-based subtitle generation, upload/provide an SRT file, or use a video that already contains captions."
        )
    return (
        f"No subtitle track was found for this {source_label} video. Upload/provide an SRT file, add a content brief for LLM fallback, "
        "or use a video that already contains captions."
    )


def _subtitle_generation_error_message(exc: httpx.HTTPStatusError, model: str, source_url: str | None) -> str:
    status_code = exc.response.status_code
    body = exc.response.text.strip()
    if len(body) > 500:
        body = body[:500].rstrip() + "..."

    source_prefix = ""
    if _looks_like_facebook_url(source_url):
        source_prefix = (
            "No subtitle track was found in the Facebook download, so Aether tried to create subtitles from the content brief. "
        )

    return (
        f"{source_prefix}Subtitle fallback failed: NVIDIA returned HTTP {status_code} for model '{model}'. "
        "This means the video was downloaded, but captions were not available and the LLM fallback did not complete. "
        "Retry later, switch translation/subtitle provider, provide an SRT file, or enable real audio STT. "
        f"Provider response: {body or 'empty response'}"
    )


def _source_label(source_url: str | None) -> str:
    if _looks_like_facebook_url(source_url):
        return "Facebook"
    if not source_url:
        return "source"
    lowered = source_url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "YouTube"
    return "source"


def _looks_like_facebook_url(source_url: str | None) -> bool:
    if not source_url:
        return False
    lowered = source_url.lower()
    return "facebook.com" in lowered or "fb.watch" in lowered


def _nvidia_api_key() -> str | None:
    api_key = (os.getenv("NVIDIA_API_KEY") or "").strip().strip('"').strip("'")
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()
    return api_key or None


def _strip_markdown_fence(text: str) -> str:
    if text.startswith("```"):
        text = re.sub(r"^```(?:srt)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_srt_content(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = _strip_markdown_fence(text)
    match = re.search(r"(?:^|\n)(\d+\s*\n\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}.*)", text, flags=re.DOTALL)
    if match:
        text = match.group(1)
    return re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", text.strip())


def _normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _chunk_text(text: str, max_chars: int = 84) -> list[str]:
    sentences = _split_sentences_for_subtitles(text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if _text_unit_length(candidate) <= max_chars:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    if not chunks:
        return [text]

    expanded: list[str] = []
    for chunk in chunks:
        if _text_unit_length(chunk) <= max_chars:
            expanded.append(chunk)
            continue
        words = chunk.split()
        if len(words) <= 1 or _contains_cjk(chunk):
            expanded.extend(_split_long_text_by_units(chunk, max_chars))
            continue
        group_size = max(6, math.ceil(len(words) / math.ceil(len(chunk) / max_chars)))
        for index in range(0, len(words), group_size):
            expanded.append(" ".join(words[index : index + group_size]))
    return expanded


def _split_sentences_for_subtitles(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.!?。！？…])\s*", normalized) if part.strip()]
    return parts or [normalized]


def _ensure_minimum_text_chunks(chunks: list[str], minimum: int, max_chars: int) -> list[str]:
    if len(chunks) >= minimum:
        return chunks

    result = list(chunks)
    while len(result) < minimum:
        split_index = max(range(len(result)), key=lambda index: _text_unit_length(result[index]), default=-1)
        if split_index < 0 or _text_unit_length(result[split_index]) <= 1:
            break
        left, right = _split_text_in_half(result[split_index], max_chars)
        if not left or not right:
            break
        result[split_index : split_index + 1] = [left, right]
    return result


def _split_text_in_half(text: str, max_chars: int) -> tuple[str, str]:
    parts = _split_long_text_by_units(text, max(1, math.ceil(_text_unit_length(text) / 2)))
    if len(parts) >= 2:
        return parts[0], _normalize_text(" ".join(parts[1:]))
    parts = _split_long_text_by_units(text, max_chars)
    if len(parts) >= 2:
        midpoint = len(parts) // 2
        return _normalize_text(" ".join(parts[:midpoint])), _normalize_text(" ".join(parts[midpoint:]))
    return text, ""


def _split_long_text_by_units(text: str, max_chars: int) -> list[str]:
    text = _normalize_text(text)
    if not text:
        return []
    if _contains_cjk(text):
        return _split_cjk_text_by_units(text, max_chars)

    words = text.split()
    if not words:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word]).strip()
        if current and _text_unit_length(candidate) > max_chars:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(" ".join(current))
    return chunks or [text]


def _split_cjk_text_by_units(text: str, max_chars: int) -> list[str]:
    max_chars = max(1, max_chars)
    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        should_flush = _text_unit_length(current) >= max_chars
        should_flush = should_flush or (char in "，,；;。！？!?…" and _text_unit_length(current) >= max_chars * 0.55)
        if should_flush:
            chunks.append(current.strip())
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]


def _text_unit_length(text: str) -> int:
    return len(_normalize_text(text).replace(" ", "")) if _contains_cjk(text) else len(_normalize_text(text))


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text))


def _probe_duration(video_path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return 6.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 6.0


def _format_srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _srt_timestamp_to_seconds(value: str) -> float:
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) != 3:
        return 0.0
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return 0.0


def trim_subtitle_to_clip(subtitle_path: Path, clip_seconds: float) -> Path:
    """Drop subtitle cues beyond a test-clip window.

    Test/preview mode trims the source video to the first ``clip_seconds``
    seconds. Trimming the subtitle the same way keeps downstream translation,
    TTS and rendering fast and aligned: cues that start after the window are
    removed and a cue straddling the boundary has its end clamped.
    """
    if clip_seconds <= 0:
        return subtitle_path
    try:
        text = subtitle_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return subtitle_path

    kept: list[str] = []
    index = 1
    for block in _split_srt_blocks(text):
        lines = block.splitlines()
        time_idx = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if time_idx < 0:
            continue
        start_raw, _, end_raw = lines[time_idx].partition("-->")
        start = _srt_timestamp_to_seconds(start_raw)
        end = _srt_timestamp_to_seconds(end_raw)
        if start >= clip_seconds:
            continue
        if end > clip_seconds:
            end = clip_seconds
        body = "\n".join(lines[time_idx + 1:]).strip()
        if not body:
            continue
        kept.append(
            f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{body}"
        )
        index += 1

    subtitle_path.write_text("\n\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return subtitle_path
