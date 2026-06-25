"""NGHI-TTS: offline, CPU Vietnamese TTS via Piper ONNX models.

Voices are standard Piper (espeak ``vi``) models published by nghimestudio at
https://nghitts.app. Each model is downloaded on demand and cached locally;
synthesis then runs torch-free on the CPU through the ``piper-tts`` package.
Vietnamese text is normalized with ``vietnormalizer`` before synthesis so numbers,
dates, units and acronyms are spoken correctly. NGHI-TTS is a third TTS provider
alongside Edge (cloud) and OmniVoice (GPU clone/design).
"""

from __future__ import annotations

import logging
import subprocess
import urllib.parse
import wave
from pathlib import Path
from threading import Lock

import requests

from .storage import ensure_storage

logger = logging.getLogger(__name__)

# (internal_id, nghitts model name, display_name).
# - internal_id: stable, ASCII/filename-safe id used everywhere in VietDub.
# - nghitts model name: MUST match nghitts.app exactly — it is the R2 object key
#   under piper/vi/ and is URL-encoded into the download path. An unpinned change
#   on nghitts.app could rename a model and break the download for that voice.
NGHITTS_PRESETS: list[tuple[str, str, str]] = [
    ("nghitts_ngochuyen", "Ngọc Huyền (mới)", "Ngọc Huyền"),
    ("nghitts_duyoryx", "Duy Oryx", "Duy Oryx"),
    ("nghitts_manhdung", "Mạnh Dũng", "Mạnh Dũng"),
    ("nghitts_minhquang", "Minh Quang", "Minh Quang"),
    ("nghitts_thanhphuong", "Thanh Phương Viettel", "Thanh Phương"),
    ("nghitts_adam", "adam", "Adam"),
]

NGHITTS_VOICE_IDS = {preset[0] for preset in NGHITTS_PRESETS}
_MODEL_NAME_BY_ID = {preset[0]: preset[1] for preset in NGHITTS_PRESETS}

NGHITTS_BASE_URL = "https://nghitts.app/api/model/piper/vi"
# Cloudflare in front of nghitts.app rejects the default Python/requests/httpx
# User-Agent with HTTP 403; send a browser-like UA so model downloads succeed.
_DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VietDub/1.0"
}
_DOWNLOAD_TIMEOUT = 180

_VOICE_CACHE: dict[str, object] = {}
_VOICE_LOCK = Lock()
_NORMALIZER = None
_NORMALIZER_LOCK = Lock()


def is_nghitts_voice(voice_id: str) -> bool:
    return (voice_id or "").strip() in NGHITTS_VOICE_IDS


def _model_name(voice_id: str) -> str:
    name = _MODEL_NAME_BY_ID.get((voice_id or "").strip())
    if not name:
        raise RuntimeError(f"Unknown NGHI-TTS voice '{voice_id}'.")
    return name


def _cache_dir() -> Path:
    path = ensure_storage() / "nghitts-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(
        url, headers=_DOWNLOAD_HEADERS, stream=True, timeout=_DOWNLOAD_TIMEOUT
    ) as response:
        response.raise_for_status()
        with open(tmp, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                if chunk:
                    handle.write(chunk)
    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded an empty file from {url}")
    tmp.replace(dest)


def ensure_model(voice_id: str) -> tuple[Path, Path]:
    """Download (if needed) and return ``(onnx_path, config_path)`` for a voice."""
    voice_id = (voice_id or "").strip()
    model_name = _model_name(voice_id)
    cache = _cache_dir()
    onnx = cache / f"{voice_id}.onnx"
    cfg = cache / f"{voice_id}.onnx.json"
    encoded = urllib.parse.quote(model_name)
    if not onnx.exists() or onnx.stat().st_size == 0:
        logger.info("Downloading NGHI-TTS model '%s'…", model_name)
        _download(f"{NGHITTS_BASE_URL}/{encoded}.onnx", onnx)
    if not cfg.exists() or cfg.stat().st_size == 0:
        _download(f"{NGHITTS_BASE_URL}/{encoded}.onnx.json", cfg)
    return onnx, cfg


def _normalizer():
    global _NORMALIZER
    if _NORMALIZER is None:
        with _NORMALIZER_LOCK:
            if _NORMALIZER is None:
                from vietnormalizer import VietnameseNormalizer

                _NORMALIZER = VietnameseNormalizer()
    return _NORMALIZER


def _normalize(text: str) -> str:
    try:
        return _normalizer().normalize(text)
    except Exception:
        logger.warning("Vietnamese normalization failed; using raw text.", exc_info=True)
        return text


def _load_voice(voice_id: str):
    voice = _VOICE_CACHE.get(voice_id)
    if voice is not None:
        return voice
    with _VOICE_LOCK:
        voice = _VOICE_CACHE.get(voice_id)
        if voice is not None:
            return voice
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise RuntimeError(
                "NGHI-TTS engine is not installed. Install it with `pip install piper-tts`."
            ) from exc
        onnx, cfg = ensure_model(voice_id)
        logger.info("Loading NGHI-TTS voice '%s' (CPU / Piper ONNX, torch-free)…", voice_id)
        voice = PiperVoice.load(str(onnx), config_path=str(cfg), use_cuda=False)
        _VOICE_CACHE[voice_id] = voice
        return voice


def synthesize_nghitts(text: str, voice_id: str, output: Path) -> Path:
    """Synthesize ``text`` with a NGHI-TTS preset voice into ``output`` (.wav or .mp3)."""
    voice = _load_voice(voice_id)
    cleaned = _normalize(text)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Piper writes a 22050 Hz int16 mono WAV. The chunked pipeline works in mp3, so
    # synthesize to a temp wav and transcode like OmniVoice does — unless the caller
    # asked for a wav directly (the timeline path renders raw wav per cue).
    if output.suffix.lower() == ".wav":
        with wave.open(str(output), "wb") as wav_file:
            voice.synthesize_wav(cleaned, wav_file)
        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("NGHI-TTS produced an empty audio file.")
        return output

    temp_wav = output.with_suffix(".nghitts.wav")
    with wave.open(str(temp_wav), "wb") as wav_file:
        voice.synthesize_wav(cleaned, wav_file)
    command = ["ffmpeg", "-y", "-i", str(temp_wav), "-c:a", "libmp3lame", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    temp_wav.unlink(missing_ok=True)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to convert NGHI-TTS audio: {result.stderr[-1000:]}")
    return output


# Bundled preview clips let the UI audition a NGHI-TTS voice instantly without
# downloading the ~60 MB model. Voice ids are already ASCII/filename-safe.
_PREVIEW_DIR = Path(__file__).resolve().parent.parent / "assets" / "voice-previews"


def preview_asset_stem(voice_id: str) -> str:
    return (voice_id or "").strip() or "voice"


def bundled_preview_path(voice_id: str) -> Path | None:
    path = _PREVIEW_DIR / f"{preview_asset_stem(voice_id)}.mp3"
    return path if path.exists() and path.stat().st_size > 0 else None
