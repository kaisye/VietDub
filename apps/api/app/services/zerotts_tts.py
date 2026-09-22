"""ZeroTTS: local CPU Vietnamese TTS using the official ONNX runtime.

The model and its eight bundled voice packs are downloaded from Hugging Face on
first use and cached under VietDub's storage directory.  Synthesis is serialized
because the ZeroTTS runtime keeps mutable ONNX/codec state per model instance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

import httpx

from .storage import ensure_storage

logger = logging.getLogger(__name__)

ZEROTTS_MODEL_ID = "zeroweight-ai/ZeroTTS"

# (VietDub id, upstream voice-pack id, display name, short description).
ZEROTTS_PRESETS: list[tuple[str, str, str, str]] = [
    ("zerotts_maichi", "maichi", "Mai Chi", "Nữ trẻ · kể chuyện · nhẹ nhàng"),
    ("zerotts_baotrang", "baotrang", "Bảo Trang", "Nữ trưởng thành · tin tức · rõ ràng"),
    ("zerotts_kimoanh", "kimoanh", "Kim Oanh", "Nữ trung niên · kể chuyện · ấm áp"),
    ("zerotts_hamy", "hamy", "Hà My", "Nữ trẻ · hoạt hình · biểu cảm"),
    ("zerotts_giahuy", "giahuy", "Gia Huy", "Nam trẻ · kể chuyện · trầm ấm"),
    ("zerotts_huuduc", "huuduc", "Hữu Đức", "Nam lớn tuổi · kể chuyện · điềm đạm"),
    ("zerotts_quangminh", "quangminh", "Quang Minh", "Nam trẻ · tin tức · dứt khoát"),
    ("zerotts_tiendat", "tiendat", "Tiến Đạt", "Nam trẻ · bình luận · năng lượng"),
]

ZEROTTS_VOICE_IDS = {preset[0] for preset in ZEROTTS_PRESETS}
_UPSTREAM_VOICE_BY_ID = {preset[0]: preset[1] for preset in ZEROTTS_PRESETS}
_COMMUNITY_ID_PREFIX = "zerotts_community_"
_MAX_VOICE_ARCHIVE_BYTES = 16 * 1024 * 1024
_MAX_VOICE_PACK_BYTES = 16 * 1024 * 1024
_COMMUNITY_API_URL = (
    "https://api-prod.zeroweight.ai/api/v1/audio/voices/public?type=community"
)
# Keep VietDub's recommended ZeroTTS voices at the top of the public catalogue.
# IDs are used instead of display names because the upstream catalogue can
# contain multiple voices with the same name (it currently has two Ngọc Huyền
# entries).
ZEROTTS_FEATURED_COMMUNITY_VOICE_IDS: tuple[str, ...] = (
    "7owuK1LaOPOaQjeSzmQ4",  # Ngọc Huyền — kể truyện, review phim
    "MAJfDVfQqPF5Fr4DvibN",  # Thức Dậy Đi
)

_MODEL = None
_MODEL_LOCK = Lock()
_SYNTHESIS_LOCK = Lock()


@dataclass(frozen=True)
class ZeroTTSCommunityVoice:
    id: str
    pack_name: str
    display_name: str
    description: str
    language: str
    gender: str
    tags: tuple[str, ...]
    preview_path: str


def _community_voice_root() -> Path:
    root = ensure_storage() / "zerotts-voices"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _configure_voice_home():
    root = _community_voice_root().resolve()
    os.environ["ZEROTTS_VOICES_HOME"] = str(root)
    import zerotts

    # ZeroTTS reads this setting when its voices module is first imported. The
    # assignment also handles a host process that imported zerotts beforehand.
    from zerotts import voices as zerotts_voices

    zerotts_voices.USER_VOICES_DIR = root
    return zerotts


def _community_voice_id(pack_name: str) -> str:
    digest = hashlib.sha256(pack_name.encode("utf-8")).hexdigest()[:16]
    return f"{_COMMUNITY_ID_PREFIX}{digest}"


def list_zerotts_community_voices() -> list[ZeroTTSCommunityVoice]:
    voices: list[ZeroTTSCommunityVoice] = []
    for pack_dir in sorted(_community_voice_root().iterdir()):
        if not pack_dir.is_dir() or not (pack_dir / "voice.npz").is_file():
            continue
        meta: dict = {}
        try:
            loaded = json.loads((pack_dir / "meta.json").read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        raw_tags = meta.get("tags")
        tags = (
            tuple(str(tag).strip() for tag in raw_tags if str(tag).strip())
            if isinstance(raw_tags, list)
            else ()
        )
        description = str(meta.get("description") or "").strip()
        gender = str(meta.get("gender") or "").strip()
        if not description:
            description = " · ".join((*tags, gender) if gender and gender not in tags else tags)
        display_name = str(meta.get("display_name") or "").strip() or pack_dir.name
        language = str(meta.get("language") or "").strip() or "vi"
        preview = pack_dir / "preview.wav"
        voices.append(
            ZeroTTSCommunityVoice(
                id=_community_voice_id(pack_dir.name),
                pack_name=pack_dir.name,
                display_name=display_name,
                description=description,
                language=language,
                gender=gender,
                tags=tags,
                preview_path=str(preview.resolve()) if preview.is_file() else "",
            )
        )
    return voices


def _community_voice_by_id(voice_id: str) -> ZeroTTSCommunityVoice | None:
    if not (voice_id or "").startswith(_COMMUNITY_ID_PREFIX):
        return None
    return next(
        (voice for voice in list_zerotts_community_voices() if voice.id == voice_id),
        None,
    )


def is_zerotts_voice(voice_id: str) -> bool:
    normalized = (voice_id or "").strip()
    return normalized in ZEROTTS_VOICE_IDS or _community_voice_by_id(normalized) is not None


def _upstream_voice_id(voice_id: str) -> str:
    normalized = (voice_id or "").strip()
    upstream = _UPSTREAM_VOICE_BY_ID.get(normalized)
    community = _community_voice_by_id(normalized)
    if community:
        upstream = community.pack_name
    if not upstream:
        raise RuntimeError(f"Unknown ZeroTTS voice '{voice_id}'.")
    return upstream


def _validate_voice_pack(pack_dir: Path) -> None:
    import numpy as np

    npz_path = pack_dir / "voice.npz"
    if npz_path.stat().st_size > _MAX_VOICE_PACK_BYTES:
        raise ValueError(f"Voice pack '{pack_dir.name}' is too large.")
    with np.load(npz_path, allow_pickle=False) as data:
        if "voice_emb" not in data:
            raise ValueError(f"Voice pack '{pack_dir.name}' has no voice_emb array.")
        embedding = np.asarray(data["voice_emb"])
        if embedding.ndim == 2:
            embedding = embedding[None, :, :]
        if embedding.ndim != 3 or embedding.shape[0] != 1 or embedding.shape[2] != 768:
            raise ValueError(f"Voice pack '{pack_dir.name}' is not compatible with ZeroTTS.")
        stored_queries = (
            int(data["n_voice_queries"])
            if "n_voice_queries" in data
            else embedding.shape[1]
        )
        if stored_queries != embedding.shape[1] or stored_queries != 10:
            raise ValueError(f"Voice pack '{pack_dir.name}' belongs to different ZeroTTS weights.")
        if embedding.dtype.kind != "f" or not np.isfinite(embedding).all():
            raise ValueError(f"Voice pack '{pack_dir.name}' contains invalid voice data.")


def install_zerotts_community_archive(archive_path: Path) -> list[ZeroTTSCommunityVoice]:
    """Install official/community ZeroTTS voice-pack zips into VietDub storage."""
    if archive_path.stat().st_size > _MAX_VOICE_ARCHIVE_BYTES:
        raise ValueError("ZeroTTS voice archive is larger than 16 MB.")
    if not zipfile.is_zipfile(archive_path):
        raise ValueError("The selected file is not a valid ZeroTTS voice ZIP.")
    with zipfile.ZipFile(archive_path) as archive:
        unpacked_size = sum(item.file_size for item in archive.infolist())
        if unpacked_size > _MAX_VOICE_PACK_BYTES:
            raise ValueError("ZeroTTS voice archive expands beyond 16 MB.")

    zerotts = _configure_voice_home()
    with tempfile.TemporaryDirectory(prefix="vietdub-zerotts-") as temp_dir:
        staged_root = Path(temp_dir)
        installed = zerotts.install_voice_zip(archive_path, staged_root)
        if not installed:
            raise ValueError("The ZIP does not contain a ZeroTTS voice pack.")
        bundled_names = set(_UPSTREAM_VOICE_BY_ID.values())
        reserved = bundled_names.intersection(installed)
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(
                f"Community voice pack cannot replace built-in ZeroTTS voice: {names}."
            )
        for pack_dir in installed.values():
            _validate_voice_pack(Path(pack_dir))

        global _MODEL
        with _SYNTHESIS_LOCK:
            destination_root = _community_voice_root()
            destination_root.mkdir(parents=True, exist_ok=True)
            for pack_name, pack_dir in installed.items():
                destination = destination_root / pack_name
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(pack_dir, destination)
            # Existing model instances snapshot installed packs on creation.
            _MODEL = None

    installed_names = set(installed)
    return [
        voice for voice in list_zerotts_community_voices()
        if voice.pack_name in installed_names
    ]


def delete_zerotts_community_voice(voice_id: str) -> bool:
    voice = _community_voice_by_id((voice_id or "").strip())
    if not voice:
        return False
    global _MODEL
    with _SYNTHESIS_LOCK:
        shutil.rmtree(_community_voice_root() / voice.pack_name)
        _MODEL = None
    preview = ensure_storage() / "voice-previews" / f"{voice.id}.mp3"
    preview.unlink(missing_ok=True)
    return True


def fetch_zerotts_community_catalog() -> list[dict]:
    """Return the public ZeroWeight community catalogue for the desktop UI."""
    response = httpx.get(
        _COMMUNITY_API_URL,
        headers={"User-Agent": "VietDub/1.2.4"},
        timeout=12.0,
        follow_redirects=False,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("ZeroTTS community catalogue returned invalid data.")

    installed = {voice.pack_name: voice.id for voice in list_zerotts_community_voices()}
    catalog: list[dict] = []
    for item in items:
        if not isinstance(item, dict) or item.get("status") != "completed":
            continue
        voice_id = str(item.get("voice_id") or "").strip()
        name = str(item.get("name") or "").strip()
        preview_url = str(item.get("preview_url") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", voice_id) or not name:
            continue
        tags = item.get("tags")
        catalog.append(
            {
                "id": voice_id,
                "name": name,
                "description": str(item.get("description") or "").strip(),
                "tags": [str(tag).strip() for tag in tags if str(tag).strip()]
                if isinstance(tags, list)
                else [],
                "language": str(item.get("language") or "vi").strip(),
                "preview_url": preview_url if _allowed_community_url(preview_url) else "",
                "installed": voice_id in installed,
                "installed_voice_id": installed.get(voice_id, ""),
            }
        )
    featured_order = {
        voice_id: index
        for index, voice_id in enumerate(ZEROTTS_FEATURED_COMMUNITY_VOICE_IDS)
    }
    catalog.sort(key=lambda voice: featured_order.get(voice["id"], len(featured_order)))
    return catalog


def _allowed_community_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "cdn.zeroweight.ai"


def _download_community_file(url: str, limit: int) -> bytes:
    if not _allowed_community_url(url):
        raise ValueError("ZeroTTS community file URL is invalid.")
    chunks: list[bytes] = []
    total = 0
    with httpx.stream(
        "GET",
        url,
        headers={"User-Agent": "VietDub/1.2.4"},
        timeout=30.0,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > limit:
                raise ValueError("ZeroTTS community voice file is too large.")
            chunks.append(chunk)
    return b"".join(chunks)


def install_zerotts_catalog_voice(voice_id: str) -> ZeroTTSCommunityVoice:
    """Download and install one public community voice by catalogue id."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", voice_id or ""):
        raise ValueError("Invalid ZeroTTS community voice id.")

    response = httpx.get(
        _COMMUNITY_API_URL,
        headers={"User-Agent": "VietDub/1.2.4"},
        timeout=12.0,
        follow_redirects=False,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else []
    record = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("voice_id") == voice_id
            and item.get("status") == "completed"
        ),
        None,
    )
    if not record:
        raise ValueError("ZeroTTS community voice is no longer available.")

    voice_bytes = _download_community_file(str(record.get("voice_url") or ""), 1024 * 1024)
    expected_floats = 10 * 768
    if len(voice_bytes) != expected_floats * 4:
        raise ValueError("ZeroTTS community voice latents are incompatible.")

    import numpy as np

    embedding = np.frombuffer(voice_bytes, dtype="<f4").copy().reshape(1, 10, 768)
    with tempfile.TemporaryDirectory(prefix="vietdub-community-") as temp_dir:
        root = Path(temp_dir)
        pack = root / voice_id
        pack.mkdir()
        np.savez(
            pack / "voice.npz",
            n_voice_queries=np.asarray(10, dtype=np.int64),
            voice_emb=embedding,
        )
        (pack / "voice.bin").write_bytes(voice_bytes)
        tags = record.get("tags")
        (pack / "meta.json").write_text(
            json.dumps(
                {
                    "name": voice_id,
                    "display_name": str(record.get("name") or voice_id),
                    "language": str(record.get("language") or "vi"),
                    "description": str(record.get("description") or ""),
                    "tags": tags if isinstance(tags, list) else [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        preview_url = str(record.get("preview_url") or "")
        if preview_url:
            preview = _download_community_file(preview_url, 8 * 1024 * 1024)
            if preview.startswith(b"RIFF"):
                (pack / "preview.wav").write_bytes(preview)
        archive = root / "voice.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for source in pack.iterdir():
                output.write(source, f"{voice_id}/{source.name}")
        installed = install_zerotts_community_archive(archive)
    if not installed:
        raise RuntimeError("ZeroTTS community voice installation failed.")
    return installed[0]


def _cache_dir() -> Path:
    path = ensure_storage() / "zerotts-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _thread_count() -> int:
    configured = os.getenv("AETHER_ZEROTTS_THREADS", "").strip()
    if configured:
        try:
            return max(1, min(32, int(configured)))
        except ValueError:
            logger.warning("Ignoring invalid AETHER_ZEROTTS_THREADS=%r", configured)
    # ZeroTTS's small per-frame ONNX graphs spend more time coordinating ORT
    # workers than doing useful work when all 8 M1 cores are enabled. On an
    # M1 Air, 2 threads measured ~2.4 s for 4.4 s audio versus ~6 s at 8.
    if (
        sys.platform == "darwin"
        and platform.machine() == "arm64"
        and (os.cpu_count() or 0) <= 8
    ):
        return 2
    return max(1, min(8, os.cpu_count() or 4))


def _load_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            ZeroTTS = _configure_voice_home().ZeroTTS
        except ImportError as exc:
            raise RuntimeError(
                "ZeroTTS is not installed. Install it with `pip install zerotts`."
            ) from exc
        logger.info("Loading ZeroTTS from %s (CPU / ONNX)…", ZEROTTS_MODEL_ID)
        _MODEL = ZeroTTS.from_pretrained(
            ZEROTTS_MODEL_ID,
            cache_dir=str(_cache_dir()),
            intra_op_num_threads=_thread_count(),
        )
        return _MODEL


def _segments(text: str) -> list[str]:
    from zerotts import normalize_vi_text
    from zerotts.chunking import (
        chunk_text,
        clean_segment_punctuation,
        normalize_punctuation,
    )

    normalized = normalize_vi_text(text)
    normalized = normalize_punctuation(normalized)
    try:
        max_seconds = float(os.getenv("AETHER_ZEROTTS_MAX_CHUNK_SECONDS", "15"))
    except ValueError:
        max_seconds = 15.0
    max_seconds = max(3.0, min(30.0, max_seconds))
    return [
        cleaned
        for item in chunk_text(normalized, max_chunk_sec=max_seconds)
        if (cleaned := clean_segment_punctuation(item))
    ]


def _tempo_from_rate(rate: str) -> float:
    match = re.fullmatch(r"([+-])(\d{1,2})%", (rate or "").strip())
    if not match:
        return 1.0
    percent = int(match.group(2)) * (1 if match.group(1) == "+" else -1)
    return max(0.1, 1.0 + percent / 100.0)


def _atempo_filter(tempo: float) -> str:
    factors: list[float] = []
    remaining = tempo
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    factors.append(remaining)
    return ",".join(f"atempo={factor:.4f}" for factor in factors)


def synthesize_zerotts(
    text: str,
    voice_id: str,
    output: Path,
    rate: str = "+0%",
) -> Path:
    """Synthesize Vietnamese text with a ZeroTTS preset into WAV or MP3."""
    import numpy as np

    output.parent.mkdir(parents=True, exist_ok=True)
    upstream_voice = _upstream_voice_id(voice_id)
    parts = _segments(text)
    if not parts:
        raise ValueError("No speakable Vietnamese text was provided to ZeroTTS.")

    with _SYNTHESIS_LOCK:
        model = _load_model()
        audio_parts = [
            np.asarray(model.synthesize(part, voice=upstream_voice)).reshape(-1)
            for part in parts
        ]
        audio_parts = [part for part in audio_parts if part.size]
        if not audio_parts:
            raise RuntimeError("ZeroTTS produced no audio samples.")
        if len(audio_parts) == 1:
            audio = audio_parts[0]
        else:
            silence = np.zeros(int(model.sample_rate * 0.12), dtype=np.float32)
            joined: list[np.ndarray] = []
            for index, part in enumerate(audio_parts):
                if index:
                    joined.append(silence)
                joined.append(part)
            audio = np.concatenate(joined)

        tempo = _tempo_from_rate(rate)
        direct_wav = output.suffix.lower() == ".wav" and abs(tempo - 1.0) < 0.001
        wav_path = output if direct_wav else output.with_suffix(".zerotts.wav")
        model.save_audio(audio, str(wav_path))

    if direct_wav:
        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("ZeroTTS produced an empty audio file.")
        return output

    command = ["ffmpeg", "-y", "-i", str(wav_path)]
    if abs(tempo - 1.0) >= 0.001:
        command.extend(["-filter:a", _atempo_filter(tempo)])
    command.extend(
        ["-c:a", "pcm_s16le" if output.suffix.lower() == ".wav" else "libmp3lame", str(output)]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        wav_path.unlink(missing_ok=True)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to finalize ZeroTTS audio: {result.stderr[-1000:]}")
    return output
