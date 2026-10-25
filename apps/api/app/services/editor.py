"""Per-job editor workspace: one editable segment per display subtitle / voice chunk.

The workspace is built once from the finished pipeline artifacts (raw video,
display SRT, source SRT, TTS chunk manifest) and saved as JSON so edits,
per-segment re-voicing and re-translation survive restarts. Rendering from the
workspace remixes the per-segment audio and burns the edited subtitles.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import tts
from .audio_bed import prepare_background_audio
from .renderer import (
    _probe_dimensions,
    _subtitle_chars_per_line,
    _wrap_cue_for_ass,
    _srt_to_ass,
    _srt_time,
    normalize_subtitle_style,
    render_video,
)
from .storage import ensure_storage
from .zerotts_tts import is_zerotts_voice

WORKSPACE_VERSION = 1
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_OVERRUN_TOLERANCE = 0.15
BLUR_KEYS = ("enabled", "x", "y", "width", "height", "style", "strength")
_LOCKS: dict[str, threading.Lock] = {}


def workspace_lock(job_id: str) -> threading.Lock:
    return _LOCKS.setdefault(job_id, threading.Lock())


def workspace_dir(job_id: str) -> Path:
    path = ensure_storage() / "editor" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_path(job_id: str) -> Path:
    return workspace_dir(job_id) / "workspace.json"


def load_workspace(job_id: str) -> dict[str, Any] | None:
    path = workspace_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    workspace["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    path = workspace_path(workspace["job_id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(workspace, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return workspace


def build_workspace(job_id: str, configuration: dict[str, Any] | None, title: str = "") -> dict[str, Any]:
    root = ensure_storage()
    raw_video = _newest(root / "raw-videos", f"{job_id}.*", {".mp4", ".mov", ".m4v", ".webm", ".mkv"})
    if not raw_video:
        raise FileNotFoundError("Không tìm thấy video gốc của job này.")
    subtitles = root / "subtitles"
    display = _newest(subtitles, f"{job_id}*.render.display.srt") or _newest(subtitles, f"{job_id}*.render.srt")
    if not display:
        raise FileNotFoundError("Không tìm thấy phụ đề đã dịch của job này.")
    source = _newest(subtitles, f"{job_id}*.render.source.srt")
    chunk_manifest = _newest(root / "audio", f"{job_id}*.chunks.json")
    translated_segments = _newest(subtitles, f"{job_id}*.translated-segments.json")
    source_language = (
        json.loads(translated_segments.read_text(encoding="utf-8")).get("source_language")
        if translated_segments
        else None
    ) or ((configuration or {}).get("languages") or {}).get("source") or "auto"

    display_cues = tts._parse_srt_cues(display)
    source_cues = tts._parse_srt_cues(source) if source else []
    chunks = json.loads(chunk_manifest.read_text(encoding="utf-8")).get("chunks", []) if chunk_manifest else []
    # Local timeline TTS voices each display cue separately, so chunks map 1:1 by
    # order; otherwise match each cue to the chunk that starts closest to it.
    one_to_one = len(chunks) == len(display_cues)

    segments: list[dict[str, Any]] = []
    for index, cue in enumerate(display_cues, start=1):
        chunk = chunks[index - 1] if one_to_one else _closest_chunk(chunks, cue.start)
        audio_path = Path(chunk["audio"]) if chunk and chunk.get("audio") else None
        has_audio = bool(audio_path and audio_path.exists() and (one_to_one or abs(float(chunk["start"]) - cue.start) < 0.05))
        segments.append(
            {
                "id": f"seg-{index:04d}",
                "start": round(cue.start, 3),
                "end": round(cue.end, 3),
                "text": cue.text,
                "source_text": _overlapping_text(source_cues, cue.start, cue.end),
                "audio_url": _storage_url(audio_path) if has_audio else None,
                "audio_start": round(float(chunk["start"]), 3) if has_audio else round(cue.start, 3),
                "audio_duration": round(float(chunk["end"]) - float(chunk["start"]), 3) if has_audio else 0.0,
                "audio_text": cue.text if has_audio else "",
                "muted": False,
            }
        )

    style = normalize_subtitle_style(((configuration or {}).get("subtitle") or {}).get("style_snapshot"))
    width, height = _probe_dimensions(raw_video)
    workspace = {
        "version": WORKSPACE_VERSION,
        "job_id": job_id,
        "title": title,
        "source_language": source_language,
        "video_url": _storage_url(raw_video),
        "duration": round(tts._probe_duration(raw_video), 3),
        "width": width,
        "height": height,
        "blur": {key: style[f"hard_sub_blur_{key}"] for key in BLUR_KEYS},
        "audio": default_audio(configuration),
        "style": default_style(configuration),
        "max_lines": default_max_lines(configuration),
        "original_regions": [],
        "segments": segments,
        "render": {"status": "idle"},
    }
    return save_workspace(workspace)


def default_audio(configuration: dict[str, Any] | None) -> dict[str, Any]:
    mix = (configuration or {}).get("audio") or {}
    return {
        "original_volume": _volume(mix.get("original_volume"), 28.0),
        "dubbed_volume": _volume(mix.get("dubbed_volume"), 100.0),
        "dubbed_enabled": True,
        "original_enabled": True,
    }


def normalize_audio(audio: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Merge an audio patch: volumes are clamped, the on/off switches default to on."""
    audio = audio or {}
    return {
        "original_volume": _volume(audio.get("original_volume", current.get("original_volume")), 28.0),
        "dubbed_volume": _volume(audio.get("dubbed_volume", current.get("dubbed_volume")), 100.0),
        "dubbed_enabled": bool(audio.get("dubbed_enabled", current.get("dubbed_enabled", True))),
        "original_enabled": bool(audio.get("original_enabled", current.get("original_enabled", True))),
    }


def default_style(configuration: dict[str, Any] | None) -> dict[str, Any]:
    return _text_style(normalize_subtitle_style(((configuration or {}).get("subtitle") or {}).get("style_snapshot")))


def default_max_lines(configuration: dict[str, Any] | None) -> int:
    return int(((configuration or {}).get("subtitle") or {}).get("max_lines") or 2)


def _text_style(style: dict[str, Any]) -> dict[str, Any]:
    """The subtitle look without the blur region (kept separately) or derived fields."""
    return {
        key: value
        for key, value in style.items()
        if not key.startswith("hard_sub_blur_") and key not in {"alignment", "margin_v"}
    }


def subtitle_layout(workspace: dict[str, Any]) -> dict[str, Any]:
    """What the preview needs to place, size and wrap cues exactly like the burned-in ASS."""
    style = subtitle_style(workspace)
    width, height = workspace["width"], workspace["height"]
    return {
        **_text_style(style),
        "font_px": max(8, round(style["font_size"] * width / 512)),
        "chars_per_line": _subtitle_chars_per_line(style, (width, height)),
        "max_lines": workspace["max_lines"],
    }


def _volume(value: Any, fallback: float) -> float:
    try:
        return round(max(0.0, min(200.0, float(value))), 1)
    except (TypeError, ValueError):
        return fallback


def with_issues(workspace: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the workspace with per-segment issue flags for the UI."""
    segments = workspace["segments"]
    voiced = {segment["id"] for segment in segments if segment.get("audio_url")}
    layout = subtitle_layout(workspace)
    flagged = []
    for position, segment in enumerate(segments):
        issues: list[str] = []
        text = segment["text"].strip()
        if not any(char.isalnum() for char in text):
            issues.append("empty")
        elif _CJK_RE.search(text):
            issues.append("untranslated")
        if "empty" in issues:
            pass
        elif segment.get("shares_audio_with") in voiced:
            pass  # Split-off part: the voice of the cue it was split from covers it.
        elif not segment.get("audio_url"):
            issues.append("no_audio")
        elif segment.get("audio_text", "") != segment["text"]:
            issues.append("stale_audio")
        # Parts split off this cue share its voice, so the overrun limit is the next independent cue.
        # Voices move independently of subtitles, so compare with the next independent
        # cue's voice when it has one.
        next_start = next(
            (
                float(later.get("audio_start", later["start"])) if later.get("audio_url") else later["start"]
                for later in segments[position + 1 :]
                if later.get("shares_audio_with") != segment["id"]
            ),
            workspace["duration"],
        )
        audio_end = float(segment.get("audio_start", segment["start"])) + float(segment.get("audio_duration") or 0)
        if segment.get("audio_url") and audio_end > next_start + _OVERRUN_TOLERANCE:
            issues.append("overrun")
        display = [
            {"start": start, "end": end, "lines": lines.split("\n")}
            for start, end, lines in _wrap_cue_for_ass(
                segment["text"], segment["start"], segment["end"], layout["chars_per_line"], layout["max_lines"]
            )
        ] if text else []
        flagged.append({**segment, "issues": issues, "display": display})
    return {**workspace, "segments": flagged, "subtitle": layout}


def patch_workspace(
    workspace: dict[str, Any],
    segment_updates: list[dict[str, Any]] | None,
    blur: dict[str, Any] | None,
    audio: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    max_lines: int | None = None,
    original_regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_id = {segment["id"]: segment for segment in workspace["segments"]}
    for update in segment_updates or []:
        segment = by_id.get(str(update.get("id")))
        if not segment:
            continue
        if update.get("text") is not None:
            segment["text"] = str(update["text"]).strip()
        if update.get("start") is not None or update.get("end") is not None:
            start = max(0.0, float(update.get("start", segment["start"])))
            end = max(start + 0.1, float(update.get("end", segment["end"])))
            # Subtitle timing only: the voice track is positioned on its own (audio_start).
            segment["start"], segment["end"] = round(start, 3), round(end, 3)
        if update.get("muted") is not None:
            segment["muted"] = bool(update["muted"])
        if update.get("audio_start") is not None and segment.get("audio_url"):
            segment["audio_start"] = round(max(0.0, min(workspace["duration"], float(update["audio_start"]))), 3)
        if update.get("audio_length") is not None and segment.get("audio_url"):
            retime_voice(workspace, segment, float(update["audio_length"]))
    workspace["segments"].sort(key=lambda segment: segment["start"])
    if blur:
        style = normalize_subtitle_style(
            {f"hard_sub_blur_{key}": blur.get(key, workspace["blur"][key]) for key in BLUR_KEYS}
        )
        workspace["blur"] = {key: style[f"hard_sub_blur_{key}"] for key in BLUR_KEYS}
    if audio:
        workspace["audio"] = normalize_audio(audio, workspace["audio"])
    if style:
        workspace["style"] = _text_style(normalize_subtitle_style({**workspace["style"], **style}))
    if max_lines is not None:
        workspace["max_lines"] = max(1, min(3, int(max_lines)))
    if original_regions is not None:
        workspace["original_regions"] = normalize_regions(original_regions, workspace["duration"])
    return save_workspace(workspace)


# Tempo limits when re-timing a voice: slower than 0.8× or faster than 2× sounds broken.
MIN_TEMPO, MAX_TEMPO = 0.8, 2.0


def retime_voice(workspace: dict[str, Any], segment: dict[str, Any], length: float) -> None:
    """Speed the segment's take up or down so it lasts ``length`` seconds."""
    source, natural = _source_take(segment)
    if not source:
        raise ValueError("Không tìm thấy file giọng gốc của câu này.")
    length = max(natural / MAX_TEMPO, min(natural / MIN_TEMPO, length))
    workdir = workspace_dir(workspace["job_id"]) / "audio"
    workdir.mkdir(exist_ok=True)
    fitted, duration = _fit_take(source, natural, length, workdir / f"{segment['id']}-{uuid.uuid4().hex[:6]}.fit.mp3")
    segment.update(
        audio_url=_storage_url(fitted),
        audio_duration=round(duration, 3),
        audio_source_url=_storage_url(source),
        audio_source_duration=round(natural, 3),
        voice_slot=round(length, 3),
    )


def _source_take(segment: dict[str, Any]) -> tuple[Path | None, float]:
    """The unfitted take behind the segment's voice and its natural length."""
    source = _path_from_storage_url(segment.get("audio_source_url"))
    if source and source.exists():
        return source, float(segment.get("audio_source_duration") or tts._probe_duration(source))
    current = _path_from_storage_url(segment.get("audio_url"))
    if not current or not current.exists():
        return None, 0.0
    # Pipeline chunks: NNNN_<provider>_fit.mp3 is a sped-up copy of NNNN_<provider>_raw.wav.
    if current.name.endswith("_fit.mp3"):
        raw = current.with_name(current.name[: -len("_fit.mp3")] + "_raw.wav")
        if raw.exists():
            current = raw
    return current, tts._probe_duration(current)


def _fit_take(source: Path, natural: float, length: float, destination: Path) -> tuple[Path, float]:
    tempo = natural / max(0.05, length)
    if natural <= 0 or abs(tempo - 1.0) < 0.01:
        return source, natural
    tempo = max(MIN_TEMPO, min(MAX_TEMPO, tempo))
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-filter:a", tts._atempo_filter(tempo), "-c:a", "libmp3lame", str(destination)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"Không chỉnh được tốc độ giọng: {result.stderr[-500:]}")
    return destination, tts._probe_duration(destination) or natural / tempo


def normalize_regions(regions: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Sort, clip to the video, drop tiny ones and trim overlaps (earlier region wins)."""
    cleaned: list[dict[str, Any]] = []
    for region in sorted(regions, key=lambda item: float(item.get("start", 0))):
        start = max(0.0, float(region.get("start", 0)))
        end = min(duration, float(region.get("end", 0)))
        if cleaned:
            start = max(start, cleaned[-1]["end"])
        if end - start < 0.1:
            continue
        length = end - start
        fade_in = _seconds(region.get("fade_in"), length)
        cleaned.append(
            {
                "id": str(region.get("id") or f"reg-{uuid.uuid4().hex[:8]}"),
                "start": round(start, 3),
                "end": round(end, 3),
                "volume": _volume_region(region.get("volume")),
                # Fades share the region: together they never exceed its length.
                "fade_in": fade_in,
                "fade_out": _seconds(region.get("fade_out"), length - fade_in),
            }
        )
    return cleaned


def _seconds(value: Any, limit: float) -> float:
    try:
        return round(max(0.0, min(limit, float(value or 0))), 3)
    except (TypeError, ValueError):
        return 0.0


def _volume_region(value: Any) -> float:
    try:
        return round(max(0.0, min(400.0, float(value))), 1)
    except (TypeError, ValueError):
        return 100.0


def original_waveform(workspace: dict[str, Any], rate: int = 20) -> dict[str, Any]:
    """Peak envelope of the source audio (0–255, ``rate`` values per second), cached."""
    import numpy as np

    cache = workspace_dir(workspace["job_id"]) / f"waveform-{rate}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    video = _path_from_storage_url(workspace["video_url"])
    sample_rate = 8000
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True,
        timeout=600,
    )
    samples = np.frombuffer(result.stdout, dtype=np.int16)
    window = sample_rate // rate
    usable = len(samples) // window * window
    if usable:
        peaks = np.abs(samples[:usable].astype(np.int32)).reshape(-1, window).max(axis=1)
        # sqrt lifts quiet passages so speech and music stay visible next to loud peaks.
        scaled = np.sqrt(peaks / 32768.0) * 255
        values = [int(value) for value in scaled.clip(0, 255)]
    else:
        values = []
    data = {"rate": rate, "peaks": values}
    cache.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return data


def insert_segment(workspace: dict[str, Any], start: float, end: float, text: str = "") -> tuple[dict[str, Any], str]:
    """Add a new cue (no voice yet) on the subtitle track."""
    start = round(max(0.0, min(workspace["duration"] - 0.2, start)), 3)
    end = round(max(start + 0.2, min(workspace["duration"], end)), 3)
    segment_id = f"seg-{uuid.uuid4().hex[:8]}"
    workspace["segments"].append(
        {
            "id": segment_id,
            "start": start,
            "end": end,
            "text": text.strip(),
            "source_text": "",
            "audio_url": None,
            "audio_start": start,
            "audio_duration": 0.0,
            "audio_text": "",
            "muted": False,
        }
    )
    workspace["segments"].sort(key=lambda segment: segment["start"])
    return save_workspace(workspace), segment_id


def split_segment(workspace: dict[str, Any], segment_id: str, at_char: int | None = None) -> dict[str, Any]:
    """Split one cue's text into two display cues; the voice stays on the first part."""
    segments = workspace["segments"]
    position = _position(segments, segment_id)
    segment = segments[position]
    text = segment["text"]
    cut = _split_index(text, at_char)
    left, right = text[:cut].strip(), text[cut:].strip()
    if not left or not right:
        raise ValueError("Không tìm được chỗ tách: đặt con trỏ vào giữa câu rồi thử lại.")
    ratio = len(left) / (len(left) + len(right))
    middle = round(segment["start"] + (segment["end"] - segment["start"]) * ratio, 3)
    owner = segment.get("shares_audio_with") or (segment["id"] if segment.get("audio_url") else None)
    part = {
        "id": f"seg-{uuid.uuid4().hex[:8]}",
        "start": middle,
        "end": segment["end"],
        "text": right,
        "source_text": segment.get("source_text", ""),
        "audio_url": None,
        "audio_start": middle,
        "audio_duration": 0.0,
        "audio_text": "",
        "muted": segment.get("muted", False),
    }
    if owner:
        part["shares_audio_with"] = owner
    if segment.get("audio_url") and segment.get("audio_text") == text:
        segment["audio_text"] = left  # The existing take still voices this cue.
    segment["end"], segment["text"] = middle, left
    segments.insert(position + 1, part)
    return save_workspace(workspace)


def merge_with_next(workspace: dict[str, Any], segment_id: str) -> dict[str, Any]:
    segments = workspace["segments"]
    position = _position(segments, segment_id)
    if position + 1 >= len(segments):
        raise ValueError("Đây là câu cuối, không có câu sau để gộp.")
    segment, following = segments[position], segments[position + 1]
    shared_voice = following.get("shares_audio_with") in {segment["id"], segment.get("shares_audio_with")}
    voice_was_fresh = segment.get("audio_text") == segment["text"]
    segment["text"] = f'{segment["text"]} {following["text"]}'.strip()
    segment["end"] = max(segment["end"], following["end"])
    if following.get("source_text") and following["source_text"] != segment.get("source_text"):
        segment["source_text"] = f'{segment.get("source_text", "")} {following["source_text"]}'.strip()
    if shared_voice and voice_was_fresh and segment.get("audio_url"):
        segment["audio_text"] = segment["text"]
    segments.remove(following)
    _release_shared_audio(workspace, following)
    return save_workspace(workspace)


def delete_segment(workspace: dict[str, Any], segment_id: str) -> dict[str, Any]:
    segment = next((item for item in workspace["segments"] if item["id"] == segment_id), None)
    if segment is None:
        raise KeyError(segment_id)
    workspace["segments"].remove(segment)
    _release_shared_audio(workspace, segment)
    return save_workspace(workspace)


def regenerate_segment_audio(
    workspace: dict[str, Any],
    segment_id: str,
    *,
    voice: str,
    provider: str,
    voice_rate: str,
    target_language: str,
    voice_overrides: tts.VoiceOverrides | None,
) -> dict[str, Any]:
    segments = workspace["segments"]
    position = next((i for i, segment in enumerate(segments) if segment["id"] == segment_id), None)
    if position is None:
        raise KeyError(segment_id)
    segment = segments[position]
    text = segment["text"].strip()
    if not text:
        raise ValueError("Câu này đang trống, không có gì để đọc.")

    workdir = workspace_dir(workspace["job_id"]) / "audio"
    workdir.mkdir(exist_ok=True)
    label = f"{segment_id}-{uuid.uuid4().hex[:6]}"
    raw = workdir / f"{label}.raw.mp3"
    if is_zerotts_voice(voice):
        provider = "zerotts"
    with tts.tts_provider_override(provider):
        tts._save_tts_segment(
            text,
            tts._resolve_voice(voice, target_language),
            raw,
            tts._normalize_rate(voice_rate),
            workdir,
            label,
            max(0.25, segment["end"] - segment["start"]),
            voice_overrides,
        )
    voice_start = float(segment.get("audio_start", segment["start"])) if segment.get("audio_url") else segment["start"]
    source_duration = tts._probe_duration(raw)
    if segment.get("voice_slot"):
        # The user sized this voice on the timeline: fit the new take to that slot.
        fitted, duration = _fit_take(raw, source_duration, float(segment["voice_slot"]), workdir / f"{label}.fit.mp3")
    else:
        next_start = segments[position + 1]["start"] if position + 1 < len(segments) else float("inf")
        slot = max(0.3, next_start - voice_start - 0.08) if next_start != float("inf") else float("inf")
        fitted, duration = tts._fit_audio_to_slot(raw, workdir / f"{label}.fit.mp3", slot)

    # The new take voices only this cue's text: parts split off it need their own voice,
    # and the cue this one was split from still speaks this text too.
    for other in segments:
        if other.get("shares_audio_with") == segment_id:
            other.pop("shares_audio_with")
    owner_id = segment.pop("shares_audio_with", None)
    for other in segments:
        if other["id"] == owner_id:
            other["audio_text"] = ""
    # Keep one previous take so the user can A/B and switch back. Older takes stay
    # on disk: saved drafts may still point at them.
    segment.update(
        previous_audio_source_url=segment.get("audio_source_url"),
        previous_audio_source_duration=segment.get("audio_source_duration"),
        previous_audio_url=segment.get("audio_url"),
        previous_audio_duration=segment.get("audio_duration", 0.0),
        previous_audio_text=segment.get("audio_text", ""),
        audio_url=_storage_url(fitted),
        audio_start=round(voice_start, 3),
        audio_duration=round(duration, 3),
        audio_text=segment["text"],
        audio_generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        # The unfitted take: later re-timing starts from it, never from a fitted copy.
        audio_source_url=_storage_url(raw),
        audio_source_duration=round(source_duration, 3),
    )
    return save_workspace(workspace)


def swap_previous_audio(workspace: dict[str, Any], segment_id: str) -> dict[str, Any]:
    segment = next((item for item in workspace["segments"] if item["id"] == segment_id), None)
    if segment is None:
        raise KeyError(segment_id)
    if not segment.get("previous_audio_url"):
        raise ValueError("Câu này chưa có bản audio trước để đổi lại.")
    for current, previous in (
        ("audio_url", "previous_audio_url"),
        ("audio_duration", "previous_audio_duration"),
        ("audio_text", "previous_audio_text"),
        ("audio_source_url", "previous_audio_source_url"),
        ("audio_source_duration", "previous_audio_source_duration"),
    ):
        segment[current], segment[previous] = segment.get(previous), segment.get(current)
    return save_workspace(workspace)


def _release_shared_audio(workspace: dict[str, Any], removed: dict[str, Any]) -> None:
    for other in workspace["segments"]:
        if other.get("shares_audio_with") == removed["id"]:
            other.pop("shares_audio_with")


def _position(segments: list[dict[str, Any]], segment_id: str) -> int:
    position = next((i for i, segment in enumerate(segments) if segment["id"] == segment_id), None)
    if position is None:
        raise KeyError(segment_id)
    return position


def _split_index(text: str, at_char: int | None) -> int:
    """Use the caret when given, else the clause break (or space) nearest the middle."""
    if at_char is not None and 0 < at_char < len(text):
        while at_char < len(text) and not text[at_char - 1].isspace() and not text[at_char].isspace():
            at_char += 1  # Never cut inside a word.
        return at_char
    middle = len(text) / 2
    clause = [i + 1 for i, char in enumerate(text[:-1]) if char in ",;:.!?" and text[i + 1] == " "]
    clause = [i for i in clause if 0.25 * len(text) <= i <= 0.75 * len(text)]
    spaces = [i for i, char in enumerate(text) if char == " "]
    candidates = clause or spaces
    return int(min(candidates, key=lambda i: abs(i - middle))) if candidates else 0


def retranslate_segment(
    workspace: dict[str, Any],
    segment_id: str,
    *,
    source_language: str,
    target_language: str,
) -> dict[str, Any]:
    from .translator import IndexedSubtitleCue, _translate_contextual_batch

    segments = workspace["segments"]
    position = next((i for i, segment in enumerate(segments) if segment["id"] == segment_id), None)
    if position is None:
        raise KeyError(segment_id)
    segment = segments[position]
    if not segment.get("source_text"):
        raise ValueError("Câu này không có câu gốc để dịch lại.")

    def cue(item: dict[str, Any], text: str) -> IndexedSubtitleCue:
        return IndexedSubtitleCue(index=item["id"], start=item["start"], end=item["end"], text=text)

    # Neighbours give the model the scene; the already-translated ones keep wording consistent.
    before = [cue(item, item["text"]) for item in segments[max(0, position - 4) : position]]
    after = [cue(item, item["source_text"]) for item in segments[position + 1 : position + 4] if item.get("source_text")]
    translated = _translate_contextual_batch(
        [cue(segment, segment["source_text"])],
        source_language,
        target_language,
        None,
        before,
        after,
        False,
        None,
    )
    segment["text"] = translated[segment["id"]]
    return save_workspace(workspace)


EDITABLE_KEYS = ("segments", "blur", "audio", "style", "max_lines", "original_regions")
_DERIVED_SEGMENT_KEYS = {"issues", "display"}


def replace_state(workspace: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Put back a whole editable state (undo/redo); derived fields are recomputed."""
    segments = []
    for segment in state["segments"]:
        clean = {key: value for key, value in segment.items() if key not in _DERIVED_SEGMENT_KEYS}
        clean["start"], clean["end"] = float(clean["start"]), float(clean["end"])
        clean["text"] = str(clean.get("text", ""))
        segments.append(clean)
    workspace["segments"] = sorted(segments, key=lambda segment: segment["start"])
    blur = normalize_subtitle_style({f"hard_sub_blur_{key}": value for key, value in state["blur"].items()})
    workspace["blur"] = {key: blur[f"hard_sub_blur_{key}"] for key in BLUR_KEYS}
    workspace["audio"] = normalize_audio(state["audio"], workspace["audio"])
    workspace["style"] = _text_style(normalize_subtitle_style(state["style"]))
    workspace["max_lines"] = max(1, min(3, int(state["max_lines"])))
    workspace["original_regions"] = normalize_regions(state["original_regions"], workspace["duration"])
    return save_workspace(workspace)


def drafts_dir(job_id: str) -> Path:
    path = workspace_dir(job_id) / "drafts"
    path.mkdir(exist_ok=True)
    return path


def save_draft(workspace: dict[str, Any], name: str = "") -> dict[str, Any]:
    """Snapshot the whole workspace so it can be restored later."""
    created = datetime.utcnow()
    draft_id = created.strftime("%Y%m%d-%H%M%S-%f")  # Sortable: newest first by name.
    snapshot = {
        "id": draft_id,
        "name": name.strip() or "Bản nháp",
        "created_at": created.isoformat(timespec="seconds") + "Z",
        "segment_count": len(workspace["segments"]),
        "workspace": {key: value for key, value in workspace.items() if key != "render"},
    }
    (drafts_dir(workspace["job_id"]) / f"{draft_id}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
    )
    return {key: value for key, value in snapshot.items() if key != "workspace"}


def list_drafts(job_id: str) -> list[dict[str, Any]]:
    drafts = []
    for path in sorted(drafts_dir(job_id).glob("*.json"), reverse=True):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        drafts.append({key: value for key, value in snapshot.items() if key != "workspace"})
    return drafts


def restore_draft(workspace: dict[str, Any], draft_id: str) -> dict[str, Any]:
    path = drafts_dir(workspace["job_id"]) / f"{Path(draft_id).name}.json"
    if not path.exists():
        raise KeyError(draft_id)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    # Restoring is itself undoable: keep the state being replaced as a draft.
    save_draft(workspace, "Tự lưu trước khi khôi phục")
    restored = {**snapshot["workspace"], "render": workspace.get("render", {"status": "idle"})}
    return save_workspace(restored)


def delete_draft(job_id: str, draft_id: str) -> None:
    (drafts_dir(job_id) / f"{Path(draft_id).name}.json").unlink(missing_ok=True)


def write_srt(workspace: dict[str, Any], destination: Path) -> Path:
    blocks = [
        f"{number}\n{_srt_time(segment['start'])} --> {_srt_time(segment['end'])}\n{segment['text']}"
        for number, segment in enumerate(
            (segment for segment in workspace["segments"] if segment["text"].strip()), start=1
        )
    ]
    destination.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return destination


def subtitle_style(workspace: dict[str, Any]) -> dict[str, Any]:
    return normalize_subtitle_style(
        {**workspace["style"], **{f"hard_sub_blur_{key}": value for key, value in workspace["blur"].items()}}
    )


def export_workspace(workspace: dict[str, Any], configuration: dict[str, Any] | None) -> Path:
    """Write SRT, ASS and the timed voice chunks to a folder for other editors (CapCut...)."""
    job_id = workspace["job_id"]
    folder = ensure_storage() / "editor-exports" / job_id
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "audio-chunks").mkdir(parents=True)
    srt = write_srt(workspace, folder / "subtitles.srt")
    video = _path_from_storage_url(workspace["video_url"])
    ass = _srt_to_ass(srt, subtitle_style(workspace), _probe_dimensions(video), workspace["max_lines"])
    ass.replace(folder / "subtitles.ass")

    rows = ["file,start_seconds,duration_seconds,text"]
    for number, segment in enumerate(workspace["segments"], start=1):
        audio = _path_from_storage_url(segment.get("audio_url"))
        if not audio or not audio.exists() or segment.get("muted"):
            continue
        name = f"{number:04d}_{_srt_time(segment['audio_start']).replace(':', '-').replace(',', '.')}{audio.suffix}"
        shutil.copyfile(audio, folder / "audio-chunks" / name)
        text = segment["text"].replace('"', '""')
        rows.append(f'{name},{segment["audio_start"]:.3f},{segment["audio_duration"]:.3f},"{text}"')
    (folder / "audio-chunks" / "timeline.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return folder


def render_workspace(
    workspace: dict[str, Any],
    configuration: dict[str, Any] | None,
    *,
    render_quality: str,
    burn_subtitles: bool,
) -> Path:
    job_id = workspace["job_id"]
    workdir = workspace_dir(job_id)
    audio = normalize_audio(workspace.get("audio"), {})
    video = _path_from_storage_url(workspace["video_url"])
    if not video or not video.exists():
        raise RuntimeError("Video gốc không còn trong storage.")

    timed = [
        tts.TimedAudioSegment(
            path=path,
            start=float(segment["audio_start"]),
            end=float(segment["audio_start"]) + float(segment["audio_duration"]),
        )
        for segment in workspace["segments"]
        if audio["dubbed_enabled"]
        and not segment.get("muted")
        and (path := _path_from_storage_url(segment.get("audio_url")))
        and path.exists()
    ]
    voice_track: Path | None = None
    if timed:
        voice_track = workdir / "voice.mp3"
        tts._mix_timed_audio(timed, voice_track, workdir / "voice-filter.txt", workspace["duration"])

    subtitle = write_srt(workspace, workdir / "subtitles.srt") if burn_subtitles else None
    # Without a dub the full source audio (original voices included) is kept, not the vocal-free bed.
    audio_bed = (
        prepare_background_audio(video, preserve_source_audio=voice_track is None)
        if audio["original_enabled"]
        else None
    )
    output = configuration or {}
    return render_video(
        video,
        voice_track,
        subtitle,
        audio_bed.path if audio_bed else None,
        render_quality,
        subtitle_style=subtitle_style(workspace),
        audio_mix={
            **(output.get("audio") or {}),
            "original_volume": audio["original_volume"],
            "dubbed_volume": audio["dubbed_volume"],
            "original_volume_regions": workspace.get("original_regions") or [],
        },
        aspect_ratio=(output.get("output") or {}).get("aspect_ratio", "source"),
        max_lines=workspace["max_lines"],
        output_filename=f"{job_id}-edit-{uuid.uuid4().hex[:6]}.mp4",
    )


def _newest(directory: Path, pattern: str, suffixes: set[str] | None = None) -> Path | None:
    candidates = [
        path
        for path in directory.glob(pattern)
        if path.is_file() and path.stat().st_size > 0 and (not suffixes or path.suffix.lower() in suffixes)
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime, default=None)


def _closest_chunk(chunks: list[dict[str, Any]], start: float) -> dict[str, Any] | None:
    return min(chunks, key=lambda chunk: abs(float(chunk["start"]) - start), default=None)


def _overlapping_text(cues: list[tts.SubtitleCue], start: float, end: float) -> str:
    return " ".join(cue.text for cue in cues if cue.start < end and cue.end > start).strip()


def _storage_url(path: Path | None) -> str | None:
    if not path:
        return None
    try:
        relative = path.resolve().relative_to(ensure_storage().resolve())
    except ValueError:
        return None
    return "/storage/" + relative.as_posix()


def _path_from_storage_url(value: object) -> Path | None:
    if not isinstance(value, str) or not value.startswith("/storage/"):
        return None
    return ensure_storage() / value[len("/storage/") :]
