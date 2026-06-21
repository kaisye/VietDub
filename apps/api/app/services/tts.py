from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import logging
import os
import re
import requests
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .progress import report_progress
from .runtime_settings import get_runtime_settings
from .speech_rate import SpeechRateProfile, speech_rate_duration_scale
from .storage import ensure_storage
from .subtitle import subtitle_to_plain_text
from .tts_runtime import RUNTIME_EDGE, resolve_effective_tts_runtime
from .vieneu_tts import VIENEU_PRESETS, is_vieneu_voice, synthesize_vieneu
from .voice_options import list_voice_options


logger = logging.getLogger(__name__)


VOICE_BY_LANGUAGE = {
    "en": "en-US-JennyNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
}


VOICE_ALIASES = {
    "james deep": "en-US-GuyNeural",
    "sarah adams": "en-US-JennyNeural",
    "robert b.": "en-US-BrianNeural",
}


GENERIC_VOICES = {"", "studio narrator", "default", "auto"}
_PROVIDER_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aether_tts_provider_override",
    default="",
)


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class AlignedSubtitleCue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TimedAudioSegment:
    path: Path
    start: float
    end: float


@dataclass(frozen=True)
class TtsChunk:
    start: float
    end: float
    cues: list[SubtitleCue]

    @property
    def text(self) -> str:
        return _dedupe_joined_text([cue.text for cue in self.cues])


@dataclass(frozen=True)
class VoiceOverrides:
    mode: str = ""
    instruction: str = ""
    reference_audio_url: str = ""
    reference_audio_path: str = ""
    reference_text: str = ""


@contextmanager
def tts_provider_override(provider: str | None):
    normalized = (provider or "").strip().lower()
    token = _PROVIDER_OVERRIDE.set(normalized if normalized in {"edge", "omnivoice", "vieneu"} else "")
    try:
        yield
    finally:
        _PROVIDER_OVERRIDE.reset(token)


def generate_tts(
    text_or_subtitle: Path,
    voice: str,
    target_language: str = "EN",
    voice_rate: str = "+0%",
    speech_rate: SpeechRateProfile | None = None,
    voice_overrides: VoiceOverrides | None = None,
    speaker_voice_map: dict[str, dict[str, str]] | None = None,
    diarization_job_id: str | None = None,
) -> Path:
    """Generate spoken audio from subtitle text and place each segment on the subtitle timeline."""
    root = ensure_storage()
    # Route VieNeu preset voices to the offline VieNeu engine regardless of the
    # configured/overridden provider — a VieNeu voice can only be spoken by VieNeu,
    # so the engine is implied by the selected voice (Edge and VieNeu coexist in the
    # same catalog).
    if is_vieneu_voice(voice) and _tts_provider() != "vieneu":
        logger.info("Voice '%s' is a VieNeu preset; routing to the VieNeu engine.", voice)
        with tts_provider_override("vieneu"):
            return generate_tts(
                text_or_subtitle,
                voice,
                target_language,
                voice_rate,
                speech_rate,
                voice_overrides,
                speaker_voice_map,
                diarization_job_id,
            )
    # The provider is explicitly VieNeu but the chosen voice isn't one of its presets
    # (e.g. a generic/default or Edge voice left selected). VieNeu can only speak its
    # built-in Vietnamese voices, so substitute the default preset for Vietnamese
    # targets and fall back to the always-available Edge voice for any other language —
    # never hand a non-preset id to the engine, which would hard-fail.
    if _tts_provider() == "vieneu" and not is_vieneu_voice(voice):
        if str(target_language or "").lower().startswith("vi") and VIENEU_PRESETS:
            default_voice = VIENEU_PRESETS[0][0]
            logger.info(
                "VieNeu provider with non-preset voice '%s'; using default preset '%s'.",
                voice,
                default_voice,
            )
            return generate_tts(
                text_or_subtitle,
                default_voice,
                target_language,
                voice_rate,
                speech_rate,
                voice_overrides,
                speaker_voice_map,
                diarization_job_id,
            )
        logger.warning(
            "VieNeu only speaks Vietnamese; falling back to Edge for language '%s'.",
            target_language,
        )
        with tts_provider_override("edge"):
            return generate_tts(
                text_or_subtitle,
                voice,
                target_language,
                voice_rate,
                speech_rate,
                voice_overrides,
                speaker_voice_map,
                diarization_job_id,
            )
    # Auto-promote to OmniVoice when the selected voice is a clone/design voice.
    # Edge TTS has no concept of reference audio or voice cloning — forwarding a
    # custom voice ID to it would silently substitute the language-default neural voice
    # (e.g. "CDTeam" → "vi-VN-HoaiMyNeural"). Re-run inside an OmniVoice override so
    # the user's explicit clone voice selection is always honoured.
    if _tts_provider() == "edge" and not _PROVIDER_OVERRIDE.get():
        if _voice_requires_omnivoice(voice, voice_overrides):
            logger.info("Voice '%s' requires OmniVoice; auto-promoting from Edge TTS provider.", voice)
            with tts_provider_override("omnivoice"):
                return generate_tts(
                    text_or_subtitle,
                    voice,
                    target_language,
                    voice_rate,
                    speech_rate,
                    voice_overrides,
                    speaker_voice_map,
                    diarization_job_id,
                )
    # Prefer local GPU OmniVoice, then a configured Colab runtime, and finally the
    # always-available default voice. When no OmniVoice runtime is reachable, run
    # the whole job on Edge so general users still get a dub instead of a failure.
    if (
        _tts_provider() == "omnivoice"
        and _tts_fallback_to_edge_enabled()
        and resolve_effective_tts_runtime() == RUNTIME_EDGE
    ):
        logger.warning("No OmniVoice runtime is available; using the default voice for this job.")
        with tts_provider_override("edge"):
            return generate_tts(
                text_or_subtitle,
                voice,
                target_language,
                voice_rate,
                speech_rate,
                voice_overrides,
                speaker_voice_map,
                diarization_job_id,
            )
    if speaker_voice_map and diarization_job_id:
        output = root / "audio" / f"{text_or_subtitle.stem}.multivoice.mp3"
        generated = generate_multivoice_cue_outputs(
            text_or_subtitle,
            target_language,
            speaker_voice_map,
            diarization_job_id,
            speech_rate=speech_rate,
            voice_overrides=voice_overrides,
        )
        segments = [
            TimedAudioSegment(path=item["audio_path"], start=float(item["start"]), end=float(item["end"]))
            for item in generated
        ]
        duration = max((float(item["end"]) for item in generated), default=0.0)
        workdir = root / "audio" / f"{text_or_subtitle.stem}.multivoice"
        _mix_timed_audio(segments, output, workdir / "timeline_filter.txt", duration)
        output.with_suffix(".chunks.json").write_text(
            json.dumps(
                {
                    "strategy": "speaker_diarization_multivoice",
                    "job_id": diarization_job_id,
                    "chunks": [
                        {key: value for key, value in item.items() if key != "audio_path"}
                        | {"audio": str(item["audio_path"])}
                        for item in generated
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return output

    selected_voice = _resolve_voice(voice, target_language)
    selected_rate = _normalize_rate(voice_rate)
    if _tts_provider() != "omnivoice":
        selected_rate = _combine_edge_rate_with_speech_rate(selected_rate, speech_rate)

    cues = _parse_srt_cues(text_or_subtitle)
    if cues:
        return _generate_aligned_tts(
            cues,
            text_or_subtitle,
            selected_voice,
            selected_rate,
            target_language,
            speech_rate,
            voice_overrides,
        )

    text = subtitle_to_plain_text(text_or_subtitle)
    if not text:
        raise ValueError("No subtitle text available for TTS generation.")

    output = root / "audio" / f"{text_or_subtitle.stem}.mp3"
    try:
        if _tts_provider() == "vieneu":
            synthesize_vieneu(text, selected_voice, output)
        else:
            asyncio.run(_save_edge_tts(text, selected_voice, output, selected_rate))
    except Exception as exc:
        if os.getenv("AETHER_ALLOW_SYNTHETIC_AUDIO") == "1":
            return _create_synthetic_audio(output)
        raise RuntimeError(f"TTS generation failed for voice '{selected_voice}' at rate '{selected_rate}': {exc}") from exc

    if output.stat().st_size == 0:
        raise RuntimeError("TTS provider returned an empty audio file.")
    return output


def generate_multivoice_cue_outputs(
    subtitle_path: Path,
    target_language: str,
    speaker_voice_map: dict[str, dict[str, str]],
    diarization_job_id: str,
    *,
    speech_rate: SpeechRateProfile | None = None,
    voice_overrides: VoiceOverrides | None = None,
    output_dir: Path | None = None,
) -> list[dict[str, Any]]:
    segments_path = subtitle_path.with_suffix(".translated-segments.json")
    if not segments_path.exists():
        raise RuntimeError("Translated speaker segments are missing for multi-voice TTS.")
    payload = json.loads(segments_path.read_text(encoding="utf-8"))
    raw_segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(raw_segments, list) or not raw_segments:
        raise RuntimeError("Translated speaker segments are empty.")

    root = ensure_storage()
    workdir = output_dir or root / "audio" / f"{subtitle_path.stem}.multivoice"
    workdir.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, Any]] = []
    provider = _tts_provider()
    for index, segment in enumerate(raw_segments, start=1):
        speaker_id = str(segment.get("speaker_id") or "")
        assignment = speaker_voice_map.get(speaker_id)
        if not assignment:
            raise RuntimeError(f"No voice mapping exists for speaker {speaker_id or 'unknown'}.")
        voice = _resolve_voice(str(assignment.get("voice") or ""), target_language)
        rate = _normalize_rate(str(assignment.get("voice_rate") or "+0%"))
        if provider != "omnivoice":
            rate = _combine_edge_rate_with_speech_rate(rate, speech_rate)
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        text = str(segment.get("translated_text") or "").strip()
        if not text or end <= start:
            continue
        suffix = ".wav" if provider == "omnivoice" else ".mp3"
        audio_path = workdir / f"{index:04d}_{speaker_id or 'speaker'}{suffix}"
        _save_tts_segment(
            text,
            voice,
            audio_path,
            rate,
            workdir,
            f"{index:04d}_{speaker_id or 'speaker'}",
            max(0.25, end - start),
            voice_overrides,
        )
        duration = max(0.05, _probe_duration(audio_path) or (end - start))
        outputs.append(
            {
                "id": f"cue-{index:04d}",
                "index": index,
                "start": round(start, 3),
                "end": round(start + duration, 3),
                "suggested_end": round(end, 3),
                "duration": round(duration, 3),
                "target_duration": round(max(0.25, end - start), 3),
                "text": text,
                "speaker_id": speaker_id,
                "voice": voice,
                "voice_rate": rate,
                "audio_path": audio_path,
                "diarization_job_id": diarization_job_id,
            }
        )
    if not outputs:
        raise RuntimeError("No multi-voice TTS segments were generated.")
    return outputs


def _generate_aligned_tts(
    cues: list[SubtitleCue],
    subtitle_path: Path,
    voice: str,
    rate: str,
    target_language: str,
    speech_rate: SpeechRateProfile | None = None,
    voice_overrides: VoiceOverrides | None = None,
) -> Path:
    root = ensure_storage()
    output = root / "audio" / f"{subtitle_path.stem}.aligned.mp3"
    if os.getenv("AETHER_ALLOW_SYNTHETIC_AUDIO") == "1":
        return _create_synthetic_audio(output)

    return _generate_chunked_tts(cues, subtitle_path, voice, rate, target_language, speech_rate, voice_overrides)


def _generate_chunked_tts(
    cues: list[SubtitleCue],
    subtitle_path: Path,
    voice: str,
    rate: str,
    target_language: str,
    speech_rate: SpeechRateProfile | None = None,
    voice_overrides: VoiceOverrides | None = None,
) -> Path:
    root = ensure_storage()

    if _should_send_srt_to_omnivoice():
        try:
            return _generate_omnivoice_srt_tts(subtitle_path, voice, rate, speech_rate, voice_overrides)
        except Exception as exc:
            if not _tts_fallback_to_edge_enabled():
                raise
            logger.warning(
                "OmniVoice SRT endpoint failed (%s); falling back to the default voice.",
                exc,
            )
            with tts_provider_override("edge"):
                return _generate_chunked_tts(
                    cues,
                    subtitle_path,
                    _edge_fallback_voice(voice),
                    rate,
                    target_language,
                    speech_rate,
                    voice_overrides,
                )

    output = root / "audio" / f"{subtitle_path.stem}.aligned.mp3"
    workdir = root / "audio" / f"{subtitle_path.stem}.chunks"
    workdir.mkdir(parents=True, exist_ok=True)

    semantic_cues = _semantic_sentence_cues(_ordered_cues(cues))

    if _tts_provider() == "vieneu":
        return _generate_vieneu_timeline_tts(
            semantic_cues, voice, workdir, output, speech_rate, voice_overrides
        )

    if _tts_provider() == "edge" and _edge_timeline_fit_enabled():
        return _generate_edge_timeline_tts(
            semantic_cues, voice, rate, workdir, output, speech_rate, voice_overrides
        )

    chunks = _chunk_cues_for_tts(semantic_cues)
    timed_segments: list[TimedAudioSegment] = []

    for index, chunk in enumerate(chunks, start=1):
        raw_segment = workdir / f"{index:04d}_chunk_raw.mp3"
        target_duration = max(0.25, chunk.end - chunk.start)
        try:
            _save_tts_segment(
                chunk.text,
                voice,
                raw_segment,
                rate,
                workdir,
                f"{index:04d}_chunk",
                target_duration,
                voice_overrides,
            )
        except Exception as exc:
            raise RuntimeError(f"TTS generation failed for voice '{voice}' on chunk {index}: {exc}") from exc
        timed_segments.append(TimedAudioSegment(path=raw_segment, start=chunk.start, end=chunk.end))

    timeline_duration = max((segment.end for segment in timed_segments), default=0.0)
    _mix_timed_audio(timed_segments, output, workdir / "timeline_filter.txt", timeline_duration)
    _write_chunk_manifest(output, chunks, timed_segments, speech_rate)

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("TTS provider returned an empty chunked audio file.")
    return output


def _edge_timeline_fit_enabled() -> bool:
    return _env_bool("AETHER_EDGE_TIMELINE_FIT", True)


def _alignment_overrun_threshold() -> float:
    return _env_float("AETHER_ALIGNMENT_REVIEW_OVERRUN_SECONDS", 0.35, minimum=0.0, maximum=10.0)


def _flag_cue_overrun(
    workdir: Path,
    label: str,
    cue: SubtitleCue,
    fitted_duration: float,
    next_start: float,
) -> None:
    """Record cues whose speech spills into the next cue after tempo fitting.

    Detection only: the audio is left untouched. Tempo compression is capped
    (AETHER_EDGE_MAX_TEMPO), so a long line can still overrun the following cue.
    Overlap is measured against the real next-cue start (not the floored fit
    target), and flagged cues are written to a dedicated debug file so reviewers
    can see exactly which cues overlap and by how much.
    """
    if next_start == float("inf"):
        return
    overrun = cue.start + fitted_duration - next_start
    if overrun <= _alignment_overrun_threshold():
        return
    flag = workdir / "overrun_review_flags.txt"
    line = (
        f"{label}: overrun={overrun:.2f}s "
        f"(start={cue.start:.2f}s, fitted={fitted_duration:.2f}s, "
        f"next_cue={next_start:.2f}s, gap={next_start - cue.start:.2f}s); "
        f"text={cue.text}\n"
    )
    with flag.open("a", encoding="utf-8") as file:
        file.write(line)


def _generate_edge_timeline_tts(
    semantic_cues: list[SubtitleCue],
    voice: str,
    rate: str,
    workdir: Path,
    output: Path,
    speech_rate: SpeechRateProfile | None,
    voice_overrides: VoiceOverrides | None,
) -> Path:
    """Synthesize Edge TTS units in parallel and fit each to the timeline.

    All units are synthesized concurrently in a single asyncio event loop
    (bounded by AETHER_EDGE_CONCURRENCY, default 6), which cuts wall-clock
    synthesis time from O(n × latency) down to O(n/concurrency × latency).
    Tempo-fitting (ffmpeg) and the final mix run sequentially after synthesis.
    """
    units = _edge_timeline_units(semantic_cues)
    if not units:
        raise RuntimeError("No subtitle cues available for Edge timeline TTS.")

    min_gap = _env_float("AETHER_EDGE_MIN_GAP_SECONDS", 0.08, minimum=0.0, maximum=2.0)
    max_concurrent = _env_int("AETHER_EDGE_CONCURRENCY", 6, minimum=1, maximum=32)

    raw_paths = asyncio.run(
        _synthesize_edge_units_async(units, voice, rate, workdir, max_concurrent)
    )

    timed_segments: list[TimedAudioSegment] = []
    manifest_chunks: list[TtsChunk] = []

    for index, (unit, raw_segment) in enumerate(zip(units, raw_paths)):
        next_start = units[index + 1].start if index + 1 < len(units) else float("inf")
        max_duration = (
            max(0.3, next_start - unit.start - min_gap)
            if next_start != float("inf")
            else float("inf")
        )

        fitted_path, fitted_duration = _fit_edge_audio_to_slot(
            raw_segment,
            workdir / f"{index + 1:04d}_edge_fit.mp3",
            max_duration,
        )
        end = unit.start + fitted_duration
        _flag_cue_overrun(workdir, f"{index + 1:04d}_edge", unit, fitted_duration, next_start)
        timed_segments.append(TimedAudioSegment(path=fitted_path, start=unit.start, end=end))
        manifest_chunks.append(TtsChunk(start=unit.start, end=end, cues=[unit]))

    timeline_duration = max((segment.end for segment in timed_segments), default=0.0)
    _mix_timed_audio(timed_segments, output, workdir / "timeline_filter.txt", timeline_duration)
    _write_chunk_manifest(output, manifest_chunks, timed_segments, speech_rate)

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("TTS provider returned an empty Edge timeline audio file.")
    return output


def _generate_vieneu_timeline_tts(
    semantic_cues: list[SubtitleCue],
    voice: str,
    workdir: Path,
    output: Path,
    speech_rate: SpeechRateProfile | None,
    voice_overrides: VoiceOverrides | None,
) -> Path:
    """Synthesize VieNeu units (CPU, sequential) and fit each to the SRT timeline.

    Mirrors the Edge timeline path so VieNeu dubs stay in sync: each unit is voiced,
    then time-compressed only if it would overrun the next cue, and overlaid on the
    original timestamps. VieNeu runs locally on CPU, so units are produced one at a
    time rather than via the async fan-out used for the Edge cloud service.
    """
    units = _edge_timeline_units(semantic_cues)
    if not units:
        raise RuntimeError("No subtitle cues available for VieNeu timeline TTS.")

    min_gap = _env_float("AETHER_EDGE_MIN_GAP_SECONDS", 0.08, minimum=0.0, maximum=2.0)
    timed_segments: list[TimedAudioSegment] = []
    manifest_chunks: list[TtsChunk] = []

    for index, unit in enumerate(units):
        raw_segment = workdir / f"{index + 1:04d}_vieneu_raw.wav"
        cleaned = _sanitize_tts_text(unit.text)
        target_duration = max(0.25, unit.end - unit.start)
        if not _has_speakable_content(cleaned):
            _create_silence(raw_segment, target_duration)
        else:
            try:
                synthesize_vieneu(cleaned, voice, raw_segment)
            except Exception as exc:
                raise RuntimeError(
                    f"VieNeu synthesis failed for voice '{voice}' on cue {index + 1}: {exc}"
                ) from exc

        next_start = units[index + 1].start if index + 1 < len(units) else float("inf")
        max_duration = (
            max(0.3, next_start - unit.start - min_gap)
            if next_start != float("inf")
            else float("inf")
        )

        fitted_path, fitted_duration = _fit_edge_audio_to_slot(
            raw_segment,
            workdir / f"{index + 1:04d}_vieneu_fit.mp3",
            max_duration,
        )
        end = unit.start + fitted_duration
        _flag_cue_overrun(workdir, f"{index + 1:04d}_vieneu", unit, fitted_duration, next_start)
        timed_segments.append(TimedAudioSegment(path=fitted_path, start=unit.start, end=end))
        manifest_chunks.append(TtsChunk(start=unit.start, end=end, cues=[unit]))

    timeline_duration = max((segment.end for segment in timed_segments), default=0.0)
    _mix_timed_audio(timed_segments, output, workdir / "timeline_filter.txt", timeline_duration)
    _write_chunk_manifest(output, manifest_chunks, timed_segments, speech_rate)

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("VieNeu returned an empty timeline audio file.")
    return output


async def _synthesize_edge_units_async(
    units: list[SubtitleCue],
    voice: str,
    rate: str,
    workdir: Path,
    max_concurrent: int,
) -> list[Path]:
    """Synthesize all Edge TTS units concurrently and return their raw audio paths."""
    import edge_tts

    semaphore = asyncio.Semaphore(max_concurrent)

    async def synth_one(unit: SubtitleCue, position: int) -> Path:
        raw = workdir / f"{position:04d}_edge_raw.mp3"
        cleaned = _sanitize_tts_text(unit.text)
        target_duration = max(0.25, unit.end - unit.start)
        silence_secs = target_duration

        if not _has_speakable_content(cleaned):
            _create_silence(raw, silence_secs)
            return raw

        last_exc: Exception | None = None
        async with semaphore:
            for attempt in range(3):
                try:
                    communicate = edge_tts.Communicate(cleaned, voice=voice, rate=rate)
                    await communicate.save(str(raw))
                    if raw.exists() and raw.stat().st_size > 0:
                        return raw
                    raise RuntimeError("No audio was received.")
                except Exception as exc:
                    last_exc = exc
                    raw.unlink(missing_ok=True)
                    if attempt < 2:
                        await asyncio.sleep(0.8 * (attempt + 1))

        if _is_edge_no_audio_error(last_exc):
            logger.warning(
                "Edge TTS returned no audio for cue %d (text: %r); substituting silence.",
                position,
                cleaned[:80],
            )
            _create_silence(raw, silence_secs)
            return raw
        raise RuntimeError(
            f"TTS generation failed for voice '{voice}' on cue {position}: {last_exc}"
        ) from last_exc

    return list(
        await asyncio.gather(*[synth_one(unit, i + 1) for i, unit in enumerate(units)])
    )


def _edge_timeline_units(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    """Group sentence cues into compact, timeline-anchored units for Edge synthesis."""
    cues = [cue for cue in _ordered_cues(cues) if cue.text and cue.end > cue.start]
    if not cues:
        return []

    max_duration = _env_float("AETHER_EDGE_UNIT_MAX_SECONDS", 10.0, minimum=2.0, maximum=60.0)
    max_gap = _env_float("AETHER_EDGE_UNIT_MAX_GAP", 0.4, minimum=0.0, maximum=5.0)
    max_chars = _env_int("AETHER_EDGE_UNIT_MAX_CHARS", 280, minimum=80, maximum=4000)

    units: list[SubtitleCue] = []
    group: list[SubtitleCue] = [cues[0]]
    for cue in cues[1:]:
        gap = cue.start - group[-1].end
        projected_duration = cue.end - group[0].start
        projected_text = _dedupe_joined_text([item.text for item in group] + [cue.text])
        if gap <= max_gap and projected_duration <= max_duration and len(projected_text) <= max_chars:
            group.append(cue)
            continue
        units.append(_merge_edge_unit(group))
        group = [cue]
    units.append(_merge_edge_unit(group))
    return units


def _merge_edge_unit(group: list[SubtitleCue]) -> SubtitleCue:
    return SubtitleCue(
        start=group[0].start,
        end=group[-1].end,
        text=_dedupe_joined_text([cue.text for cue in group]),
    )


def _fit_edge_audio_to_slot(source: Path, dest: Path, max_duration: float) -> tuple[Path, float]:
    """Time-compress an Edge segment only when it would overrun the next cue.

    Returns the audio path to place on the timeline and its (possibly fitted) duration.
    Speech that already ends before the next cue keeps Edge's natural speed; only
    overflowing speech is sped up (capped) so words are preserved instead of cut.
    """
    measured = _probe_duration(source)
    if measured <= 0:
        return source, 0.05
    if measured <= max_duration:
        return source, measured

    max_tempo = _env_float("AETHER_EDGE_MAX_TEMPO", 1.6, minimum=1.0, maximum=3.0)
    tempo = min(max_tempo, measured / max(0.05, max_duration))
    if tempo <= 1.01:
        return source, measured

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-filter:a",
        _atempo_filter(tempo),
        "-c:a",
        "libmp3lame",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        # Compression failed — keep the natural segment rather than dropping audio.
        return source, measured
    fitted = _probe_duration(dest)
    if fitted <= 0:
        fitted = measured / tempo
    return dest, fitted


def _should_send_srt_to_omnivoice() -> bool:
    if _tts_provider() != "omnivoice":
        return False
    if not _env_bool("AETHER_OMNIVOICE_USE_SRT_ENDPOINT", True):
        return False

    settings = get_runtime_settings()
    base_url = settings.omnivoice_api_url.strip().rstrip("/")
    if not base_url:
        return False
    return _omnivoice_transport(base_url) == "fastapi"


def _generate_omnivoice_srt_tts(
    subtitle_path: Path,
    voice: str,
    rate: str,
    speech_rate: SpeechRateProfile | None = None,
    voice_overrides: VoiceOverrides | None = None,
) -> Path:
    root = ensure_storage()
    output = root / "audio" / f"{subtitle_path.stem}.omnivoice_srt.wav"
    settings = get_runtime_settings()
    voice_option = _find_voice_option(voice)
    base_url = _resolve_omnivoice_base_url(settings)
    if not base_url:
        raise RuntimeError("OMNIVOICE_API_URL is required when AETHER_TTS_PROVIDER=omnivoice.")

    mode = _voice_mode(settings, voice_option, voice_overrides)
    if voice_option and not mode and (voice_option.reference_audio_url or voice_option.reference_audio_path):
        mode = "clone"
    if mode not in {"auto", "design", "clone"}:
        mode = "clone"

    srt_timeline_mode = os.getenv("AETHER_OMNIVOICE_SRT_TIMELINE_MODE", "cue").strip().lower() or "cue"
    payload: dict[str, object] = {
        "srt_text": subtitle_path.read_text(encoding="utf-8", errors="ignore"),
        "mode": mode,
        "timeline_mode": srt_timeline_mode,
        "output_format": "wav",
        "chunk_min_seconds": _env_float("AETHER_TTS_CHUNK_MIN_SECONDS", 30.0, minimum=5.0, maximum=120.0),
        "chunk_max_seconds": _env_float("AETHER_TTS_CHUNK_MAX_SECONDS", 60.0, minimum=10.0, maximum=300.0),
        "chunk_max_chars": _env_int("AETHER_TTS_CHUNK_MAX_CHARS", 6500, minimum=500, maximum=30000),
    }
    speed = _omnivoice_speed(rate)
    if speed != 1.0:
        payload["speed"] = speed

    instruction = _voice_instruction(settings, voice_option, voice, mode, voice_overrides)
    if instruction:
        payload["instruct"] = instruction
    ref_audio_source = ""
    if mode == "clone":
        ref_audio_payload, ref_audio_source = _omnivoice_reference_audio_payload(
            settings,
            voice_option,
            voice_overrides,
        )
        payload.update(ref_audio_payload)

        ref_text = _voice_reference_text(settings, voice_option, voice_overrides)
        if not ref_text:
            ref_text_path = voice_option.reference_text_path if voice_option and voice_option.reference_text_path else settings.omnivoice_ref_text_path
            ref_text = _read_optional_text(ref_text_path)
        if ref_text:
            payload["ref_text"] = ref_text

    headers = {}
    api_key = settings.omnivoice_api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = _env_int("AETHER_OMNIVOICE_SRT_TIMEOUT_SECONDS", 1800, minimum=120, maximum=14400)
    if _env_bool("AETHER_OMNIVOICE_SRT_ASYNC", True):
        _download_omnivoice_srt_job(base_url, payload, headers, output, timeout)
    else:
        _download_omnivoice_srt_direct(base_url, payload, headers, output, timeout)

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("OmniVoice SRT endpoint returned an empty audio file.")

    manifest = {
        "strategy": "remote_omnivoice_srt_endpoint",
        "endpoint": "/synthesize/srt/jobs" if _env_bool("AETHER_OMNIVOICE_SRT_ASYNC", True) else "/synthesize/srt/file",
        "source_srt": str(subtitle_path),
        "audio": str(output),
        "voice": voice,
        "voice_option_id": voice_option.id if voice_option else "",
        "mode": mode,
        "ref_audio_source": ref_audio_source,
        "rate": rate,
        "speed": speed,
        "speech_rate": _speech_rate_manifest(speech_rate),
        "instruction_configured": bool(instruction),
    }
    output.with_suffix(".srt-tts.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def generate_omnivoice_srt_cues(
    subtitle_path: Path,
    voice: str,
    rate: str,
    cue_dir: Path,
    speech_rate: SpeechRateProfile | None = None,
    voice_overrides: VoiceOverrides | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    settings = get_runtime_settings()
    voice_option = _find_voice_option(voice)
    base_url = _resolve_omnivoice_base_url(settings)
    if not base_url:
        raise RuntimeError("OMNIVOICE_API_URL is required when AETHER_TTS_PROVIDER=omnivoice.")
    if _omnivoice_transport(base_url) != "fastapi":
        raise RuntimeError("OmniVoice cue-list synthesis requires the FastAPI OmniVoice service.")

    mode = _voice_mode(settings, voice_option, voice_overrides)
    if voice_option and not mode and (voice_option.reference_audio_url or voice_option.reference_audio_path):
        mode = "clone"
    if mode not in {"auto", "design", "clone"}:
        mode = "clone"

    cue_timeline_mode = os.getenv("AETHER_OMNIVOICE_CUE_TIMELINE_MODE", "cue").strip().lower() or "cue"
    payload: dict[str, object] = {
        "srt_text": subtitle_path.read_text(encoding="utf-8", errors="ignore"),
        "mode": mode,
        "timeline_mode": cue_timeline_mode,
        "output_format": "wav",
        "chunk_min_seconds": _env_float("AETHER_TTS_CHUNK_MIN_SECONDS", 30.0, minimum=5.0, maximum=120.0),
        "chunk_max_seconds": _env_float("AETHER_TTS_CHUNK_MAX_SECONDS", 60.0, minimum=10.0, maximum=300.0),
        "chunk_max_chars": _env_int("AETHER_TTS_CHUNK_MAX_CHARS", 6500, minimum=500, maximum=30000),
    }
    speed = _omnivoice_speed(rate)
    if speed != 1.0:
        payload["speed"] = speed

    instruction = _voice_instruction(settings, voice_option, voice, mode, voice_overrides)
    if instruction:
        payload["instruct"] = instruction
    ref_audio_source = ""
    if mode == "clone":
        ref_audio_payload, ref_audio_source = _omnivoice_reference_audio_payload(
            settings,
            voice_option,
            voice_overrides,
        )
        payload.update(ref_audio_payload)

        ref_text = _voice_reference_text(settings, voice_option, voice_overrides)
        if not ref_text:
            ref_text_path = voice_option.reference_text_path if voice_option and voice_option.reference_text_path else settings.omnivoice_ref_text_path
            ref_text = _read_optional_text(ref_text_path)
        if ref_text:
            payload["ref_text"] = ref_text

    headers = {}
    api_key = settings.omnivoice_api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = _env_int("AETHER_OMNIVOICE_CUE_TIMEOUT_SECONDS", 1800, minimum=120, maximum=14400)
    use_async = _env_bool("AETHER_OMNIVOICE_CUES_ASYNC", True)
    if use_async:
        data, endpoint = _request_omnivoice_srt_cues_async(base_url, payload, headers, timeout)
    else:
        data, endpoint = _request_omnivoice_srt_cues_sync(base_url, payload, headers, timeout)

    cue_dir.mkdir(parents=True, exist_ok=True)
    cues: list[dict[str, object]] = []
    for raw_cue in data.get("cues", []):
        cue_id = str(raw_cue.get("id") or f"cue-{len(cues) + 1:04d}")
        source_url = str(raw_cue.get("audio_url") or "")
        suffix = Path(str(raw_cue.get("filename") or "")).suffix or ".wav"
        local_path = cue_dir / f"{cue_id}{suffix}"
        audio_response = _omnivoice_request(
            "get",
            source_url,
            operation=f"SRT cue audio download {cue_id}",
            timeout=300,
        )
        if audio_response.status_code >= 400:
            raise RuntimeError(f"OmniVoice cue audio download failed for {cue_id}: {audio_response.text[:700]}")
        local_path.write_bytes(audio_response.content)
        if not local_path.exists() or local_path.stat().st_size == 0:
            raise RuntimeError(f"OmniVoice returned an empty cue audio file for {cue_id}.")

        cues.append(
            {
                "id": cue_id,
                "index": int(raw_cue.get("index") or len(cues) + 1),
                "start": float(raw_cue.get("start") or 0.0),
                "end": float(raw_cue.get("end") or 0.0),
                "duration_seconds": float(raw_cue.get("duration_seconds") or _probe_duration(local_path) or 0.0),
                "target_duration_seconds": float(raw_cue.get("target_duration_seconds") or 0.0),
                "audio_path": local_path,
                "text": str(raw_cue.get("text") or ""),
            }
        )

    metadata = {
        "strategy": "remote_omnivoice_srt_cue_endpoint",
        "endpoint": endpoint,
        "timeline_mode": data.get("timeline_mode") or payload["timeline_mode"],
        "mode": mode,
        "ref_audio_source": ref_audio_source,
        "speed": speed,
        "speech_rate": _speech_rate_manifest(speech_rate),
        "instruction_configured": bool(instruction),
    }
    return cues, metadata


def _request_omnivoice_srt_cues_async(
    base_url: str,
    payload: dict[str, object],
    headers: dict[str, str],
    timeout: int,
) -> tuple[dict[str, Any], str]:
    start_response = _omnivoice_request(
        "post",
        f"{base_url}/synthesize/srt/cues/jobs",
        json=payload,
        headers=headers,
        timeout=120,
        operation="SRT cue-list job start",
    )
    if start_response.status_code == 404:
        return _request_omnivoice_srt_cues_sync(base_url, payload, headers, timeout)
    if start_response.status_code >= 400:
        raise RuntimeError(f"OmniVoice SRT cue-list job start failed: {start_response.text[:700]}")

    job = start_response.json()
    status_url = str(job.get("status_url") or f"{base_url}/synthesize/srt/cues/jobs/{job.get('job_id')}")
    result_url = str(job.get("result_url") or f"{status_url}/result")
    deadline = time.monotonic() + timeout
    interval = _env_float("AETHER_OMNIVOICE_CUE_POLL_SECONDS", 5.0, minimum=1.0, maximum=60.0)

    while time.monotonic() < deadline:
        status_response = _omnivoice_request(
            "get",
            status_url,
            headers=headers,
            timeout=120,
            operation="SRT cue-list job status",
        )
        if status_response.status_code >= 400:
            raise RuntimeError(f"OmniVoice SRT cue-list job status failed: {status_response.text[:700]}")
        status_data = status_response.json()
        status = str(status_data.get("status") or "").lower()
        if status == "completed":
            embedded = status_data.get("cues")
            if isinstance(embedded, dict) and isinstance(embedded.get("cues"), list):
                return embedded, "/synthesize/srt/cues/jobs"
            result_response = _omnivoice_request(
                "get",
                result_url,
                headers=headers,
                timeout=300,
                operation="SRT cue-list job result",
            )
            if result_response.status_code >= 400:
                raise RuntimeError(f"OmniVoice SRT cue-list job result failed: {result_response.text[:700]}")
            return result_response.json(), "/synthesize/srt/cues/jobs"
        if status == "failed":
            raise RuntimeError(f"OmniVoice SRT cue-list job failed: {status_data.get('error') or 'unknown error'}")
        time.sleep(interval)

    raise RuntimeError(f"OmniVoice SRT cue-list job timed out after {timeout} seconds.")


def _request_omnivoice_srt_cues_sync(
    base_url: str,
    payload: dict[str, object],
    headers: dict[str, str],
    timeout: int,
) -> tuple[dict[str, Any], str]:
    response = _omnivoice_request(
        "post",
        f"{base_url}/synthesize/srt/cues",
        json=payload,
        headers=headers,
        timeout=timeout,
        operation="SRT cue-list synthesis",
    )
    if response.status_code == 404:
        raise RuntimeError(
            "OmniVoice service does not expose /synthesize/srt/cues or /synthesize/srt/cues/jobs. "
            "Restart the Colab OmniVoice service from the updated omnivoice_service.py."
        )
    if response.status_code >= 400:
        raise RuntimeError(f"OmniVoice SRT cue-list request failed: {response.text[:700]}")
    return response.json(), "/synthesize/srt/cues"


def _download_omnivoice_srt_direct(
    base_url: str,
    payload: dict[str, object],
    headers: dict[str, str],
    output: Path,
    timeout: int,
) -> None:
    response = _omnivoice_request(
        "post",
        f"{base_url}/synthesize/srt/file",
        json=payload,
        headers=headers,
        timeout=timeout,
        operation="direct SRT synthesis",
    )
    if response.status_code >= 400:
        if response.status_code == 524 or "524: A timeout occurred" in response.text:
            raise RuntimeError(
                "OmniVoice direct SRT request timed out behind Cloudflare Tunnel. "
                "Restart the Colab OmniVoice service with the async job endpoints and keep "
                "AETHER_OMNIVOICE_SRT_ASYNC=1, or use a non-Cloudflare URL."
            )
        raise RuntimeError(f"OmniVoice SRT request failed: {_omnivoice_error_detail(response)}")
    output.write_bytes(response.content)


def _response_is_stale_tunnel(response: requests.Response) -> bool:
    """True when the response indicates the tunnel for this URL is temporarily gone.

    Covers both providers: a Cloudflare 530 / HTML error page (the watchdog rotated
    cloudflared to a fresh URL) and an ngrok edge error page (the Colab agent
    dropped or is mid-reconnect). In both cases the OmniVoice service inside the VM
    keeps running, so the cure is to re-resolve to the current tunnel URL and keep
    polling the same job — not to fail the job.
    """
    if response.status_code == 530:
        return True
    return _is_tunnel_error_response(
        response.text, response.headers.get("content-type", "")
    )


def _refresh_omnivoice_base_url(current: str) -> str:
    """Re-resolve the active OmniVoice tunnel URL after a stale-tunnel error.

    Picks up the fresh URL the Colab watchdog pushed into runtime settings (and
    auto-starts/recovers Colab if needed). Raises if no healthy runtime can be
    obtained, so an unrecoverable session still surfaces as a loud failure.
    """
    refreshed = _resolve_omnivoice_base_url(get_runtime_settings()).rstrip("/")
    if refreshed and refreshed != current:
        logger.warning(
            "OmniVoice tunnel rotated mid-job; resuming on the new URL %s.", refreshed
        )
    return refreshed


def _response_is_job_lost(response: requests.Response) -> bool:
    """True when the service is up but no longer knows this job_id.

    The async SRT job registry lives in OmniVoice's process memory, so a service
    restart (re-bootstrap, VM recycle) drops every job id and polling returns
    HTTP 404 "SRT synthesis job not found". The cure is to recreate the job — not
    to keep polling a dead id (which never completes) nor to fail the whole render.
    Distinguished from the missing-audio 404 ("Generated audio file is missing"),
    which is a genuine job-level failure.
    """
    if response.status_code != 404:
        return False
    lowered = response.text.lower()
    if "job not found" in lowered:
        return True
    try:
        detail = str(response.json().get("detail") or "").lower()
    except ValueError:
        return False
    return "job not found" in detail


def _download_omnivoice_srt_job(
    base_url: str,
    payload: dict[str, object],
    headers: dict[str, str],
    output: Path,
    timeout: int,
) -> None:
    create_timeout = _env_int("AETHER_OMNIVOICE_JOB_CREATE_TIMEOUT_SECONDS", 60, minimum=10, maximum=300)
    max_recreations = _env_int("AETHER_OMNIVOICE_JOB_MAX_RECREATIONS", 3, minimum=0, maximum=10)

    def _create(url: str) -> tuple[str, str]:
        """POST a new SRT job. Returns (url, job_id); empty job_id => direct fallback already wrote output."""
        response = _omnivoice_request(
            "post",
            f"{url}/synthesize/srt/jobs",
            json=payload,
            headers=headers,
            timeout=create_timeout,
            operation="SRT job creation",
        )
        if _response_is_stale_tunnel(response):
            # Tunnel died before the job was even created — re-resolve and recreate
            # against the current URL (no job_id exists yet to preserve).
            url = _refresh_omnivoice_base_url(url)
            response = _omnivoice_request(
                "post",
                f"{url}/synthesize/srt/jobs",
                json=payload,
                headers=headers,
                timeout=create_timeout,
                operation="SRT job creation (after tunnel refresh)",
            )
        if response.status_code == 404:
            if _env_bool("AETHER_OMNIVOICE_ALLOW_DIRECT_FALLBACK", False) and not _is_cloudflare_tunnel_url(url):
                _download_omnivoice_srt_direct(url, payload, headers, output, timeout)
                return url, ""
            raise RuntimeError(
                "OmniVoice service does not expose /synthesize/srt/jobs. "
                "Restart the Colab OmniVoice service from the updated omnivoice_service.py. "
                "Direct /synthesize/srt/file is disabled by default because Cloudflare Tunnel returns 524 for long requests."
            )
        if response.status_code >= 400:
            raise RuntimeError(f"OmniVoice SRT job creation failed: {_omnivoice_error_detail(response)}")
        job_id = str(response.json().get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError("OmniVoice SRT job endpoint did not return a job_id.")
        return url, job_id

    base_url, job_id = _create(base_url)
    if not job_id:
        return  # direct fallback already wrote the output

    report_progress("Đang tổng hợp giọng trên OmniVoice…")
    deadline = time.time() + timeout
    poll_interval = _env_float("AETHER_OMNIVOICE_JOB_POLL_SECONDS", 5.0, minimum=1.0, maximum=60.0)
    recreations = 0
    status_payload: dict[str, object] = {}

    def _recreate() -> bool:
        """Recreate the lost job on the recovered service. Returns False if output is already written."""
        nonlocal base_url, job_id, recreations
        if recreations >= max_recreations:
            raise RuntimeError(
                f"OmniVoice restarted repeatedly and could not retain the SRT job "
                f"(after {recreations} recreate attempts)."
            )
        recreations += 1
        logger.warning(
            "OmniVoice lost the SRT job (service restarted); recreating job (attempt %d/%d).",
            recreations,
            max_recreations,
        )
        report_progress(
            f"OmniVoice vừa khởi động lại — đang tạo lại phiên tổng hợp (lần {recreations}/{max_recreations})…"
        )
        base_url = _refresh_omnivoice_base_url(base_url)
        base_url, job_id = _create(base_url)
        if job_id:
            report_progress("Đang tổng hợp giọng trên OmniVoice…")
        return bool(job_id)

    while time.time() < deadline:
        status_response = _omnivoice_request(
            "get",
            f"{base_url}/synthesize/srt/jobs/{job_id}",
            headers=headers,
            timeout=30,
            operation="SRT job polling",
        )
        if _response_is_stale_tunnel(status_response):
            # The tunnel rotated while the job kept running on the VM. Re-resolve
            # to the new URL and poll the same job_id — do not abandon the job.
            report_progress("Mất kết nối tunnel — đang nối lại OmniVoice…")
            base_url = _refresh_omnivoice_base_url(base_url)
            report_progress("Đang tổng hợp giọng trên OmniVoice…")
            continue
        if _response_is_job_lost(status_response):
            if not _recreate():
                return
            continue
        if status_response.status_code >= 400:
            raise RuntimeError(f"OmniVoice SRT job status failed: {_omnivoice_error_detail(status_response)}")
        status_payload = status_response.json()
        status = str(status_payload.get("status") or "").lower()
        if status == "completed":
            report_progress("Đang tải kết quả giọng…")
            file_response = _omnivoice_request(
                "get",
                f"{base_url}/synthesize/srt/jobs/{job_id}/file",
                headers=headers,
                timeout=300,
                operation="SRT job file download",
            )
            if _response_is_stale_tunnel(file_response):
                base_url = _refresh_omnivoice_base_url(base_url)
                continue
            if _response_is_job_lost(file_response):
                if not _recreate():
                    return
                continue
            if file_response.status_code >= 400:
                raise RuntimeError(
                    f"OmniVoice SRT job file download failed: {_omnivoice_error_detail(file_response)}"
                )
            output.write_bytes(file_response.content)
            return
        if status == "failed":
            raise RuntimeError(f"OmniVoice SRT job failed: {status_payload.get('error') or 'unknown error'}")
        time.sleep(poll_interval)

    raise RuntimeError(f"OmniVoice SRT job timed out after {timeout}s: {status_payload}")


def _omnivoice_request(method: str, url: str, operation: str, **kwargs) -> requests.Response:
    attempts = _env_int("AETHER_OMNIVOICE_HTTP_RETRIES", 4, minimum=1, maximum=12)
    retry_statuses = {408, 429, 500, 502, 503, 504, 520, 522, 523, 524}
    # ngrok-free serves an HTML interstitial to browser-like clients; this header
    # bypasses it so the JSON/audio responses come through unaltered. It is ignored
    # by cloudflared and the local FastAPI service, so it is safe to always send.
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("ngrok-skip-browser-warning", "true")
    kwargs["headers"] = headers
    last_error: Exception | None = None
    response: requests.Response | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code not in retry_statuses:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:180]}")
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ReadTimeout,
            requests.exceptions.Timeout,
        ) as exc:
            last_error = exc

        if attempt < attempts:
            time.sleep(min(20.0, 1.5 * attempt))

    if response is not None:
        return response
    raise RuntimeError(f"OmniVoice {operation} connection failed after {attempts} attempts: {last_error}") from last_error


def _is_cloudflare_tunnel_url(base_url: str) -> bool:
    lowered = base_url.lower()
    return "trycloudflare.com" in lowered or "cloudflare" in lowered


def _colab_recovery_detail(state: str) -> str:
    """Friendly live line for each Colab recovery state, so the UI shows that the
    runtime is reconnecting/restarting rather than actually synthesising voice."""
    return {
        "starting": "Đang khởi động phiên Colab…",
        "installing": "Đang cài đặt OmniVoice trên Colab…",
        "loading": "Đang tải model OmniVoice lên GPU…",
        "waiting_for_gpu": "Đang chờ Google cấp GPU…",
        "waiting_for_login": "Cần đăng nhập Google lại trong Cấu hình…",
        "disconnected": "Mất kết nối Colab — đang nối lại…",
    }.get(state, "Đang khôi phục OmniVoice trên Colab…")


def _resolve_omnivoice_base_url(settings) -> str:
    base_url = settings.omnivoice_api_url.strip().rstrip("/")
    if not base_url:
        raise RuntimeError("OMNIVOICE_API_URL is required when AETHER_TTS_PROVIDER=omnivoice.")
    if settings.omnivoice_runtime != "colab":
        return base_url
    if _omnivoice_health_ready(base_url):
        return base_url

    from .omnivoice_colab_runtime import get_colab_runtime_status, start_colab_runtime

    report_progress("Mất kết nối OmniVoice — đang khôi phục phiên Colab…")
    start_colab_runtime()
    timeout = _env_int("AETHER_COLAB_RECOVERY_TIMEOUT_SECONDS", 900, minimum=60, maximum=3600)
    deadline = time.time() + timeout
    last_state = ""
    last_error = ""
    while time.time() < deadline:
        status = get_colab_runtime_status()
        last_state = str(status.get("state") or "")
        last_error = str(status.get("error") or "")
        candidate = str(status.get("api_url") or "").strip().rstrip("/")
        report_progress(_colab_recovery_detail(last_state))
        if status.get("reachable") and candidate:
            return candidate
        if last_state == "waiting_for_login":
            raise RuntimeError(
                "The Colab session expired and Google authorization is required. "
                "Open Runtime Settings, complete Google login, then use Continue on this job."
            )
        if last_state in {"setup_required", "error"}:
            raise RuntimeError(
                f"Colab OmniVoice recovery failed ({last_state}): "
                f"{last_error or 'open Runtime Settings for details'}"
            )
        time.sleep(3)

    raise RuntimeError(
        f"Colab OmniVoice did not become ready within {timeout} seconds "
        f"(last state: {last_state or 'unknown'}). Open Runtime Settings and retry the job."
    )


def _omnivoice_health_ready(base_url: str) -> bool:
    try:
        response = requests.get(
            f"{base_url}/health",
            timeout=6,
            headers={"ngrok-skip-browser-warning": "true"},
        )
        if response.status_code >= 400:
            return False
        payload = response.json()
        return str(payload.get("status") or "").lower() in {"ready", "loading"}
    except (requests.RequestException, ValueError):
        return False


def _omnivoice_error_detail(response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    text = response.text
    if _is_cloudflare_error_response(text, content_type):
        return (
            "the Cloudflare tunnel is no longer available. "
            "Aether will request a fresh Colab runtime on Continue/Retry."
        )
    if _is_ngrok_error_response(text, content_type):
        return (
            "the ngrok tunnel is temporarily offline (the Colab agent "
            "disconnected). Aether will retry on the current URL automatically."
        )
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error")
            if detail:
                return str(detail)[:700]
    except ValueError:
        pass
    return re.sub(r"\s+", " ", text).strip()[:700] or f"HTTP {response.status_code}"


def _is_cloudflare_error_response(text: str, content_type: str = "") -> bool:
    lowered = text.lower()
    return (
        "cloudflare tunnel error" in lowered
        or ("text/html" in content_type and "cloudflare" in lowered)
        or "trycloudflare.com | cloudflare" in lowered
    )


def _is_ngrok_error_response(text: str, content_type: str = "") -> bool:
    """True when the body is an ngrok edge error page (agent offline / disconnected).

    ngrok serves a branded HTML page (pulling assets from assets.ngrok.com and
    showing an ``ERR_NGROK_xxxx`` code) when the reserved domain has no agent
    connected — e.g. the Colab agent dropped or is mid-reconnect. This is NOT the
    browser-warning interstitial (which the ngrok-skip-browser-warning header
    suppresses); it means the tunnel is temporarily unreachable.
    """
    lowered = text.lower()
    if "err_ngrok" in lowered:
        return True
    return "text/html" in content_type and "assets.ngrok.com" in lowered


def _is_tunnel_error_response(text: str, content_type: str = "") -> bool:
    return _is_cloudflare_error_response(text, content_type) or _is_ngrok_error_response(
        text, content_type
    )


def _write_chunk_manifest(
    output: Path,
    chunks: list[TtsChunk],
    segments: list[TimedAudioSegment],
    speech_rate: SpeechRateProfile | None = None,
) -> None:
    manifest = {
        "strategy": "chunk_tts_target_duration_overlay_srt_timeline",
        "tts_provider": _tts_provider(),
        "chunk_count": len(chunks),
        "speech_rate": _speech_rate_manifest(speech_rate),
        "chunks": [
            {
                "chunk": index,
                "start": chunk.start,
                "end": chunk.end,
                "target_duration": max(0.25, chunk.end - chunk.start),
                "audio": str(segment.path),
                "text": chunk.text,
            }
            for index, (chunk, segment) in enumerate(zip(chunks, segments), start=1)
        ],
    }
    output.with_suffix(".chunks.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _semantic_sentence_cues(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    ordered = [cue for cue in _ordered_cues(cues) if cue.text and cue.end > cue.start]
    if not ordered:
        return []

    max_duration = _env_float("AETHER_TTS_SEMANTIC_MAX_SECONDS", 14.0, minimum=3.0, maximum=60.0)
    pause_threshold = _env_float("AETHER_TTS_SEMANTIC_PAUSE_THRESHOLD", 0.8, minimum=0.0, maximum=10.0)
    semantic: list[SubtitleCue] = []
    pending: list[SubtitleCue] = []

    for index, cue in enumerate(ordered):
        pending.append(cue)
        next_cue = ordered[index + 1] if index + 1 < len(ordered) else None
        joined = _dedupe_joined_text([item.text for item in pending])
        duration = pending[-1].end - pending[0].start
        next_gap = max(0.0, next_cue.start - pending[-1].end) if next_cue else 0.0
        should_close = (
            _ends_complete_sentence(joined)
            or next_cue is None
            or duration >= max_duration
            or (next_gap >= pause_threshold and duration >= 1.0)
        )
        if not should_close:
            continue

        semantic.extend(_split_text_across_timeline(joined, pending[0].start, pending[-1].end))
        pending = []

    if pending:
        joined = _dedupe_joined_text([item.text for item in pending])
        semantic.extend(_split_text_across_timeline(joined, pending[0].start, pending[-1].end))

    return [cue for cue in semantic if cue.text and cue.end > cue.start]


def _split_text_across_timeline(text: str, start: float, end: float) -> list[SubtitleCue]:
    parts = _split_complete_sentences(text)
    if len(parts) <= 1:
        return [SubtitleCue(start=start, end=end, text=_clean_cue_text(text))]

    duration = max(0.05, end - start)
    weights = [max(1.0, _word_count(part)) for part in parts]
    total_weight = sum(weights) or 1.0
    cursor = start
    cues: list[SubtitleCue] = []
    for index, (part, weight) in enumerate(zip(parts, weights)):
        cue_end = end if index == len(parts) - 1 else min(end, cursor + duration * weight / total_weight)
        cue_end = max(cursor + 0.05, cue_end)
        cues.append(SubtitleCue(start=cursor, end=cue_end, text=part))
        cursor = cue_end
    return cues


def _chunk_cues_for_tts(cues: list[SubtitleCue]) -> list[TtsChunk]:
    cues = [cue for cue in cues if cue.text and cue.end > cue.start]
    if not cues:
        return []

    target_duration = _env_float("AETHER_TTS_CHUNK_TARGET_SECONDS", 45.0, minimum=10.0, maximum=180.0)
    min_duration = _env_float("AETHER_TTS_CHUNK_MIN_SECONDS", 30.0, minimum=5.0, maximum=120.0)
    max_duration = _env_float("AETHER_TTS_CHUNK_MAX_SECONDS", 60.0, minimum=10.0, maximum=300.0)
    pause_threshold = _env_float("AETHER_TTS_CHUNK_PAUSE_THRESHOLD", 0.7, minimum=0.0, maximum=10.0)
    max_chars = _env_int("AETHER_TTS_CHUNK_MAX_CHARS", 6500, minimum=500, maximum=30000)

    chunks: list[TtsChunk] = []
    current: list[SubtitleCue] = []
    current_chars = 0

    for index, cue in enumerate(cues):
        current.append(cue)
        current_chars += len(cue.text) + 1
        next_cue = cues[index + 1] if index + 1 < len(cues) else None

        start = current[0].start
        end = current[-1].end
        duration = end - start
        next_gap = max(0.0, next_cue.start - end) if next_cue else 0.0
        should_close = (
            next_cue is None
            or duration >= max_duration
            or current_chars >= max_chars
            or (duration >= target_duration and next_gap >= pause_threshold)
            or (duration >= min_duration and next_gap >= pause_threshold * 1.5)
        )

        if should_close:
            chunks.append(TtsChunk(start=start, end=end, cues=current))
            current = []
            current_chars = 0

    if current:
        chunks.append(TtsChunk(start=current[0].start, end=current[-1].end, cues=current))
    return chunks


_WHISPERX_ALIGN_MODEL_CACHE: dict[tuple[str, str, str], tuple[Any, Any]] = {}


def _align_chunk_subtitles(
    chunk: TtsChunk,
    actual_duration: float,
    audio_path: Path | None = None,
    target_language: str = "en",
) -> list[AlignedSubtitleCue]:
    provider = os.getenv("AETHER_FORCED_ALIGNMENT_PROVIDER", "fallback").strip().lower()
    if provider in {"fallback", "proportional"}:
        return _fallback_align_chunk(chunk, actual_duration)
    if provider == "whisperx":
        if audio_path is None:
            raise RuntimeError("WhisperX alignment requires a generated audio file.")
        try:
            return _whisperx_align_chunk(chunk, actual_duration, audio_path, target_language)
        except Exception:
            if _forced_alignment_allow_fallback():
                return _fallback_align_chunk(chunk, actual_duration)
            raise
    if provider == "mfa":
        raise RuntimeError("MFA alignment is not implemented yet. Use AETHER_FORCED_ALIGNMENT_PROVIDER=whisperx or fallback.")
    if provider not in {"fallback", "proportional", "whisperx", "mfa"}:
        provider = "fallback"
    return _fallback_align_chunk(chunk, actual_duration)


def _whisperx_align_chunk(
    chunk: TtsChunk,
    actual_duration: float,
    audio_path: Path,
    target_language: str,
) -> list[AlignedSubtitleCue]:
    language_code = _alignment_language_code(target_language)
    remote_url = os.getenv("AETHER_WHISPERX_API_URL", "").strip()
    if remote_url:
        return _remote_whisperx_align_chunk(chunk, actual_duration, audio_path, language_code, remote_url)

    _allow_duplicate_openmp_on_windows()
    device = _whisperx_device()
    whisperx = _import_whisperx()
    model, metadata = _load_whisperx_alignment_model(whisperx, language_code, device)

    audio = whisperx.load_audio(str(audio_path))
    aligned = whisperx.align(
        [{"start": 0.0, "end": actual_duration, "text": chunk.text}],
        model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )
    word_segments = _extract_whisperx_word_segments(aligned)
    if not word_segments:
        raise RuntimeError("WhisperX did not return word-level timestamps for this chunk.")
    return _cue_timings_from_aligned_words(chunk, actual_duration, word_segments)


def _remote_whisperx_align_chunk(
    chunk: TtsChunk,
    actual_duration: float,
    audio_path: Path,
    language_code: str,
    base_url: str,
) -> list[AlignedSubtitleCue]:
    url = base_url.rstrip("/") + "/align"
    payload = {
        "audio_base64": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
        "language": language_code,
        "transcript": chunk.text,
        "segments": [{"start": 0.0, "end": actual_duration, "text": chunk.text}],
        "return_char_alignments": False,
    }
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("AETHER_WHISPERX_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=_env_int("AETHER_WHISPERX_TIMEOUT_SECONDS", 300, minimum=30, maximum=3600),
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Remote WhisperX request failed: {response.text[:500]}")

    data = response.json()
    word_segments = data.get("word_segments") or []
    if not word_segments:
        raise RuntimeError("Remote WhisperX did not return word-level timestamps for this chunk.")
    return _cue_timings_from_aligned_words(chunk, actual_duration, word_segments)


def _import_whisperx():
    # WhisperX can pull in Torch/NumPy libraries that initialize Intel OpenMP.
    # On Windows/Conda this may collide with another libiomp5md.dll already
    # loaded by the process and abort Python before an exception is raised.
    # Set the standard workaround before importing WhisperX so failed alignment
    # can fall back instead of killing the API worker.
    _allow_duplicate_openmp_on_windows()
    try:
        import whisperx  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "WhisperX is not installed. Install optional alignment dependencies with "
            "`pip install -r apps/api/requirements-audio.txt`."
        ) from exc
    return whisperx


def _allow_duplicate_openmp_on_windows() -> None:
    if os.name == "nt" and not os.getenv("KMP_DUPLICATE_LIB_OK"):
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"


def _load_whisperx_alignment_model(whisperx, language_code: str, device: str) -> tuple[Any, Any]:
    model_name = os.getenv("AETHER_WHISPERX_ALIGN_MODEL", "").strip()
    cache_key = (language_code, device, model_name)
    if cache_key not in _WHISPERX_ALIGN_MODEL_CACHE:
        kwargs = {"language_code": language_code, "device": device}
        if model_name:
            kwargs["model_name"] = model_name
        _WHISPERX_ALIGN_MODEL_CACHE[cache_key] = whisperx.load_align_model(**kwargs)
    return _WHISPERX_ALIGN_MODEL_CACHE[cache_key]


def _extract_whisperx_word_segments(aligned: dict[str, Any]) -> list[dict[str, Any]]:
    words = aligned.get("word_segments") or []
    if words:
        return [word for word in words if "start" in word and "end" in word]

    extracted: list[dict[str, Any]] = []
    for segment in aligned.get("segments") or []:
        for word in segment.get("words") or []:
            if "start" in word and "end" in word:
                extracted.append(word)
    return extracted


def _cue_timings_from_aligned_words(
    chunk: TtsChunk,
    actual_duration: float,
    word_segments: list[dict[str, Any]],
) -> list[AlignedSubtitleCue]:
    cues = [cue for cue in chunk.cues if cue.text]
    if not cues:
        return []

    weights = [max(1, _word_count(cue.text)) for cue in cues]
    total_words = sum(weights)
    if len(word_segments) < max(1, round(total_words * 0.35)):
        raise RuntimeError(
            f"WhisperX returned too few aligned words ({len(word_segments)}) for {total_words} expected cue words."
        )

    word_index = 0
    cursor = chunk.start
    chunk_end = chunk.start + max(0.05, actual_duration)
    aligned: list[AlignedSubtitleCue] = []
    for index, (cue, cue_words) in enumerate(zip(cues, weights)):
        remaining_cues = len(cues) - index
        remaining_words = len(word_segments) - word_index
        take = max(1, min(cue_words, remaining_words - remaining_cues + 1))

        if index == len(cues) - 1:
            cue_word_segments = word_segments[word_index:]
        else:
            cue_word_segments = word_segments[word_index : word_index + take]
        word_index += len(cue_word_segments)

        if cue_word_segments:
            start = chunk.start + max(0.0, float(cue_word_segments[0]["start"]))
            end = chunk.start + max(0.0, float(cue_word_segments[-1]["end"]))
        else:
            start = cursor
            end = cursor + max(0.05, actual_duration * cue_words / max(1, total_words))

        start = max(cursor, min(start, chunk_end))
        end = max(start + 0.05, min(end, chunk_end))
        if index == len(cues) - 1:
            end = max(end, chunk_end)
        aligned.append(AlignedSubtitleCue(start=start, end=end, text=cue.text))
        cursor = end
    return aligned


def _alignment_language_code(target_language: str) -> str:
    override = os.getenv("AETHER_FORCED_ALIGNMENT_LANGUAGE", "").strip().lower()
    value = override or (target_language or "en").strip().lower()
    value = value.split("-")[0].split("_")[0]
    aliases = {"vn": "vi", "zh-cn": "zh", "zh-tw": "zh"}
    return aliases.get(value, value or "en")


def _whisperx_device() -> str:
    configured = os.getenv("AETHER_WHISPERX_DEVICE", "").strip()
    if configured:
        return configured
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _forced_alignment_allow_fallback() -> bool:
    return os.getenv("AETHER_FORCED_ALIGNMENT_ALLOW_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}


def _fallback_align_chunk(chunk: TtsChunk, actual_duration: float) -> list[AlignedSubtitleCue]:
    cues = [cue for cue in chunk.cues if cue.text]
    if not cues:
        return []

    weights = [max(1.0, _word_count(cue.text)) for cue in cues]
    total_weight = sum(weights) or 1.0
    cursor = chunk.start
    chunk_end = chunk.start + max(0.05, actual_duration)
    aligned: list[AlignedSubtitleCue] = []
    for index, (cue, weight) in enumerate(zip(cues, weights)):
        if index == len(cues) - 1:
            end = chunk_end
        else:
            end = min(chunk_end, cursor + actual_duration * weight / total_weight)
        if end <= cursor:
            end = min(chunk_end, cursor + 0.05)
        aligned.append(AlignedSubtitleCue(start=cursor, end=end, text=cue.text))
        cursor = end
    return aligned


def _slice_chunk_audio_by_aligned_cues(
    source_audio: Path,
    chunk: TtsChunk,
    aligned_cues: list[AlignedSubtitleCue],
    output_dir: Path,
    chunk_id: str,
) -> list[TimedAudioSegment]:
    """Split aligned chunk audio while retaining the original subtitle timeline."""
    if len(aligned_cues) != len(chunk.cues):
        raise ValueError("Aligned cue count must match the source chunk cue count.")
    output_dir.mkdir(parents=True, exist_ok=True)
    segments: list[TimedAudioSegment] = []
    for index, (cue, aligned) in enumerate(zip(chunk.cues, aligned_cues), start=1):
        offset = max(0.0, aligned.start - chunk.start)
        duration = max(0.05, aligned.end - aligned.start)
        output = output_dir / f"{chunk_id}-{index:04d}.mp3"
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{offset:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source_audio),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "3",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            raise RuntimeError(
                f"Unable to slice aligned TTS cue {chunk_id}-{index:04d}: {result.stderr[-800:]}"
            )
        segments.append(
            TimedAudioSegment(
                path=output,
                start=cue.start,
                end=cue.end,
            )
        )
    return segments


def _write_aligned_subtitles(path: Path, cues: list[AlignedSubtitleCue]) -> None:
    blocks = [
        f"{index}\n{_format_srt_time(cue.start)} --> {_format_srt_time(cue.end)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
        if cue.text and cue.end > cue.start
    ]
    if blocks:
        path.write_text("\n\n".join(blocks).strip() + "\n", encoding="utf-8")


def _create_sentence_distributed_audio(
    sentences: list[str],
    target_duration: float,
    voice: str,
    rate: str,
    workdir: Path,
    label: str,
) -> list[Path]:
    sentence_audio: list[Path] = []
    for sentence_index, sentence in enumerate(sentences, start=1):
        part = workdir / f"{label}_sentence_{sentence_index:02d}.mp3"
        _save_tts_segment(sentence, voice, part, rate, workdir, f"{label}_sentence_{sentence_index:02d}")
        sentence_audio.append(part)

    measured = [_probe_duration(path) for path in sentence_audio]
    total_voice_duration = sum(duration for duration in measured if duration > 0)
    if total_voice_duration <= 0:
        return sentence_audio

    fitted_audio = sentence_audio

    trailing_silence = max(0.0, target_duration - total_voice_duration)
    pause_per_sentence = trailing_silence / len(fitted_audio) if fitted_audio else 0.0

    parts: list[Path] = []
    for sentence_index, path in enumerate(fitted_audio, start=1):
        parts.append(path)
        if pause_per_sentence >= 0.05:
            silence = workdir / f"{label}_sentence_{sentence_index:02d}_pause.mp3"
            _create_silence(silence, pause_per_sentence)
            parts.append(silence)
    return parts


async def _save_edge_tts(text: str, voice: str, output: Path, rate: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(output))


def _save_tts_segment(
    text: str,
    voice: str,
    output: Path,
    rate: str,
    workdir: Path,
    label: str,
    target_duration: float | None = None,
    voice_overrides: VoiceOverrides | None = None,
) -> Path:
    cleaned = _sanitize_tts_text(text)
    silence_duration = target_duration if target_duration and target_duration > 0 else 0.3

    if not _has_speakable_content(cleaned):
        # Punctuation/symbol-only cue: nothing to voice. Emit silence sized to the
        # slot so the timeline stays aligned instead of failing on empty TTS audio.
        return _create_silence(output, silence_duration)

    try:
        _save_tts_with_retries(cleaned, voice, output, rate, target_duration, voice_overrides)
        return output
    except Exception as initial_exc:
        if _is_edge_no_audio_error(initial_exc):
            # edge-tts accepted the connection but returned no audio chunks.
            # This happens for very short text, certain Unicode characters, or
            # transient rate-limiting where every retry returns the same empty
            # response.  Emit silence so the rest of the dub continues.
            logger.warning(
                "Edge TTS returned no audio for cue '%s' (text: %r); substituting silence.",
                label,
                cleaned[:80],
            )
            return _create_silence(output, silence_duration)
        fragments = _split_tts_text(cleaned)
        if len(fragments) <= 1:
            raise

    parts: list[Path] = []
    total_weight = max(1.0, sum(_word_count(fragment) for fragment in fragments))
    for index, fragment in enumerate(fragments, start=1):
        part = workdir / f"{label}_split_{index:02d}.mp3"
        fragment_duration = None
        if target_duration:
            fragment_duration = max(0.25, target_duration * _word_count(fragment) / total_weight)
        try:
            _save_tts_with_retries(fragment, voice, part, rate, fragment_duration, voice_overrides)
        except Exception as frag_exc:
            if _is_edge_no_audio_error(frag_exc):
                logger.warning(
                    "Edge TTS returned no audio for fragment %d of cue '%s' (text: %r); substituting silence.",
                    index,
                    label,
                    fragment[:80],
                )
                _create_silence(part, fragment_duration or silence_duration)
            else:
                raise
        parts.append(part)
    _concat_audio(parts, output, workdir / f"{label}_split_concat.txt")
    return output


def _save_tts_with_retries(
    text: str,
    voice: str,
    output: Path,
    rate: str,
    target_duration: float | None = None,
    voice_overrides: VoiceOverrides | None = None,
) -> None:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            _save_provider_tts(text, voice, output, rate, target_duration, voice_overrides)
            if output.exists() and output.stat().st_size > 0:
                return
            raise RuntimeError("No audio was received.")
        except Exception as exc:
            last_error = exc
            output.unlink(missing_ok=True)
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(str(last_error) if last_error else "TTS provider did not return audio.")


def _save_provider_tts(
    text: str,
    voice: str,
    output: Path,
    rate: str,
    target_duration: float | None = None,
    voice_overrides: VoiceOverrides | None = None,
) -> None:
    provider = _tts_provider()
    if provider == "vieneu":
        synthesize_vieneu(text, voice, output)
        return
    if provider == "omnivoice":
        try:
            _save_omnivoice_tts(text, voice, output, rate, voice_overrides)
            return
        except Exception as exc:
            if not _tts_fallback_to_edge_enabled():
                raise
            output.unlink(missing_ok=True)
            fallback_voice = _edge_fallback_voice(voice)
            logger.warning(
                "OmniVoice synthesis failed (%s); falling back to default voice '%s'.",
                exc,
                fallback_voice,
            )
            asyncio.run(_save_edge_tts(text, fallback_voice, output, rate))
            return
    asyncio.run(_save_edge_tts(text, voice, output, rate))


def _tts_fallback_to_edge_enabled() -> bool:
    # Disabled by default: when a user explicitly selects an OmniVoice/clone voice
    # (e.g. "CDTeam"), silently substituting a generic Edge voice (e.g. HoaiMy)
    # produces a render with the wrong voice. Honour the selected voice — attempt
    # to bring up the OmniVoice runtime and otherwise fail loudly. Set
    # AETHER_TTS_FALLBACK_TO_EDGE=1 to opt back into the resilient substitution.
    return _env_bool("AETHER_TTS_FALLBACK_TO_EDGE", False)


def _edge_fallback_voice(voice: str) -> str:
    """Pick a usable Edge voice when OmniVoice is unavailable.

    The incoming ``voice`` may be an OmniVoice voice-option id that Edge cannot
    speak, so resolve to a language-appropriate neural voice, defaulting to
    Vietnamese (the app's primary target language).
    """
    normalized = (voice or "").strip()
    if normalized.endswith("Neural") and "-" in normalized:
        return normalized
    option = _find_voice_option(normalized)
    if option is not None:
        language_key = (getattr(option, "locale", "") or "").split("-")[0].lower()
        if language_key in VOICE_BY_LANGUAGE:
            return VOICE_BY_LANGUAGE[language_key]
    return VOICE_BY_LANGUAGE.get("vi", VOICE_BY_LANGUAGE["en"])


def _nvidia_api_key() -> str | None:
    api_key = (os.getenv("NVIDIA_API_KEY") or "").strip().strip('"').strip("'")
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()
    return api_key or None


def _omnivoice_timeout_seconds() -> int:
    return _env_int("AETHER_OMNIVOICE_TIMEOUT_SECONDS", 300, minimum=30, maximum=7200)


def _save_omnivoice_tts(
    text: str,
    voice: str,
    output: Path,
    rate: str,
    voice_overrides: VoiceOverrides | None = None,
) -> None:
    settings = get_runtime_settings()
    voice_option = _find_voice_option(voice)
    base_url = _resolve_omnivoice_base_url(settings)
    if not base_url:
        raise RuntimeError("OMNIVOICE_API_URL is required when AETHER_TTS_PROVIDER=omnivoice.")

    mode = _voice_mode(settings, voice_option, voice_overrides)
    if voice_option and not mode and (voice_option.reference_audio_url or voice_option.reference_audio_path):
        mode = "clone"
    if mode not in {"auto", "design", "clone"}:
        mode = "auto"
    speed = _omnivoice_speed(rate)

    if _omnivoice_transport(base_url) == "gradio":
        _save_omnivoice_gradio_tts(
            text,
            voice,
            output,
            speed,
            settings,
            voice_option,
            mode,
            voice_overrides,
        )
        return

    payload: dict[str, object] = {"text": text, "mode": mode, "output_format": "wav"}
    if speed != 1.0:
        payload["speed"] = speed
    instruction = _voice_instruction(settings, voice_option, voice, mode, voice_overrides)
    if instruction:
        payload["instruct"] = instruction

    if mode == "clone":
        ref_audio_payload, _ref_audio_source = _omnivoice_reference_audio_payload(
            settings,
            voice_option,
            voice_overrides,
        )
        payload.update(ref_audio_payload)
        ref_text = _voice_reference_text(settings, voice_option, voice_overrides)
        if not ref_text:
            ref_text_path = voice_option.reference_text_path if voice_option and voice_option.reference_text_path else settings.omnivoice_ref_text_path
            ref_text = _read_optional_text(ref_text_path)
        if ref_text:
            payload["ref_text"] = ref_text

    headers = {}
    api_key = settings.omnivoice_api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = _omnivoice_timeout_seconds()
    response = requests.post(f"{base_url}/synthesize/file", json=payload, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"OmniVoice request failed: {_omnivoice_error_detail(response)}")

    temp_wav = output.with_suffix(".omnivoice.wav")
    temp_wav.write_bytes(response.content)
    if output.suffix.lower() == ".wav":
        temp_wav.replace(output)
        return

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(temp_wav),
        "-c:a",
        "libmp3lame",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    temp_wav.unlink(missing_ok=True)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to convert OmniVoice audio: {result.stderr[-1000:]}")


def _omnivoice_transport(base_url: str) -> str:
    configured = (
        os.getenv("AETHER_OMNIVOICE_TRANSPORT")
        or os.getenv("OMNIVOICE_TRANSPORT")
        or "auto"
    ).strip().lower()
    if configured in {"fastapi", "gradio"}:
        return configured
    lowered = base_url.lower()
    if "gradio.live" in lowered or "hf.space" in lowered:
        return "gradio"
    return "fastapi"


def _save_omnivoice_gradio_tts(
    text: str,
    voice: str,
    output: Path,
    speed: float,
    settings,
    voice_option,
    mode: str,
    voice_overrides: VoiceOverrides | None = None,
) -> None:
    try:
        from gradio_client import Client, handle_file
    except Exception as exc:
        raise RuntimeError("gradio_client is required for OmniVoice Gradio URLs. Install apps/api/requirements.txt again.") from exc

    base_url = settings.omnivoice_api_url.strip().rstrip("/")
    ref_audio = _gradio_reference_audio(settings, voice_option, voice_overrides)
    if not ref_audio:
        raise RuntimeError("A reference audio file or URL is required for the OmniVoice Gradio clone endpoint.")

    ref_text = _voice_reference_text(settings, voice_option, voice_overrides) or _voice_ref_text(settings, voice_option)
    instruction = (
        voice_overrides.instruction.strip()
        if voice_overrides and voice_overrides.instruction.strip()
        else _gradio_instruction(settings, voice_option)
    )
    client = Client(base_url)
    result = client.predict(
        text=text,
        lang=os.getenv("OMNIVOICE_GRADIO_LANG", "Auto"),
        ref_aud=handle_file(ref_audio),
        ref_text=ref_text or "",
        instruct=instruction or "",
        ns=_env_int("OMNIVOICE_GRADIO_NS", 32, minimum=1, maximum=256),
        gs=_env_float("OMNIVOICE_GRADIO_GS", 2.0, minimum=0.0, maximum=20.0),
        dn=_env_bool("OMNIVOICE_GRADIO_DN", True),
        sp=speed,
        du=0.0,
        pp=_env_bool("OMNIVOICE_GRADIO_PP", True),
        po=_env_bool("OMNIVOICE_GRADIO_PO", True),
        api_name=os.getenv("OMNIVOICE_GRADIO_API_NAME", "/_clone_fn"),
    )
    source = _extract_gradio_audio_result(result)
    if not source:
        raise RuntimeError(f"OmniVoice Gradio did not return an audio file. Result: {result!r}")
    _copy_or_convert_audio_result(source, output)


def _gradio_reference_audio(
    settings,
    voice_option,
    voice_overrides: VoiceOverrides | None = None,
) -> str | Path | None:
    for candidate in _reference_audio_candidates(settings, voice_option, voice_overrides):
        if _is_http_url(candidate):
            return candidate
        resolved = _resolve_existing_file(candidate)
        if resolved:
            return resolved
    return None


def _voice_ref_text(settings, voice_option) -> str:
    ref_text = voice_option.reference_text if voice_option and voice_option.reference_text else settings.omnivoice_ref_text
    if ref_text:
        return ref_text
    ref_text_path = voice_option.reference_text_path if voice_option and voice_option.reference_text_path else settings.omnivoice_ref_text_path
    return _read_optional_text(ref_text_path) or ""


def _voice_mode(settings, voice_option, voice_overrides: VoiceOverrides | None = None) -> str:
    override = voice_overrides.mode.strip().lower() if voice_overrides else ""
    mode = override or (
        voice_option.omnivoice_mode
        if voice_option and voice_option.omnivoice_mode
        else settings.omnivoice_mode
    ).strip().lower()
    has_override_reference = bool(
        voice_overrides
        and (
            voice_overrides.reference_audio_url.strip()
            or voice_overrides.reference_audio_path.strip()
        )
    )
    if not mode and (
        has_override_reference
        or (
            voice_option
            and (voice_option.reference_audio_url or voice_option.reference_audio_path)
        )
    ):
        mode = "clone"
    return mode if mode in {"auto", "design", "clone"} else "auto"


def _voice_reference_audio_url(settings, voice_option, voice_overrides: VoiceOverrides | None = None) -> str:
    for candidate in _reference_audio_candidates(settings, voice_option, voice_overrides):
        if _is_http_url(candidate):
            return candidate
    return ""


def _reference_audio_candidates(
    settings,
    voice_option,
    voice_overrides: VoiceOverrides | None = None,
) -> list[str]:
    candidates = [
        voice_overrides.reference_audio_url if voice_overrides else "",
        voice_overrides.reference_audio_path if voice_overrides else "",
        getattr(voice_option, "reference_audio_url", "") if voice_option else "",
        getattr(voice_option, "reference_audio_path", "") if voice_option else "",
        getattr(settings, "omnivoice_ref_audio_url", ""),
        getattr(settings, "omnivoice_ref_audio_path", ""),
    ]
    return [str(value).strip() for value in candidates if str(value or "").strip()]


def _omnivoice_reference_audio_payload(
    settings,
    voice_option,
    voice_overrides: VoiceOverrides | None = None,
) -> tuple[dict[str, str], str]:
    for candidate in _reference_audio_candidates(settings, voice_option, voice_overrides):
        if _is_http_url(candidate):
            return {"ref_audio_url": candidate}, "url"
        ref_audio_base64 = _read_audio_base64(candidate)
        if ref_audio_base64:
            return {"ref_audio_base64": ref_audio_base64}, "base64"
    return {}, ""


def _is_http_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _voice_reference_text(settings, voice_option, voice_overrides: VoiceOverrides | None = None) -> str:
    if voice_overrides and voice_overrides.reference_text.strip():
        return voice_overrides.reference_text.strip()
    return voice_option.reference_text if voice_option and voice_option.reference_text else settings.omnivoice_ref_text


def _voice_instruction(
    settings,
    voice_option,
    voice: str,
    mode: str,
    voice_overrides: VoiceOverrides | None = None,
) -> str:
    if voice_overrides and voice_overrides.instruction.strip():
        return voice_overrides.instruction.strip()
    instruction = voice_option.instruction if voice_option and voice_option.instruction else settings.omnivoice_instruct
    if instruction:
        return instruction
    return voice if mode == "design" else ""


def _combine_edge_rate_with_speech_rate(rate: str, speech_rate: SpeechRateProfile | None) -> str:
    scale = speech_rate_duration_scale(speech_rate)
    if scale == 1.0:
        return rate
    manual_percent = _rate_percent(rate)
    speech_percent = round((1.0 / scale - 1.0) * 100)
    combined = max(-15, min(18, manual_percent + speech_percent))
    return f"{combined:+d}%"


def _omnivoice_speed(rate: str | None) -> float:
    return round(min(2.0, max(0.5, 1.0 + _rate_percent(rate) / 100.0)), 3)


def _rate_percent(rate: str | None) -> int:
    if not rate:
        return 0
    match = re.fullmatch(r"([+-])(\d{1,2})%", rate.strip())
    if not match:
        return 0
    value = int(match.group(2))
    return value if match.group(1) == "+" else -value


def _speech_rate_manifest(speech_rate: SpeechRateProfile | None) -> dict[str, object] | None:
    if not speech_rate:
        return None
    return {
        "category": speech_rate.category,
        "units_per_second": speech_rate.units_per_second,
        "median_cue_units_per_second": speech_rate.median_cue_units_per_second,
        "active_duration_seconds": speech_rate.active_duration_seconds,
        "total_units": speech_rate.total_units,
        "cue_count": speech_rate.cue_count,
        "pause_ratio": speech_rate.pause_ratio,
        "overlap_ratio": speech_rate.overlap_ratio,
        "recommended_duration_scale": speech_rate.recommended_duration_scale,
        "recommended_edge_rate": speech_rate.recommended_edge_rate,
        "confidence": speech_rate.confidence,
    }


def _gradio_instruction(settings, voice_option) -> str:
    configured = os.getenv("OMNIVOICE_GRADIO_INSTRUCT", "").strip()
    raw = configured or (voice_option.instruction if voice_option and voice_option.instruction else settings.omnivoice_instruct)
    if not raw:
        return ""
    if "，" in raw and re.search(r"[\u4e00-\u9fff]", raw):
        return raw

    supported = {
        "american accent",
        "australian accent",
        "british accent",
        "canadian accent",
        "child",
        "chinese accent",
        "elderly",
        "female",
        "high pitch",
        "indian accent",
        "japanese accent",
        "korean accent",
        "low pitch",
        "male",
        "middle-aged",
        "moderate pitch",
        "portuguese accent",
        "russian accent",
        "teenager",
        "very high pitch",
        "very low pitch",
        "whisper",
        "young adult",
    }
    tags = [tag.strip().lower() for tag in raw.split(",") if tag.strip()]
    if tags and all(tag in supported for tag in tags):
        return ", ".join(tags)
    return ""


def _extract_gradio_audio_result(result) -> str | Path | None:
    if result is None:
        return None
    if isinstance(result, (str, Path)):
        return result
    if isinstance(result, dict):
        for key in ("path", "url", "value", "name"):
            value = result.get(key)
            found = _extract_gradio_audio_result(value)
            if found:
                return found
        return None
    if isinstance(result, (list, tuple)):
        for item in result:
            found = _extract_gradio_audio_result(item)
            if found:
                return found
    return None


def _copy_or_convert_audio_result(source: str | Path, output: Path) -> None:
    source_path: Path
    raw = str(source)
    if raw.startswith(("http://", "https://")):
        source_path = output.with_suffix(Path(raw.split("?", 1)[0]).suffix or ".wav")
        response = requests.get(raw, timeout=300)
        response.raise_for_status()
        source_path.write_bytes(response.content)
    else:
        source_path = Path(raw)
        if not source_path.exists():
            raise RuntimeError(f"OmniVoice Gradio returned a missing audio file: {source_path}")

    if source_path.suffix.lower() == output.suffix.lower():
        shutil.copyfile(source_path, output)
        return

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-c:a",
        "libmp3lame",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to convert OmniVoice Gradio audio: {result.stderr[-1000:]}")


def _find_voice_option(voice_id: str):
    normalized = (voice_id or "").strip()
    if not normalized:
        return None
    return next((voice for voice in list_voice_options() if voice.id == normalized), None)


def _read_audio_base64(path_value: str) -> str | None:
    path = _resolve_existing_file(path_value)
    if not path:
        return None
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _read_optional_text(path_value: str) -> str | None:
    path = _resolve_existing_file(path_value)
    if not path:
        return None
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    return text or None


def _resolve_existing_file(path_value: str) -> Path | None:
    raw = (path_value or "").strip().strip('"').strip("'")
    if not raw:
        return None
    candidates = [Path(raw).expanduser()]
    if not candidates[0].is_absolute():
        candidates.append(Path.cwd() / raw)
        candidates.append(Path(__file__).resolve().parents[4] / raw)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _sanitize_tts_text(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = cleaned.replace("&", " và ")
    cleaned = re.sub(r"[`*_#\\[\\]{}|~^]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or " "


def _has_speakable_content(text: str) -> bool:
    """True if the text has anything a TTS engine can voice (letters or digits).

    Edge TTS returns "No audio was received" for whitespace/punctuation/emoji-only
    input. Such cues carry no speech, so the timeline should get silence for that
    slot instead of failing the whole dub.
    """
    return any(char.isalnum() for char in text)


def _is_edge_no_audio_error(exc: BaseException) -> bool:
    """True when edge-tts refused to produce audio for the given text.

    Distinguished from transient network/connection failures so we can emit
    silence for the cue rather than failing the entire dub.  The check is
    intentionally broad (substring match) because edge-tts surfaces the same
    root cause through both its own ``NoAudioReceived`` exception and our
    empty-file ``RuntimeError``.
    """
    return "no audio was received" in str(exc).lower()


def _split_tts_text(text: str, max_chars: int = 90) -> list[str]:
    provider = _tts_provider()
    max_chars = _env_int(
        "AETHER_TTS_SPLIT_MAX_CHARS",
        14000 if provider == "omnivoice" else max_chars,
        minimum=60,
        maximum=30000,
    )
    max_words = _env_int(
        "AETHER_TTS_SPLIT_MAX_WORDS",
        1800 if provider == "omnivoice" else 60,
        minimum=20,
        maximum=2500,
    )
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    fragments: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= max_chars and _word_count(candidate) <= max_words:
            current = f"{current} {sentence}".strip()
            continue
        if current:
            fragments.append(current)
        current = sentence
    if current:
        fragments.append(current)

    expanded: list[str] = []
    for fragment in fragments or [text]:
        if len(fragment) <= max_chars and _word_count(fragment) <= max_words:
            expanded.append(fragment)
            continue
        words = fragment.split()
        chunk: list[str] = []
        for word in words:
            candidate = " ".join([*chunk, word])
            if (len(candidate) > max_chars or len(candidate.split()) > max_words) and chunk:
                expanded.append(" ".join(chunk))
                chunk = [word]
            else:
                chunk.append(word)
        if chunk:
            expanded.append(" ".join(chunk))
    return expanded


def _resolve_voice(voice: str, target_language: str) -> str:
    normalized = (voice or "").strip()
    option = _find_voice_option(normalized)
    if option:
        # Edge TTS only accepts Microsoft Neural voice IDs (e.g. "vi-VN-HoaiMyNeural").
        # OmniVoice-specific IDs (e.g. "CDTeam") must not be forwarded to Edge TTS.
        is_neural_id = normalized.endswith("Neural") and "-" in normalized
        if is_neural_id or _tts_provider() in {"omnivoice", "vieneu"}:
            return normalized
        # Provider is Edge — map via the voice option's locale to the nearest neural voice.
        locale_key = (option.locale or "").split("-")[0].lower()
        lang_key = locale_key or (target_language or "en").split("-")[0].lower()
        return VOICE_BY_LANGUAGE.get(lang_key, VOICE_BY_LANGUAGE.get(
            (target_language or "en").split("-")[0].lower(), VOICE_BY_LANGUAGE["en"]
        ))
    matched_option = next(
        (o for o in list_voice_options() if o.name.strip().casefold() == normalized.casefold()),
        None,
    )
    if matched_option:
        is_neural_id = matched_option.id.endswith("Neural") and "-" in matched_option.id
        if is_neural_id or _tts_provider() in {"omnivoice", "vieneu"}:
            return matched_option.id
        locale_key = (matched_option.locale or "").split("-")[0].lower()
        lang_key = locale_key or (target_language or "en").split("-")[0].lower()
        return VOICE_BY_LANGUAGE.get(lang_key, VOICE_BY_LANGUAGE.get(
            (target_language or "en").split("-")[0].lower(), VOICE_BY_LANGUAGE["en"]
        ))
    if normalized.endswith("Neural") and "-" in normalized:
        return normalized
    language_key = (target_language or "en").split("-")[0].lower()
    if normalized.lower() in GENERIC_VOICES:
        return VOICE_BY_LANGUAGE.get(language_key, VOICE_BY_LANGUAGE["en"])
    alias = VOICE_ALIASES.get(normalized.lower())
    if alias:
        return alias
    return VOICE_BY_LANGUAGE.get(language_key, VOICE_BY_LANGUAGE["en"])


def _normalize_rate(rate: str | None) -> str:
    if not rate:
        return "+0%"
    value = rate.strip()
    if re.fullmatch(r"[+-]\d{1,2}%", value):
        return value
    return "+0%"


def _parse_srt_cues(path: Path) -> list[SubtitleCue]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if "-->" in block]
    cues: list[SubtitleCue] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start, end = _parse_timing_line(lines[timing_index])
        cue_text = " ".join(line for line in lines[timing_index + 1 :] if line and not line.isdigit()).strip()
        if cue_text and end > start:
            cues.append(SubtitleCue(start=start, end=end, text=cue_text))
    return cues


def _ordered_cues(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    return sorted(cues, key=lambda cue: (cue.start, cue.end))


def _group_cues_for_tts(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    if not cues:
        return []

    if _tts_unit_mode() == "cue":
        return [cue for cue in cues if cue.text]

    provider = _tts_provider()
    max_gap = _env_float("AETHER_TTS_GROUP_MAX_GAP", 0.5 if provider == "omnivoice" else 0.9, minimum=0.0, maximum=30.0)
    max_duration = _env_float("AETHER_TTS_GROUP_MAX_DURATION", 20.0 if provider == "omnivoice" else 11.0, minimum=1.0, maximum=900.0)
    max_chars = _env_int("AETHER_TTS_GROUP_MAX_CHARS", 1600 if provider == "omnivoice" else 240, minimum=80, maximum=30000)
    max_words = _env_int("AETHER_TTS_GROUP_MAX_WORDS", 240 if provider == "omnivoice" else 60, minimum=20, maximum=2500)

    groups: list[SubtitleCue] = []
    start = cues[0].start
    end = cues[0].end
    texts = [_clean_cue_text(cues[0].text)]

    for cue in cues[1:]:
        text = _clean_cue_text(cue.text)
        gap = cue.start - end
        projected_duration = cue.end - start
        projected_text = " ".join([*texts, text])
        projected_chars = len(projected_text)
        projected_words = _word_count(projected_text)
        should_merge = (
            gap <= max_gap
            and projected_duration <= max_duration
            and projected_chars <= max_chars
            and projected_words <= max_words
        )

        if should_merge:
            end = max(end, cue.end)
            texts.append(text)
            continue

        groups.append(SubtitleCue(start=start, end=end, text=_dedupe_joined_text(texts)))
        start = cue.start
        end = cue.end
        texts = [text]

    groups.append(SubtitleCue(start=start, end=end, text=_dedupe_joined_text(texts)))
    return [group for group in groups if group.text]


def _tts_provider() -> str:
    return _PROVIDER_OVERRIDE.get() or get_runtime_settings().tts_provider or "edge"


def _voice_requires_omnivoice(voice_id: str, voice_overrides: VoiceOverrides | None = None) -> bool:
    """Return True when the voice can only be synthesised by OmniVoice.

    Standard Edge TTS Neural IDs (e.g. "vi-VN-HoaiMyNeural") work with both engines.
    Clone / design voices that carry reference audio or an explicit omnivoice_mode need
    OmniVoice — Edge TTS cannot handle them and would silently fall back to the
    language-default neural voice.
    """
    if voice_overrides and (
        voice_overrides.reference_audio_url
        or voice_overrides.reference_audio_path
        or voice_overrides.mode in {"clone", "design"}
    ):
        return True
    option = _find_voice_option((voice_id or "").strip())
    if option is None:
        return False
    if option.omnivoice_mode in {"clone", "design"}:
        return True
    return bool(option.reference_audio_url or option.reference_audio_path)


def _tts_unit_mode() -> str:
    value = os.getenv("AETHER_TTS_UNIT_MODE", "").strip().lower()
    if value in {"cue", "group", "chunk"}:
        return value
    return "cue" if _tts_provider() == "omnivoice" else "group"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _clean_cue_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(">>", "")).strip()


def _dedupe_joined_text(parts: list[str]) -> str:
    result: list[str] = []
    for part in parts:
        if not part:
            continue
        if result and (part == result[-1] or part in result[-1]):
            continue
        result.append(part)
    return " ".join(result)


def _split_complete_sentences(text: str) -> list[str]:
    normalized = _clean_cue_text(text)
    if not normalized:
        return []

    raw_parts = [part.strip() for part in re.split(r"(?<=[.!?…。！？])\s+", normalized) if part.strip()]
    if len(raw_parts) <= 1:
        return [normalized]

    sentences: list[str] = []
    for part in raw_parts:
        if _ends_complete_sentence(part):
            sentences.append(part)
            continue
        if sentences:
            sentences[-1] = f"{sentences[-1]} {part}".strip()
        else:
            sentences.append(part)
    return sentences or [normalized]


def _ends_complete_sentence(text: str) -> bool:
    return bool(re.search(r"[.!?…。！？]['\")\]]?\s*$", text.strip()))


def _distribute_sentence_silence_enabled() -> bool:
    return os.getenv("AETHER_TTS_DISTRIBUTE_SENTENCE_SILENCE", "1").strip().lower() not in {"0", "false", "off", "no"}


def _parse_timing_line(line: str) -> tuple[float, float]:
    start_text, end_text = line.split("-->", 1)
    end_text = end_text.split()[0]
    return _srt_time_to_seconds(start_text.strip()), _srt_time_to_seconds(end_text.strip())


def _srt_time_to_seconds(value: str) -> float:
    match = re.match(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})", value)
    if not match:
        return 0.0
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms")) / 1000
    )


def _format_srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _fit_segment_to_duration(source: Path, destination: Path, target_duration: float) -> Path:
    # Keep provider-native speech speed. FFmpeg time-stretching makes cloned voice
    # sound unnatural, so timing is handled by semantic slicing and timeline mix.
    return source

    duration = _probe_duration(source)
    if duration <= 0 or target_duration <= 0:
        return source

    ratio = duration / target_duration

    # Nếu gần khớp rồi thì giữ nguyên
    if 0.97 <= ratio <= 1.03:
        return source

    # duration > target_duration: audio dài hơn -> tăng tốc
    # duration < target_duration: audio ngắn hơn -> làm chậm
    tempo = ratio

    # Không nên kéo quá mạnh vì sẽ méo voice
    tempo = max(0.99, min(1.005, tempo))

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-filter:a",
        _atempo_filter(tempo),
        "-c:a",
        "libmp3lame",
        str(destination),
    ]

    result = subprocess.run(command, capture_output=True, text=True, timeout=120)

    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError(f"Unable to fit TTS segment to subtitle timing: {result.stderr[-1000:]}")

    return destination


def _atempo_filter(tempo: float) -> str:
    factors: list[float] = []
    remaining = max(0.5, tempo)
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    factors.append(remaining)
    return ",".join(f"atempo={factor:.4f}" for factor in factors)


def _create_silence(output: Path, duration: float) -> Path:
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        f"{duration:.3f}",
        "-c:a",
        "libmp3lame",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to create silence for TTS alignment: {result.stderr[-1000:]}")
    return output


def _concat_audio(parts: list[Path], output: Path, list_file: Path) -> None:
    if not parts:
        raise RuntimeError("No audio segments were generated for TTS alignment.")
    list_file.write_text("".join(f"file '{part.resolve().as_posix()}'\n" for part in parts), encoding="utf-8")
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:a",
        "libmp3lame",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Unable to concatenate aligned TTS audio: {result.stderr[-1000:]}")


def _mix_timed_audio(segments: list[TimedAudioSegment], output: Path, filter_file: Path, duration: float) -> None:
    if not segments:
        raise RuntimeError("No timed audio segments were generated for TTS alignment.")

    duration = max(0.25, duration)
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-t",
        f"{duration:.3f}",
        "-i",
        "anullsrc=r=24000:cl=stereo",
    ]
    for segment in segments:
        command.extend(["-i", str(segment.path)])

    filter_file.write_text(_timeline_filter(segments, duration), encoding="utf-8")
    command.extend(
        [
            "-filter_complex_script",
            str(filter_file),
            "-map",
            "[aout]",
            "-c:a",
            "libmp3lame",
            "-t",
            f"{duration:.3f}",
            str(output),
        ]
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to mix timed TTS audio: {result.stderr[-1000:]}")


def _timeline_filter(segments: list[TimedAudioSegment], duration: float) -> str:
    lines = ["[0:a]volume=0[base]"]
    labels = ["[base]"]
    for index, segment in enumerate(segments, start=1):
        delay_ms = max(0, round(segment.start * 1000))
        segment_duration = max(0.05, segment.end - segment.start)
        label = f"s{index}"
        lines.append(
            f"[{index}:a]"
            f"atrim=0:{segment_duration:.3f},"
            "asetpts=PTS-STARTPTS,"
            f"adelay={delay_ms}:all=1"
            f"[{label}]"
        )
        labels.append(f"[{label}]")

    lines.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0,"
        + f"atrim=0:{duration:.3f},"
        + "alimiter=limit=0.95[aout]"
    )
    return ";\n".join(lines)


def _probe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _create_synthetic_audio(output: Path) -> Path:
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=6",
        "-c:a",
        "libmp3lame",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Unable to create fallback audio: {result.stderr[-1000:]}")
    return output
