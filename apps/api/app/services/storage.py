from __future__ import annotations

import os
from pathlib import Path


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
