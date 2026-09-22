from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable


def _resolve_storage_root() -> Path:
    """Resolve the storage root.

    When ``AETHER_DATA_DIR`` is set (e.g. by the packaged desktop app, which
    points it at a per-user writable directory) storage lives under
    ``<AETHER_DATA_DIR>/storage``. Otherwise fall back to the repo-relative
    ``storage`` folder used during development.
    """
    data_dir = os.getenv("AETHER_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "storage"
    return Path(__file__).resolve().parents[4] / "storage"


STORAGE_ROOT = _resolve_storage_root()


def ensure_storage() -> Path:
    for name in (
        "raw-videos",
        "subtitles",
        "audio",
        "audio-bed",
        "audio-cues",
        "rendered-outputs",
        "thumbnails",
        "logs",
        "source-uploads",
        "voice-references",
        "reports",
        "source-transcripts",
    ):
        (STORAGE_ROOT / name).mkdir(parents=True, exist_ok=True)
    return STORAGE_ROOT


MEDIA_FOLDERS = (
    "raw-videos", "subtitles", "audio", "audio-bed", "audio-cues",
    "rendered-outputs", "thumbnails", "reports", "source-transcripts",
)


def _size(path: Path) -> int:
    if path.is_file():
        stat = path.stat()
        return stat.st_blocks * 512
    if not path.exists():
        return 0
    # Model snapshots often use hard links. Count each inode once so the UI
    # describes physical disk space that will actually be recovered.
    seen: set[tuple[int, int]] = set()
    total = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        stat = item.stat()
        inode = (stat.st_dev, stat.st_ino)
        if inode not in seen:
            seen.add(inode)
            total += stat.st_blocks * 512
    return total


def storage_usage() -> dict[str, int]:
    """Return user-data usage grouped by what can safely be cleaned."""
    root = ensure_storage()
    project_data = sum(_size(root / name) for name in MEDIA_FOLDERS)
    cache = _size(root / "zerotts-cache")
    voice_data = _size(root / "zerotts-voices") + _size(root / "voice-references")
    logs = _size(root / "logs") + _size(root / "runtime")
    return {
        "project_data": project_data,
        "zerotts_cache": cache,
        "voice_data": voice_data,
        "logs": logs,
        "total": project_data + cache + voice_data + logs,
    }


def _remove_path(path: Path) -> int:
    size = _size(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
    return size


def remove_job_artifacts(job_id: str) -> int:
    """Remove only files whose filename/directory is owned by one job id."""
    root = ensure_storage()
    removed = 0
    for name in MEDIA_FOLDERS:
        folder = root / name
        if not folder.exists():
            continue
        # All job artifacts are named with the job UUID, including variants such
        # as ``<id>-voice-...`` and directories such as ``<id>.demucs``.
        for candidate in folder.glob(f"{job_id}*"):
            removed += _remove_path(candidate)
    return removed


def cleanup_temporary_artifacts(active_job_ids: Iterable[str]) -> int:
    """Discard intermediate artifacts, never those owned by active jobs.

    Files in ``raw-videos`` are working copies used while a job is running and
    for optional source-video review/retry afterwards.  Once a job is no longer
    active they are safe to remove explicitly through the storage cleanup UI;
    rendered outputs and the original source upload are kept.
    """
    root = ensure_storage()
    active = set(active_job_ids)
    removed = 0
    raw = root / "raw-videos"
    if raw.exists():
        for candidate in raw.iterdir():
            if not any(candidate.name.startswith(job_id) for job_id in active):
                removed += _remove_path(candidate)
    # Demucs separation directories are expensive intermediates. Preserve any
    # belonging to a currently processing job.
    audio_bed = root / "audio-bed"
    if audio_bed.exists():
        for candidate in audio_bed.glob("*.demucs"):
            if not any(candidate.name.startswith(job_id) for job_id in active):
                removed += _remove_path(candidate)
    previews = root / "subtitle-previews"
    if previews.exists():
        removed += _remove_path(previews)
    return removed


def clear_zerotts_cache() -> int:
    cache = ensure_storage() / "zerotts-cache"
    return _remove_path(cache) if cache.exists() else 0
