from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .audio_bed import background_volume
from .renderer import render_video
from .speech_rate import SpeechRateProfile
from .storage import ensure_storage
from . import tts


def create_audio_cue_manifest(
    job_id: str,
    source_video_path: Path,
    translated_subtitle_path: Path,
    voice: str,
    target_language: str,
    voice_rate: str,
    speech_rate: SpeechRateProfile | None = None,
    voice_overrides: tts.VoiceOverrides | None = None,
    speaker_voice_map: dict[str, dict[str, str]] | None = None,
    diarization_job_id: str | None = None,
    audio_mix: dict[str, Any] | None = None,
) -> Path:
    root = ensure_storage()
    cue_dir = root / "audio-cues" / job_id
    cue_dir.mkdir(parents=True, exist_ok=True)

    if speaker_voice_map and diarization_job_id:
        outputs = tts.generate_multivoice_cue_outputs(
            translated_subtitle_path,
            target_language,
            speaker_voice_map,
            diarization_job_id,
            speech_rate=speech_rate,
            voice_overrides=voice_overrides,
            output_dir=cue_dir,
        )
        tracks = [
            {
                **{key: value for key, value in item.items() if key not in {"audio_path", "diarization_job_id"}},
                "suggested_start": item["start"],
                "audio_url": _storage_url(item["audio_path"]),
                "volume": 1.0,
                "muted": False,
                "locked": False,
            }
            for item in outputs
        ]
        duration_seconds = max(
            tts._probe_duration(source_video_path),
            max((float(cue["end"]) for cue in tracks), default=0.0),
        )
        return save_audio_cue_manifest(
            job_id,
            {
                "job_id": job_id,
                "source_video_url": _storage_url(source_video_path),
                "translated_subtitle_url": _storage_url(translated_subtitle_path),
                "background_audio_url": None,
                "duration_seconds": round(duration_seconds, 3),
                "tracks": {
                    "tts": tracks,
                    "background": {"audio_url": None, "volume": _background_gain(audio_mix)},
                },
                "tts_cue_source": {
                    "strategy": "speaker_diarization_multivoice",
                    "diarization_job_id": diarization_job_id,
                },
            },
        )

    selected_voice = tts._resolve_voice(voice, target_language)
    selected_rate = tts._normalize_rate(voice_rate)
    if tts._should_send_srt_to_omnivoice():
        return _create_omnivoice_audio_cue_manifest(
            job_id,
            source_video_path,
            translated_subtitle_path,
            selected_voice,
            selected_rate,
            cue_dir,
            speech_rate,
            voice_overrides,
            audio_mix,
        )

    if tts._tts_provider() != "omnivoice":
        selected_rate = tts._combine_edge_rate_with_speech_rate(selected_rate, speech_rate)

    cues = tts._parse_srt_cues(translated_subtitle_path)
    if not cues:
        raise RuntimeError("Translated subtitle has no readable cues for manual audio review.")

    semantic_cues = tts._semantic_sentence_cues(tts._ordered_cues(cues))
    chunks = tts._chunk_cues_for_tts(semantic_cues)
    if not chunks:
        raise RuntimeError("No semantic TTS chunks could be created for manual audio review.")

    tts_tracks: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        cue_id = f"cue-{index:04d}"
        audio_path = cue_dir / f"{cue_id}.mp3"
        target_duration = max(0.25, chunk.end - chunk.start)
        try:
            tts._save_tts_segment(
                chunk.text,
                selected_voice,
                audio_path,
                selected_rate,
                cue_dir,
                cue_id,
                target_duration,
                voice_overrides,
            )
        except Exception as exc:
            raise RuntimeError(f"TTS cue generation failed for {cue_id}: {exc}") from exc

        audio_duration = tts._probe_duration(audio_path)
        duration = max(0.05, audio_duration or target_duration)
        tts_tracks.append(
            {
                "id": cue_id,
                "index": index,
                "start": round(chunk.start, 3),
                "end": round(chunk.start + duration, 3),
                "suggested_start": round(chunk.start, 3),
                "suggested_end": round(chunk.end, 3),
                "duration": round(duration, 3),
                "target_duration": round(target_duration, 3),
                "audio_url": _storage_url(audio_path),
                "text": chunk.text,
                "volume": 1.0,
                "muted": False,
                "locked": False,
            }
        )

    duration_seconds = max(
        tts._probe_duration(source_video_path),
        max((float(cue["end"]) for cue in tts_tracks), default=0.0),
    )
    manifest = {
        "job_id": job_id,
        "source_video_url": _storage_url(source_video_path),
        "translated_subtitle_url": _storage_url(translated_subtitle_path),
        "background_audio_url": None,
        "duration_seconds": round(duration_seconds, 3),
        "tracks": {
            "tts": tts_tracks,
            "background": {
                "audio_url": None,
                "volume": _background_gain(audio_mix),
            },
        },
    }
    return save_audio_cue_manifest(job_id, manifest)


def _create_omnivoice_audio_cue_manifest(
    job_id: str,
    source_video_path: Path,
    translated_subtitle_path: Path,
    selected_voice: str,
    selected_rate: str,
    cue_dir: Path,
    speech_rate: SpeechRateProfile | None,
    voice_overrides: tts.VoiceOverrides | None,
    audio_mix: dict[str, Any] | None,
) -> Path:
    cue_outputs, metadata = tts.generate_omnivoice_srt_cues(
        translated_subtitle_path,
        selected_voice,
        selected_rate,
        cue_dir,
        speech_rate=speech_rate,
        voice_overrides=voice_overrides,
    )
    if not cue_outputs:
        raise RuntimeError("OmniVoice did not return any audio cues for manual audio review.")

    tts_tracks: list[dict[str, Any]] = []
    for cue in cue_outputs:
        audio_path = cue.get("audio_path")
        if not isinstance(audio_path, Path):
            raise RuntimeError(f"OmniVoice cue {cue.get('id')} did not include a local audio path.")
        start = round(float(cue.get("start") or 0.0), 3)
        target_end = round(float(cue.get("end") or start), 3)
        duration = max(0.05, float(cue.get("duration_seconds") or tts._probe_duration(audio_path) or 0.0))
        tts_tracks.append(
            {
                "id": str(cue.get("id") or f"cue-{len(tts_tracks) + 1:04d}"),
                "index": int(cue.get("index") or len(tts_tracks) + 1),
                "start": start,
                "end": round(start + duration, 3),
                "suggested_start": start,
                "suggested_end": target_end,
                "duration": round(duration, 3),
                "target_duration": round(float(cue.get("target_duration_seconds") or max(0.05, target_end - start)), 3),
                "audio_url": _storage_url(audio_path),
                "text": str(cue.get("text") or ""),
                "volume": 1.0,
                "muted": False,
                "locked": False,
            }
        )

    duration_seconds = max(
        tts._probe_duration(source_video_path),
        max((float(cue["end"]) for cue in tts_tracks), default=0.0),
    )
    manifest = {
        "job_id": job_id,
        "source_video_url": _storage_url(source_video_path),
        "translated_subtitle_url": _storage_url(translated_subtitle_path),
        "background_audio_url": None,
        "target_audio_preview_url": None,
        "duration_seconds": round(duration_seconds, 3),
        "tracks": {
            "tts": tts_tracks,
            "background": {
                "audio_url": None,
                "volume": _background_gain(audio_mix),
            },
        },
        "tts_cue_source": metadata,
    }
    return save_audio_cue_manifest(job_id, manifest)


def attach_background_audio(job_id: str, background_audio_path: Path | None) -> Path:
    manifest = load_audio_cue_manifest(job_id)
    url = _storage_url(background_audio_path) if background_audio_path else None
    manifest["background_audio_url"] = url
    background = manifest.setdefault("tracks", {}).setdefault("background", {})
    background["audio_url"] = url
    background.setdefault("volume", background_volume())
    return save_audio_cue_manifest(job_id, manifest)


def load_audio_cue_manifest(job_id: str) -> dict[str, Any]:
    path = audio_cue_manifest_path(job_id)
    if not path.exists():
        raise FileNotFoundError(f"Audio cue manifest does not exist for job {job_id}.")
    return json.loads(path.read_text(encoding="utf-8"))


def save_audio_cue_manifest(job_id: str, manifest: dict[str, Any]) -> Path:
    path = audio_cue_manifest_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def audio_cue_manifest_path(job_id: str) -> Path:
    return ensure_storage() / "audio-cues" / f"{job_id}.cue-manifest.json"


def patch_audio_cue_manifest(
    job_id: str,
    cue_updates: list[dict[str, Any]],
    background_volume_value: float | None = None,
) -> dict[str, Any]:
    manifest = load_audio_cue_manifest(job_id)
    tts_track = manifest.get("tracks", {}).get("tts", [])
    by_id = {str(cue.get("id")): cue for cue in tts_track}

    for update in cue_updates:
        cue = by_id.get(str(update.get("id")))
        if not cue:
            continue
        start = max(0.0, float(update.get("start", cue.get("start", 0.0))))
        duration = max(0.05, float(cue.get("duration") or (float(cue.get("end", 0.0)) - float(cue.get("start", 0.0))) or 0.05))
        cue["start"] = round(start, 3)
        cue["end"] = round(start + duration, 3)
        if update.get("volume") is not None:
            cue["volume"] = max(0.0, min(2.0, float(update["volume"])))
        if update.get("muted") is not None:
            cue["muted"] = bool(update["muted"])
        if update.get("locked") is not None:
            cue["locked"] = bool(update["locked"])

    if background_volume_value is not None:
        background = manifest.setdefault("tracks", {}).setdefault("background", {})
        background["volume"] = max(0.0, min(1.0, float(background_volume_value)))

    save_audio_cue_manifest(job_id, manifest)
    return manifest


def render_audio_cue_manifest(
    job_id: str,
    render_quality: str | None = None,
    *,
    subtitle_style: dict[str, Any] | None = None,
    audio_mix: dict[str, Any] | None = None,
    aspect_ratio: str = "source",
    include_subtitles: bool = True,
    max_lines: int = 2,
) -> Path:
    manifest = load_audio_cue_manifest(job_id)
    mixed_tts = mix_audio_cues(job_id, manifest)
    source_video = _path_from_storage_url(manifest.get("source_video_url"))
    subtitle = _path_from_storage_url(manifest.get("translated_subtitle_url"))
    background = _path_from_storage_url(manifest.get("background_audio_url"))
    if not source_video or not source_video.exists():
        raise RuntimeError("Source video for manual cue render is missing.")
    if include_subtitles and (not subtitle or not subtitle.exists()):
        raise RuntimeError("Translated subtitle for manual cue render is missing.")
    return render_video(
        source_video,
        mixed_tts,
        subtitle if include_subtitles else None,
        background,
        render_quality,
        subtitle_style=subtitle_style,
        audio_mix=audio_mix,
        aspect_ratio=aspect_ratio,
        max_lines=max_lines,
    )


def _background_gain(audio_mix: dict[str, Any] | None) -> float:
    if not audio_mix:
        return background_volume()
    try:
        return max(0.0, min(1.0, float(audio_mix.get("original_volume", 0.0)) / 100.0))
    except (TypeError, ValueError):
        return background_volume()


def preview_audio_cue_manifest(job_id: str) -> dict[str, Any]:
    manifest = load_audio_cue_manifest(job_id)
    mixed_tts = mix_audio_cues(job_id, manifest)
    preview_url = _storage_url(mixed_tts)
    manifest["target_audio_preview_url"] = f"{preview_url}?v={int(mixed_tts.stat().st_mtime)}" if preview_url else None
    save_audio_cue_manifest(job_id, manifest)
    return manifest


def mix_audio_cues(job_id: str, manifest: dict[str, Any]) -> Path:
    root = ensure_storage()
    output = root / "audio" / f"{job_id}.manual-cues.mp3"
    filter_file = root / "audio-cues" / f"{job_id}.manual-filter.txt"
    duration = max(0.25, float(manifest.get("duration_seconds") or 0.0))
    cue_inputs: list[tuple[dict[str, Any], Path]] = []
    for cue in manifest.get("tracks", {}).get("tts", []):
        if cue.get("muted"):
            continue
        path = _path_from_storage_url(cue.get("audio_url"))
        if path and path.exists():
            cue_inputs.append((cue, path))

    if not cue_inputs:
        _create_silence(output, duration)
        return output

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
    for _cue, path in cue_inputs:
        command.extend(["-i", str(path)])

    filter_file.write_text(_manual_timeline_filter(cue_inputs, duration), encoding="utf-8")
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
    timeout = max(300, int(duration * 6 + 120))
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to mix manual cue audio: {result.stderr[-1000:]}")
    return output


def _manual_timeline_filter(cue_inputs: list[tuple[dict[str, Any], Path]], duration: float) -> str:
    lines = ["[0:a]volume=0[base]"]
    labels = ["[base]"]
    for input_index, (cue, _path) in enumerate(cue_inputs, start=1):
        start = max(0.0, float(cue.get("start") or 0.0))
        cue_duration = max(0.05, float(cue.get("duration") or (float(cue.get("end", start)) - start) or 0.05))
        volume = max(0.0, min(2.0, float(cue.get("volume", 1.0))))
        delay_ms = max(0, round(start * 1000))
        label = f"cue{input_index}"
        lines.append(
            f"[{input_index}:a]"
            f"atrim=0:{cue_duration:.3f},"
            "asetpts=PTS-STARTPTS,"
            f"volume={volume:.3f},"
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


def _create_silence(output: Path, duration: float) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=24000:cl=stereo:d={duration:.3f}",
        "-c:a",
        "libmp3lame",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Unable to create silent manual cue audio: {result.stderr[-1000:]}")


def _storage_url(path: Path | None) -> str | None:
    if not path:
        return None
    root = ensure_storage().resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return str(path)
    return "/storage/" + relative.as_posix()


def _path_from_storage_url(value: object) -> Path | None:
    if not value:
        return None
    raw = str(value)
    prefix = "/storage/"
    if raw.startswith(prefix):
        return ensure_storage() / raw[len(prefix) :]
    path = Path(raw)
    if path.is_absolute():
        return path
    return ensure_storage() / path
