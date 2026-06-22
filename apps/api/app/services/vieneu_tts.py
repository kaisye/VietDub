"""VieNeu-TTS: offline, CPU, Apache-2.0 Vietnamese TTS engine.

Wraps the `vieneu` package as a third TTS provider alongside Edge and OmniVoice.
Uses the model's built-in **preset voices** (codes are precomputed in the model's
voices.json), so synthesis runs entirely on CPU via a GGUF backbone + ONNX codec
with **no torch dependency** — verified by blocking `import torch` at runtime.

The engine is loaded once (lazily) and cached; the model is downloaded to the
app's storage dir on first use. Voice cloning is intentionally NOT exposed here:
encoding a new reference requires the heavier torch codec, so cloning stays on the
GPU OmniVoice path.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import unicodedata
from pathlib import Path

from .storage import ensure_storage

logger = logging.getLogger(__name__)


# (preset_id, display_name, accent) — ids MUST match the installed VieNeu model's
# voices.json exactly. These are the built-in preset voices of VieNeu-TTS v3 Turbo,
# the default model in vieneu >= 3.x, keyed by their full Vietnamese names. (The old
# v2 short codes like "Tuyen"/"Vinh"/"Ly"/"Sơn" no longer exist in v3, which is why
# every VieNeu cue failed with "Voice 'Tuyen' not found".)
VIENEU_PRESETS: list[tuple[str, str, str]] = [
    ("Ngọc Linh", "Ngọc Linh", "Nữ · giọng tươi sáng"),
    ("Ngọc Lan", "Ngọc Lan", "Nữ · giọng dịu dàng"),
    ("Mỹ Duyên", "Mỹ Duyên", "Nữ · giọng mượt mà"),
    ("Trúc Ly", "Trúc Ly", "Nữ · giọng trẻ trung"),
    ("Gia Bảo", "Gia Bảo", "Nam · giọng mượt mà"),
    ("Thái Sơn", "Thái Sơn", "Nam · giọng chắc khỏe"),
    ("Đức Trí", "Đức Trí", "Nam · giọng rõ ràng"),
    ("Xuân Vĩnh", "Xuân Vĩnh", "Nam · giọng vui tươi"),
    ("Trọng Hữu", "Trọng Hữu", "Nam · giọng uyên bác"),
    ("Bình An", "Bình An", "Nam · giọng điềm đạm"),
]
VIENEU_VOICE_IDS = {preset[0] for preset in VIENEU_PRESETS}

_lock = threading.Lock()
_engine = None


def is_vieneu_voice(voice_id: str) -> bool:
    return (voice_id or "").strip() in VIENEU_VOICE_IDS


def _vieneu_temperature() -> float:
    # VieNeu-TTS v3 Turbo's tuned default is 0.8. (The old 1.0 default was tuned for
    # the v2 GGUF backbone, where lowering it too far made the autoregressive model
    # emit the END token early and read only part of the line — "đọc không đủ chữ".)
    try:
        return max(0.05, min(1.5, float(os.getenv("AETHER_VIENEU_TEMPERATURE", "0.8"))))
    except (TypeError, ValueError):
        return 0.8


def _vieneu_top_k() -> int:
    # v3 Turbo's tuned default is 25.
    try:
        return max(1, min(100, int(os.getenv("AETHER_VIENEU_TOP_K", "25"))))
    except (TypeError, ValueError):
        return 25


def _vieneu_max_context() -> int:
    # The library hard-codes 2048, but the model is trained for 4096. 2048 caps
    # both the GGUF context window AND the per-chunk generation length, which
    # truncates longer lines mid-sentence ("reads only part of the text").
    try:
        return max(2048, min(4096, int(os.getenv("AETHER_VIENEU_MAX_CONTEXT", "4096"))))
    except (TypeError, ValueError):
        return 4096


def _patch_vieneu_max_context() -> None:
    """Raise VieNeu's context window to the model's trained size before load.

    n_ctx is fixed when the llama.cpp backbone is created inside __init__, so the
    bump must happen on the base __init__ (which sets max_context=2048) before the
    subclass builds the backbone.
    """
    try:
        from vieneu.base import BaseVieneuTTS
    except Exception:  # noqa: BLE001
        logger.warning("Could not patch VieNeu max_context; long lines may truncate.")
        return
    if getattr(BaseVieneuTTS, "_aether_ctx_patched", False):
        return
    _orig_init = BaseVieneuTTS.__init__
    target = _vieneu_max_context()

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        self.max_context = target

    BaseVieneuTTS.__init__ = _patched_init
    BaseVieneuTTS._aether_ctx_patched = True
    logger.info("VieNeu max_context raised to %d (was 2048).", target)


def preview_asset_stem(voice_id: str) -> str:
    """ASCII-safe filename stem for a bundled preview clip (e.g. 'Thái Sơn' -> 'vieneu_ThaiSon')."""
    norm = unicodedata.normalize("NFKD", voice_id or "")
    ascii_id = "".join(c for c in norm if c.isascii() and (c.isalnum() or c in {"-", "_"}))
    return f"vieneu_{ascii_id or 'voice'}"


def bundled_preview_path(voice_id: str) -> Path | None:
    """Return the pre-rendered demo clip shipped with the app, if present.

    Lets the UI audition a VieNeu voice instantly without downloading the ~0.5 GB
    model — the model is only fetched when the user actually renders.
    """
    assets = Path(__file__).resolve().parent.parent / "assets" / "voice-previews"
    candidate = assets / f"{preview_asset_stem(voice_id)}.mp3"
    return candidate if candidate.exists() else None


def _cache_dir() -> Path:
    path = ensure_storage() / "vieneu-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        # vieneu / huggingface_hub read these at import / first use. Keep the model
        # download inside the app's storage dir and tolerate the duplicated Intel
        # OpenMP runtime that MKL + onnxruntime/llama.cpp both pull in on Windows.
        os.environ.setdefault("HF_HOME", str(_cache_dir()))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        try:
            from vieneu import Vieneu
        except Exception as exc:  # noqa: BLE001 - surface a clear actionable error
            raise RuntimeError(
                "VieNeu TTS is not installed. Install it with `pip install vieneu`."
            ) from exc
        _patch_vieneu_max_context()
        logger.info("Loading VieNeu TTS engine (CPU / ONNX, torch-free)…")
        _engine = Vieneu()
        logger.info("VieNeu TTS engine ready.")
        return _engine


def synthesize_vieneu(text: str, voice_id: str, output: Path) -> Path:
    """Synthesize ``text`` with a VieNeu preset voice into ``output`` (.wav or .mp3)."""
    voice_id = (voice_id or "").strip()
    engine = _get_engine()
    try:
        voice = engine.get_preset_voice(voice_id)  # precomputed codes -> no torch
    except ValueError as exc:
        # The installed model may not expose this preset id (e.g. the vieneu library
        # swapped its bundled model and voice names across versions). Degrade to the
        # model's own default voice instead of failing every cue.
        logger.warning(
            "VieNeu preset %r unavailable (%s); falling back to the model's default voice.",
            voice_id,
            exc,
        )
        voice = engine.get_preset_voice(None)
    audio = engine.infer(
        text, voice=voice, temperature=_vieneu_temperature(), top_k=_vieneu_top_k()
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".wav":
        engine.save(audio, str(output))
        return output

    # The chunked pipeline works in mp3; transcode VieNeu's wav like OmniVoice does.
    temp_wav = output.with_suffix(".vieneu.wav")
    engine.save(audio, str(temp_wav))
    command = ["ffmpeg", "-y", "-i", str(temp_wav), "-c:a", "libmp3lame", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    temp_wav.unlink(missing_ok=True)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to convert VieNeu audio: {result.stderr[-1000:]}")
    return output
