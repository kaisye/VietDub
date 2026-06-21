from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx

from .runtime_settings import get_runtime_settings
from .storage import ensure_storage


def diarize_video(
    job_id: str,
    video_path: Path,
    transcript_path: Path,
    *,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> Path:
    audio_path = ensure_storage() / "diarization" / f"{job_id}.16k.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    _extract_mono_audio(video_path, audio_path)

    settings = get_runtime_settings()
    payload = {
        "audio_path": str(audio_path.resolve()),
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
    }
    try:
        response = httpx.post(
            f"{settings.diarization_api_url.rstrip('/')}/diarize",
            json=payload,
            timeout=float(60 * 60),
        )
        response.raise_for_status()
        diarization = response.json()
    except Exception as exc:
        raise RuntimeError(f"Local speaker diarization request failed: {exc}") from exc

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    spans = transcript.get("spans") if isinstance(transcript, dict) else []
    turns = diarization.get("turns") if isinstance(diarization, dict) else []
    overlap_turns = diarization.get("overlap_turns") if isinstance(diarization, dict) else []
    for span in spans if isinstance(spans, list) else []:
        span_start = float(span.get("start") or 0.0)
        span_end = float(span.get("end") or 0.0)
        speaker_id, overlap_seconds = speaker_for_interval(
            span_start,
            span_end,
            turns if isinstance(turns, list) else [],
        )
        span["speaker_id"] = speaker_id
        span["speaker_overlap_seconds"] = round(overlap_seconds, 3)
        span_duration = max(0.001, span_end - span_start)
        span["speaker_assignment_quality"] = round(min(1.0, overlap_seconds / span_duration), 3)
        span["speaker_overlap"] = interval_has_overlap(
            span_start,
            span_end,
            overlap_turns if isinstance(overlap_turns, list) else [],
        )

    transcript["diarization_model"] = diarization.get("model")
    transcript["diarization_device"] = diarization.get("device")
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")

    artifact_path = ensure_storage() / "diarization" / f"{job_id}.speakers.json"
    artifact = {
        **diarization,
        "job_id": job_id,
        "video_path": str(video_path.resolve()),
        "audio_path": str(audio_path.resolve()),
        "transcript_path": str(transcript_path.resolve()),
    }
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact_path


def load_diarization(job_id: str) -> dict[str, Any]:
    path = diarization_path(job_id)
    if not path.exists():
        raise FileNotFoundError(f"Speaker diarization artifact does not exist for job {job_id}.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Speaker diarization artifact is invalid.")
    return data


def diarization_path(job_id: str) -> Path:
    return ensure_storage() / "diarization" / f"{job_id}.speakers.json"


def speaker_for_interval(start: float, end: float, turns: list[dict[str, Any]]) -> tuple[str | None, float]:
    best_speaker: str | None = None
    best_overlap = 0.0
    totals: dict[str, float] = {}
    for turn in turns:
        turn_start = float(turn.get("start") or 0.0)
        turn_end = float(turn.get("end") or 0.0)
        overlap = max(0.0, min(end, turn_end) - max(start, turn_start))
        if overlap <= 0:
            continue
        speaker_id = str(turn.get("speaker_id") or "")
        if not speaker_id:
            continue
        totals[speaker_id] = totals.get(speaker_id, 0.0) + overlap
    if totals:
        best_speaker, best_overlap = max(totals.items(), key=lambda item: item[1])
    return best_speaker, best_overlap


def speaker_map_for_subtitle(job_id: str) -> list[dict[str, Any]]:
    data = load_diarization(job_id)
    turns = data.get("turns")
    return turns if isinstance(turns, list) else []


def interval_has_overlap(start: float, end: float, overlap_turns: list[dict[str, Any]]) -> bool:
    return any(
        max(0.0, min(end, float(turn.get("end") or 0.0)) - max(start, float(turn.get("start") or 0.0))) > 0
        for turn in overlap_turns
    )


def _extract_mono_audio(video_path: Path, output_path: Path) -> None:
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
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Unable to extract audio for speaker diarization: {result.stderr[-1200:]}")
