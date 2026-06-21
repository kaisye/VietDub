from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .storage import ensure_storage


@dataclass(frozen=True)
class AudioBedResult:
    path: Path | None
    mode: str
    message: str


def prepare_background_audio(
    video_path: Path,
    *,
    preserve_source_audio: bool = False,
) -> AudioBedResult:
    """Prepare a background audio bed from the source video.

    The preferred path uses Demucs to remove vocals and keep music/effects.
    In hybrid mode, failures fall back to the original audio at low volume so
    the output still has a usable sound bed.
    """
    mode = _audio_bed_mode()
    if preserve_source_audio:
        if not _has_audio_stream(video_path):
            return AudioBedResult(path=None, mode="source", message="No source audio stream found.")
        source_audio = _extract_source_audio(video_path)
        return AudioBedResult(
            path=source_audio,
            mode="source",
            message="Voice generation is disabled; preserving the source audio track.",
        )
    if mode == "off":
        return AudioBedResult(path=None, mode=mode, message="Background sound is disabled.")

    if not _has_audio_stream(video_path):
        return AudioBedResult(path=None, mode=mode, message="No source audio stream found.")

    source_audio = _extract_source_audio(video_path)
    if mode == "mix_original":
        return AudioBedResult(path=source_audio, mode=mode, message="Background audio extracted from source video.")

    if not _demucs_available():
        if mode == "separate":
            raise RuntimeError("AETHER_AUDIO_BED_MODE=separate requires Demucs. Install it with: pip install demucs")
        return AudioBedResult(
            path=source_audio,
            mode="mix_original",
            message="Vocal separation unavailable, using low-volume source audio.",
        )

    try:
        no_vocals = _separate_vocals(source_audio, video_path.stem)
        return AudioBedResult(path=no_vocals, mode="separate", message="Vocal separation completed.")
    except Exception as exc:
        if mode == "separate":
            raise
        return AudioBedResult(
            path=source_audio,
            mode="mix_original",
            message=f"Vocal separation failed, using low-volume source audio. Reason: {exc}",
        )


def background_volume() -> float:
    return _env_float("AETHER_BACKGROUND_VOLUME", 0.22, minimum=0.0, maximum=1.0)


def tts_volume() -> float:
    return _env_float("AETHER_TTS_VOLUME", 1.0, minimum=0.0, maximum=2.0)


def _audio_bed_mode() -> str:
    value = os.getenv("AETHER_AUDIO_BED_MODE", "hybrid").strip().lower()
    aliases = {"none": "off", "disabled": "off", "original": "mix_original"}
    value = aliases.get(value, value)
    if value not in {"hybrid", "separate", "mix_original", "off"}:
        return "hybrid"
    return value


def _has_audio_stream(video_path: Path) -> bool:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    return result.returncode == 0 and "audio" in result.stdout


def _extract_source_audio(video_path: Path) -> Path:
    root = ensure_storage()
    output = root / "audio-bed" / f"{video_path.stem}.source.wav"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to extract source audio: {result.stderr[-1000:]}")
    return output


def _demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def _separate_vocals(source_audio: Path, job_stem: str) -> Path:
    root = ensure_storage()
    model = os.getenv("AETHER_AUDIO_SEPARATION_MODEL", "htdemucs").strip() or "htdemucs"
    timeout = _env_int("AETHER_AUDIO_SEPARATION_TIMEOUT_SECONDS", 1800, minimum=60, maximum=7200)
    separation_root = root / "audio-bed" / f"{job_stem}.demucs"
    if separation_root.exists():
        shutil.rmtree(separation_root)
    separation_root.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "demucs",
        "--two-stems",
        "vocals",
        "-n",
        model,
        "-o",
        str(separation_root),
        str(source_audio),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Demucs failed: {result.stderr[-1000:] or result.stdout[-1000:]}")

    candidates = sorted(
        separation_root.glob(f"**/{source_audio.stem}/no_vocals.*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        candidates = sorted(separation_root.glob("**/no_vocals.*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("Demucs completed without producing no_vocals audio.")

    output = root / "audio-bed" / f"{job_stem}.no_vocals.wav"
    shutil.copyfile(candidates[0], output)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("Separated background audio is empty.")
    return output


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
