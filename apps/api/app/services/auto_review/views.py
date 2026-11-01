"""Turn editor workspace segments into detector views (and measure natural take lengths)."""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Callable

from .detect import CueView, Layout


def natural_duration(segment: dict[str, Any], probe: Callable[[Path], float] | None = None) -> float | None:
    """Length of the segment's take at speed 1.0 (the unfitted source take)."""
    if not segment.get("audio_url"):
        return None
    if segment.get("audio_source_duration") and segment.get("audio_source_url"):
        return float(segment["audio_source_duration"])
    source, measured = _source_take(segment)
    if source is None:
        return None
    if measured:
        return measured
    return _probe(source, probe)


def _source_take(segment: dict[str, Any]) -> tuple[Path | None, float]:
    from .. import editor

    source = editor._path_from_storage_url(segment.get("audio_source_url"))
    if source and source.exists():
        return source, float(segment.get("audio_source_duration") or 0)
    current = editor._path_from_storage_url(segment.get("audio_url"))
    if not current or not current.exists():
        return None, 0.0
    if current.name.endswith("_fit.mp3"):
        raw = current.with_name(current.name[: -len("_fit.mp3")] + "_raw.wav")
        if raw.exists():
            return raw, 0.0
    return current, 0.0


def _probe(path: Path, probe: Callable[[Path], float] | None) -> float | None:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                frames, rate = handle.getnframes(), handle.getframerate()
                if rate:
                    return frames / rate
        except (wave.Error, OSError, EOFError):
            pass
    if probe is None:
        from .. import tts

        probe = tts._probe_duration
    value = probe(path)
    return value or None


def build_views(workspace: dict[str, Any], naturals: dict[str, float | None]) -> list[CueView]:
    """``naturals`` maps segment id → natural take length (see :func:`natural_duration`)."""
    voiced_ids = {segment["id"] for segment in workspace["segments"] if segment.get("audio_url")}
    views = []
    for segment in sorted(workspace["segments"], key=lambda item: item["start"]):
        voiced = bool(segment.get("audio_url")) and not segment.get("muted")
        views.append(
            CueView(
                id=segment["id"],
                start=float(segment["start"]),
                end=float(segment["end"]),
                text=str(segment.get("text") or ""),
                voiced=voiced,
                shared=segment.get("shares_audio_with") in voiced_ids,
                voice_start=float(segment.get("audio_start", segment["start"])) if voiced else float(segment["start"]),
                voice_len=float(segment.get("audio_duration") or 0) if voiced else 0.0,
                natural=naturals.get(segment["id"]) if voiced else None,
                muted=bool(segment.get("muted")),
            )
        )
    return views


def layout_for(workspace: dict[str, Any]) -> Layout:
    from .. import editor

    try:
        chars = int(editor.subtitle_layout(workspace)["chars_per_line"])
    except Exception:  # noqa: BLE001 — older workspaces without a full style.
        chars = 42
    return Layout(chars_per_line=chars, max_lines=int(workspace.get("max_lines") or 2), duration=float(workspace.get("duration") or 0))
