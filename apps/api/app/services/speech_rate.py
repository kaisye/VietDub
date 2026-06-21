from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
import re
from pathlib import Path
from statistics import median

from .storage import ensure_storage


@dataclass(frozen=True)
class SpeechRateProfile:
    category: str
    units_per_second: float
    median_cue_units_per_second: float
    active_duration_seconds: float
    total_units: int
    cue_count: int
    pause_ratio: float
    overlap_ratio: float
    recommended_duration_scale: float
    recommended_edge_rate: str
    confidence: str


@dataclass(frozen=True)
class _SpeechCue:
    start: float
    end: float
    text: str


def analyze_speech_rate_from_srt(
    subtitle_path: Path,
    source_language: str | None = None,
    timestamp_quality: str | None = None,
) -> SpeechRateProfile | None:
    if not speech_rate_detection_enabled():
        return None
    text = subtitle_path.read_text(encoding="utf-8", errors="ignore")
    cues = _parse_srt_cues(text)
    if not cues:
        return None
    profile = analyze_speech_rate(cues, source_language)
    if not profile:
        return None
    if timestamp_quality == "approximate":
        return replace(profile, confidence="low")
    if timestamp_quality == "mixed" and profile.confidence == "high":
        return replace(profile, confidence="medium")
    return profile


def analyze_speech_rate(cues: list[_SpeechCue], source_language: str | None = None) -> SpeechRateProfile | None:
    ordered = [cue for cue in sorted(cues, key=lambda item: (item.start, item.end)) if cue.end > cue.start and cue.text.strip()]
    if not ordered:
        return None

    overlap_ratio = _overlap_ratio(ordered)
    rolling = overlap_ratio >= _env_float("AETHER_SPEECH_RATE_ROLLING_OVERLAP_RATIO", 0.25, 0.0, 1.0)
    unit_texts = _rolling_incremental_texts(ordered) if rolling else [_clean_text(cue.text) for cue in ordered]
    unit_counts = [_speech_unit_count(text) for text in unit_texts]
    total_units = sum(unit_counts)
    active_duration = _active_duration(ordered)
    if total_units <= 0 or active_duration <= 0:
        return None

    cue_rates: list[float] = []
    for cue, count in zip(ordered, unit_counts):
        duration = cue.end - cue.start
        if count > 0 and duration >= 0.25:
            cue_rates.append(count / duration)

    median_rate = median(cue_rates) if cue_rates else total_units / active_duration
    units_per_second = total_units / active_duration
    pause_ratio = _pause_ratio(ordered)
    cjk_heavy = _is_cjk_heavy(" ".join(cue.text for cue in ordered), source_language)
    category = _speech_rate_category(units_per_second, cjk_heavy)
    scale = _duration_scale_for_category(category)
    confidence = _confidence(ordered, total_units, active_duration, overlap_ratio)
    return SpeechRateProfile(
        category=category,
        units_per_second=round(units_per_second, 3),
        median_cue_units_per_second=round(median_rate, 3),
        active_duration_seconds=round(active_duration, 3),
        total_units=total_units,
        cue_count=len(ordered),
        pause_ratio=round(pause_ratio, 3),
        overlap_ratio=round(overlap_ratio, 3),
        recommended_duration_scale=scale,
        recommended_edge_rate=_edge_rate_from_duration_scale(scale),
        confidence=confidence,
    )


def write_speech_rate_profile(subtitle_path: Path, profile: SpeechRateProfile) -> Path:
    output = ensure_storage() / "reports" / f"{subtitle_path.stem}.speech-rate.json"
    output.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def speech_rate_log_message(profile: SpeechRateProfile) -> str:
    return (
        "Detected source speech rate: "
        f"{profile.category} ({profile.units_per_second:.2f} units/s, "
        f"scale={profile.recommended_duration_scale:.2f}, confidence={profile.confidence})."
    )


def speech_rate_translation_hint(profile: SpeechRateProfile | None) -> str:
    if not profile or profile.confidence == "low":
        return ""
    if profile.category in {"fast", "very_fast"}:
        return (
            f"- Source speech rate is {profile.category} ({profile.units_per_second:.2f} units/s). "
            "Keep the translation compact and spoken-friendly, but do not omit factual details.\n"
        )
    if profile.category == "slow":
        return (
            f"- Source speech rate is slow ({profile.units_per_second:.2f} units/s). "
            "Use a calm, natural translation and do not over-compress the wording.\n"
        )
    return (
        f"- Source speech rate is normal ({profile.units_per_second:.2f} units/s). "
        "Use normal voice-over pacing.\n"
    )


def speech_rate_duration_scale(profile: SpeechRateProfile | None) -> float:
    if not profile or profile.confidence == "low":
        return 1.0
    if not speech_rate_tts_adjustment_enabled():
        return 1.0
    return profile.recommended_duration_scale


def speech_rate_detection_enabled() -> bool:
    return _env_bool("AETHER_SPEECH_RATE_DETECTION_ENABLED", True)


def speech_rate_tts_adjustment_enabled() -> bool:
    return _env_bool("AETHER_SPEECH_RATE_TTS_ADJUSTMENT_ENABLED", True)


def _parse_srt_cues(srt_text: str) -> list[_SpeechCue]:
    cues: list[_SpeechCue] = []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", srt_text.strip()) if "-->" in block]
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_text, end_text = lines[timing_index].split("-->", 1)
        start = _srt_time_to_seconds(start_text.strip())
        end = _srt_time_to_seconds(end_text.strip().split()[0])
        text = " ".join(line for line in lines[timing_index + 1 :] if line and not line.isdigit())
        text = _clean_text(text)
        if text and end > start:
            cues.append(_SpeechCue(start=start, end=end, text=text))
    return cues


def _rolling_incremental_texts(cues: list[_SpeechCue]) -> list[str]:
    result: list[str] = []
    previous = ""
    for cue in cues:
        current = _clean_text(cue.text)
        delta = _text_delta(previous, current)
        result.append(delta)
        previous = current
    return result


def _text_delta(previous: str, current: str) -> str:
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous) :].strip()
    if previous.endswith(current) or current in previous:
        return ""

    prev_tokens = previous.split()
    curr_tokens = current.split()
    best_overlap = 0
    max_overlap = min(len(prev_tokens), len(curr_tokens))
    for count in range(1, max_overlap + 1):
        if prev_tokens[-count:] == curr_tokens[:count]:
            best_overlap = count
    if best_overlap:
        return " ".join(curr_tokens[best_overlap:]).strip()
    return current


def _active_duration(cues: list[_SpeechCue]) -> float:
    intervals = [(cue.start, cue.end) for cue in cues if cue.end > cue.start]
    if not intervals:
        return 0.0
    intervals.sort()
    merged: list[tuple[float, float]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return sum(end - start for start, end in merged)


def _pause_ratio(cues: list[_SpeechCue]) -> float:
    if len(cues) < 2:
        return 0.0
    total_gap = 0.0
    previous_end = cues[0].end
    for cue in cues[1:]:
        total_gap += max(0.0, cue.start - previous_end)
        previous_end = max(previous_end, cue.end)
    span = max(0.001, max(cue.end for cue in cues) - min(cue.start for cue in cues))
    return min(1.0, total_gap / span)


def _overlap_ratio(cues: list[_SpeechCue]) -> float:
    if len(cues) < 2:
        return 0.0
    overlaps = 0
    previous_max_end = cues[0].end
    for cue in cues[1:]:
        if cue.start < previous_max_end:
            overlaps += 1
        previous_max_end = max(previous_max_end, cue.end)
    return overlaps / max(1, len(cues) - 1)


def _speech_unit_count(text: str) -> int:
    text = _clean_text(text)
    cjk_chars = re.findall(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text)
    text_without_cjk = re.sub(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", " ", text)
    word_tokens = re.findall(r"[^\W_]+(?:[-_/][^\W_]+)*", text_without_cjk, flags=re.UNICODE)
    return len(cjk_chars) + len(word_tokens)


def _is_cjk_heavy(text: str, source_language: str | None) -> bool:
    language = (source_language or "").strip().lower()
    if language.startswith("zh") or language in {"chinese", "cn", "zho", "ja", "japanese"}:
        return True
    cjk_count = len(re.findall(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text))
    latin_count = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk_count > latin_count


def _speech_rate_category(units_per_second: float, cjk_heavy: bool) -> str:
    if cjk_heavy:
        slow = _env_float("AETHER_SPEECH_RATE_CJK_SLOW_THRESHOLD", 3.0, 0.5, 20.0)
        fast = _env_float("AETHER_SPEECH_RATE_CJK_FAST_THRESHOLD", 5.2, 1.0, 30.0)
        very_fast = _env_float("AETHER_SPEECH_RATE_CJK_VERY_FAST_THRESHOLD", 7.0, 1.0, 40.0)
    else:
        slow = _env_float("AETHER_SPEECH_RATE_SLOW_THRESHOLD", 2.0, 0.5, 10.0)
        fast = _env_float("AETHER_SPEECH_RATE_FAST_THRESHOLD", 3.4, 1.0, 15.0)
        very_fast = _env_float("AETHER_SPEECH_RATE_VERY_FAST_THRESHOLD", 4.5, 1.0, 20.0)

    if units_per_second >= very_fast:
        return "very_fast"
    if units_per_second >= fast:
        return "fast"
    if units_per_second < slow:
        return "slow"
    return "normal"


def _duration_scale_for_category(category: str) -> float:
    defaults = {
        "slow": 1.1,
        "normal": 1.0,
        "fast": 0.92,
        "very_fast": 0.86,
    }
    value = _env_float(f"AETHER_SPEECH_RATE_{category.upper()}_DURATION_SCALE", defaults.get(category, 1.0), 0.75, 1.25)
    return round(value, 3)


def _edge_rate_from_duration_scale(scale: float) -> str:
    if scale <= 0:
        return "+0%"
    percent = round((1.0 / scale - 1.0) * 100)
    percent = max(-15, min(18, percent))
    return f"{percent:+d}%"


def _confidence(cues: list[_SpeechCue], total_units: int, active_duration: float, overlap_ratio: float) -> str:
    if len(cues) < 3 or total_units < 12 or active_duration < 3.0:
        return "low"
    if overlap_ratio > 0.65:
        return "medium"
    return "high"


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\{\\.*?\}", " ", text)
    text = re.sub(
        r"\[(?:music|nhac|nhạc|applause|vo tay|vỗ tay|laughter|cuoi|cười|cheering|reo hò|reo ho)\]",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip()


def _srt_time_to_seconds(value: str) -> float:
    match = re.match(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})", value)
    if not match:
        return 0.0
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms")) / 1000
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
