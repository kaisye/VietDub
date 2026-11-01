"""Editor audio tracks (music / sound effects): schema normalization and the ffmpeg mix.

A workspace may hold ``audio_tracks``; each track has clips that reference an asset of
the shared music library by id. Rendering pre-mixes every audible clip into one
stereo file the length of the video (trim, offset, loop, gain, fades and automatic
ducking under the dub voice); the renderer then adds that file to the final mix.

Ducking is plain volume automation computed from the known voice intervals (not a
sidechain compressor), so it is deterministic and the editor preview can mirror it
exactly:

* voice intervals ``[s, e]`` (non-muted voiced segments) are merged when the gap
  between them is shorter than ``attack + release`` (the music stays down instead of
  pumping between close lines);
* the envelope ``d(t)`` ramps linearly 0→1 over ``[s - attack, s]``, holds 1 until
  ``e`` and ramps 1→0 over ``[e, e + release]``;
* the clip gain is multiplied by ``1 - (1 - g) * d(t)`` with ``g = 10^(amount_db/20)``.
"""
from __future__ import annotations

import math
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable

from .ffmpeg_compat import complex_filter_file_args

TRACK_KINDS = ("music", "sfx")
DEFAULT_DUCKING = {"enabled": True, "amount_db": -12.0, "attack": 0.3, "release": 0.6}
MIN_CLIP = 0.05
MIX_RATE = 48000


# ---- schema -------------------------------------------------------------------------


def _number(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(number):
        return fallback
    return max(low, min(high, number))


def normalize_ducking(value: Any, kind: str = "music") -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {
        # Sound effects are short accents: they are not ducked unless asked to.
        "enabled": bool(value.get("enabled", kind == "music")),
        "amount_db": round(_number(value.get("amount_db"), DEFAULT_DUCKING["amount_db"], -40.0, 0.0), 1),
        "attack": round(_number(value.get("attack"), DEFAULT_DUCKING["attack"], 0.01, 5.0), 3),
        "release": round(_number(value.get("release"), DEFAULT_DUCKING["release"], 0.01, 5.0), 3),
    }


def normalize_clip(clip: Any, duration: float, kind: str = "music") -> dict[str, Any] | None:
    """Clamp one clip to the timeline; ``None`` when it is unusable (no asset, too short)."""
    if not isinstance(clip, dict) or not str(clip.get("asset_id") or "").strip():
        return None
    start = round(_number(clip.get("start"), 0.0, 0.0, max(0.0, duration - MIN_CLIP)), 3)
    length = round(_number(clip.get("duration"), 0.0, 0.0, max(0.0, duration - start)), 3)
    if length < MIN_CLIP:
        return None
    fade_in = round(_number(clip.get("fade_in"), 0.0, 0.0, length), 3)
    fade_out = round(_number(clip.get("fade_out"), 0.0, 0.0, length - fade_in), 3)
    normalized = {
        "id": str(clip.get("id") or f"clip-{uuid.uuid4().hex[:8]}"),
        "asset_id": str(clip["asset_id"]).strip(),
        "start": start,
        "in": round(_number(clip.get("in"), 0.0, 0.0, 86400.0), 3),
        "duration": length,
        "volume": round(_number(clip.get("volume"), 100.0, 0.0, 200.0), 1),
        "fade_in": fade_in,
        "fade_out": fade_out,
        "loop": bool(clip.get("loop", False)),
        "ducking": normalize_ducking(clip.get("ducking"), kind),
    }
    if clip.get("title"):
        # Display name snapshot: still meaningful if the asset is later deleted.
        normalized["title"] = str(clip["title"])[:200]
    return normalized


def normalize_track(track: Any, duration: float, index: int = 0) -> dict[str, Any] | None:
    if not isinstance(track, dict):
        return None
    kind = track.get("kind") if track.get("kind") in TRACK_KINDS else "music"
    clips = [
        clip
        for clip in (normalize_clip(item, duration, kind) for item in track.get("clips") or [])
        if clip is not None
    ]
    name = str(track.get("name") or "").strip()[:80]
    return {
        "id": str(track.get("id") or f"atr-{uuid.uuid4().hex[:8]}"),
        "name": name or (f"Nhạc {index + 1}" if kind == "music" else f"Hiệu ứng {index + 1}"),
        "kind": kind,
        "muted": bool(track.get("muted", False)),
        "locked": bool(track.get("locked", False)),
        "volume": round(_number(track.get("volume"), 100.0, 0.0, 200.0), 1),
        # "solo" is a preview-only switch: never persisted.
        "clips": sorted(clips, key=lambda clip: clip["start"]),
    }


def normalize_audio_tracks(tracks: Any, duration: float) -> list[dict[str, Any]]:
    """Absent / malformed → ``[]``; ids are kept unique (later duplicates get new ids)."""
    result: list[dict[str, Any]] = []
    seen_tracks: set[str] = set()
    seen_clips: set[str] = set()
    for index, track in enumerate(tracks if isinstance(tracks, list) else []):
        normalized = normalize_track(track, duration, index)
        if normalized is None:
            continue
        if normalized["id"] in seen_tracks:
            normalized["id"] = f"atr-{uuid.uuid4().hex[:8]}"
        seen_tracks.add(normalized["id"])
        for clip in normalized["clips"]:
            if clip["id"] in seen_clips:
                clip["id"] = f"clip-{uuid.uuid4().hex[:8]}"
            seen_clips.add(clip["id"])
        result.append(normalized)
    return result


def apply_ops(tracks: list[dict[str, Any]], ops: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Granular edits: add/update/delete tracks, add/update/delete/move clips.

    ``move_clip`` changes ``start`` and optionally the track (``to_track``). Unknown ids
    raise ``KeyError`` so a stale client fails loudly instead of silently losing edits.
    """
    tracks = [dict(track, clips=[dict(clip) for clip in track["clips"]]) for track in tracks]

    def find_track(track_id: Any) -> dict[str, Any]:
        for track in tracks:
            if track["id"] == track_id:
                return track
        raise KeyError(f"audio track {track_id}")

    def find_clip(clip_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        for track in tracks:
            for clip in track["clips"]:
                if clip["id"] == clip_id:
                    return track, clip
        raise KeyError(f"audio clip {clip_id}")

    for op in ops:
        kind = op.get("op")
        if kind == "add_track":
            track = dict(op.get("track") or {})
            track.setdefault("id", f"atr-{uuid.uuid4().hex[:8]}")
            track.setdefault("clips", [])
            tracks.append(track)
        elif kind == "update_track":
            track = find_track(op.get("id"))
            for key in ("name", "kind", "muted", "locked", "volume"):
                if key in (op.get("patch") or {}):
                    track[key] = op["patch"][key]
        elif kind == "delete_track":
            tracks.remove(find_track(op.get("id")))
        elif kind == "add_clip":
            track = find_track(op.get("track_id"))
            clip = dict(op.get("clip") or {})
            clip.setdefault("id", f"clip-{uuid.uuid4().hex[:8]}")
            track["clips"].append(clip)
        elif kind == "update_clip":
            _track, clip = find_clip(op.get("id"))
            patch = op.get("patch") or {}
            for key in ("asset_id", "title", "start", "in", "duration", "volume", "fade_in", "fade_out", "loop"):
                if key in patch:
                    clip[key] = patch[key]
            if "ducking" in patch:
                clip["ducking"] = {**(clip.get("ducking") or {}), **(patch["ducking"] or {})}
        elif kind == "delete_clip":
            track, clip = find_clip(op.get("id"))
            track["clips"].remove(clip)
        elif kind == "move_clip":
            track, clip = find_clip(op.get("id"))
            if op.get("start") is not None:
                clip["start"] = op["start"]
            if op.get("to_track") and op["to_track"] != track["id"]:
                target = find_track(op["to_track"])
                track["clips"].remove(clip)
                target["clips"].append(clip)
        else:
            raise ValueError(f"Thao tác audio track không hợp lệ: {kind}")
    return normalize_audio_tracks(tracks, duration)


# ---- ducking ------------------------------------------------------------------------


def voice_intervals(workspace: dict[str, Any]) -> list[tuple[float, float]]:
    """Where the dub voice is heard (the same rule as the voice mix: voiced, not muted)."""
    if not (workspace.get("audio") or {}).get("dubbed_enabled", True):
        return []
    intervals = []
    for segment in workspace.get("segments") or []:
        if segment.get("muted") or not segment.get("audio_url"):
            continue
        start = float(segment.get("audio_start", segment.get("start", 0.0)) or 0.0)
        length = float(segment.get("audio_duration") or 0.0)
        if length > 0:
            intervals.append((start, start + length))
    return sorted(intervals)


def merge_intervals(intervals: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def duck_envelope(t: float, intervals: list[tuple[float, float]], attack: float, release: float) -> float:
    """0–1 ducking depth at ``t`` for already merged intervals (reference implementation)."""
    depth = 0.0
    for start, end in intervals:
        rise = (t - (start - attack)) / attack
        fall = ((end + release) - t) / release
        depth = max(depth, max(0.0, min(1.0, rise, fall)))
    return depth


def ducking_expression(
    intervals: list[tuple[float, float]],
    ducking: dict[str, Any],
    window: tuple[float, float],
) -> str | None:
    """ffmpeg ``volume`` expression (timeline time ``t``) or ``None`` when nothing ducks."""
    if not ducking.get("enabled"):
        return None
    attack, release = float(ducking["attack"]), float(ducking["release"])
    depth = 1.0 - 10 ** (float(ducking["amount_db"]) / 20.0)
    if depth <= 0.0005:
        return None
    merged = merge_intervals(intervals, attack + release)
    lo, hi = window
    relevant = [(s, e) for s, e in merged if e + release > lo and s - attack < hi]
    if not relevant:
        return None
    terms = "+".join(
        f"clip(min((t-{s - attack:.3f})/{attack:.3f},({e + release:.3f}-t)/{release:.3f}),0,1)"
        for s, e in relevant
    )
    return f"1-{depth:.4f}*({terms})"


# ---- render -------------------------------------------------------------------------


class MusicAssetMissing(RuntimeError):
    pass


def audible_clips(
    workspace: dict[str, Any], resolve: Callable[[str], Path | None]
) -> list[tuple[dict[str, Any], dict[str, Any], Path]]:
    """(track, clip, file) for every clip that will be heard; a missing file is an error."""
    tracks = normalize_audio_tracks(workspace.get("audio_tracks"), float(workspace.get("duration") or 0))
    result = []
    for track in tracks:
        if track["muted"] or track["volume"] <= 0:
            continue
        for clip in track["clips"]:
            if clip["volume"] <= 0:
                continue
            path = resolve(clip["asset_id"])
            if path is None or not path.exists():
                name = clip.get("title") or clip["asset_id"]
                raise MusicAssetMissing(
                    f"Thiếu file nhạc “{name}” (asset {clip['asset_id']}) trong thư viện nhạc — "
                    "thêm lại vào thư viện hoặc xóa clip này khỏi track “"
                    f"{track['name']}” rồi render lại."
                )
            result.append((track, clip, path))
    return result


def build_music_filter(
    clips: list[tuple[dict[str, Any], dict[str, Any], Path]],
    intervals: list[tuple[float, float]],
    duration: float,
) -> str:
    """Filter graph: input 0 is a silent base of ``duration``; input i (1…) is clip i's file."""
    fmt = f"aformat=sample_fmts=fltp:sample_rates={MIX_RATE}:channel_layouts=stereo"
    lines = [f"[0:a]{fmt}[base]"]
    labels = ["[base]"]
    for index, (track, clip, _path) in enumerate(clips, start=1):
        length = clip["duration"]
        gain = clip["volume"] / 100.0 * track["volume"] / 100.0
        chain = [
            f"atrim=start={clip['in']:.3f}:duration={length:.3f}",
            "asetpts=PTS-STARTPTS",
            fmt,
            f"volume={gain:.4f}",
        ]
        if clip["fade_in"] > 0:
            chain.append(f"afade=t=in:st=0:d={clip['fade_in']:.3f}")
        if clip["fade_out"] > 0:
            chain.append(f"afade=t=out:st={max(0.0, length - clip['fade_out']):.3f}:d={clip['fade_out']:.3f}")
        chain.append(f"adelay={round(clip['start'] * 1000)}:all=1")
        duck = ducking_expression(intervals, clip["ducking"], (clip["start"], clip["start"] + length))
        if duck:
            chain.append(f"volume='{duck}':eval=frame")
        label = f"m{index}"
        lines.append(f"[{index}:a]" + ",".join(chain) + f"[{label}]")
        labels.append(f"[{label}]")
    lines.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0,"
        + f"atrim=0:{duration:.3f}[aout]"
    )
    return ";\n".join(lines)


def render_music_track(
    workspace: dict[str, Any],
    output: Path,
    resolve: Callable[[str], Path | None],
) -> Path | None:
    """Pre-mix the workspace's audio tracks into ``output`` (FLAC); ``None`` when silent."""
    clips = audible_clips(workspace, resolve)
    if not clips:
        return None
    duration = max(0.25, float(workspace.get("duration") or 0))
    filter_file = output.with_suffix(".filter.txt")
    filter_file.write_text(build_music_filter(clips, voice_intervals(workspace), duration), encoding="utf-8")
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-t", f"{duration:.3f}", "-i", f"anullsrc=r={MIX_RATE}:cl=stereo",
    ]
    for _track, clip, path in clips:
        if clip["loop"]:
            command.extend(["-stream_loop", "-1"])
        command.extend(["-i", str(path)])
    command.extend(
        [*complex_filter_file_args(filter_file), "-map", "[aout]", "-c:a", "flac", "-t", f"{duration:.3f}", str(output)]
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=max(300, int(duration * 4 + 120)))
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Không trộn được track nhạc: {result.stderr[-800:]}")
    return output
