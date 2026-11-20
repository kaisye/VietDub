"""Word-by-word subtitle timing ("karaoke"): the words light up or appear as the voice says them.

Vietnamese is one syllable per written word, so time is shared by syllables, with
pauses after punctuation. When the dubbed voice track is known, the words are laid
on the stretches where the voice actually sounds, so a pause in the speech also
pauses the subtitle.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

KARAOKE_MODES = ("off", "highlight", "reveal")
FRAME_SECONDS = 0.02
_SAMPLE_RATE = 16000

_DIGITS = re.compile(r"\d")
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


def word_weight(word: str) -> float:
    """Roughly how long the word takes to say, in syllables."""
    digits = len(_DIGITS.findall(word))
    letters = len(_LETTERS.findall(word))
    # "2017" is read "hai nghìn không trăm mười bảy"; a long foreign word has several syllables.
    weight = max(1.0, digits * 1.5) if digits else 1.0
    if letters > 7:
        weight += (letters - 7) / 4
    return weight


def pause_after(word: str) -> float:
    """Extra pause (in syllables) after a word, from its trailing punctuation."""
    tail = word.rstrip("\"'”’)»]")
    if tail.endswith((".", "!", "?", "…", "。", "！", "？")):
        return 0.8
    if tail.endswith((",", ";", ":", "—", "–", "，")):
        return 0.45
    return 0.0


@dataclass(frozen=True)
class VoiceActivity:
    """Which 20 ms frames of a voice track carry speech."""

    voiced: tuple[bool, ...]

    def voiced_frames(self, start: float, end: float) -> list[int]:
        first = max(0, int(start / FRAME_SECONDS))
        last = min(len(self.voiced), int(end / FRAME_SECONDS + 0.999))
        return [index for index in range(first, last) if self.voiced[index]]


def load_voice_activity(path: Path | None) -> VoiceActivity | None:
    """Decode the voice track and mark the frames that are louder than its noise floor."""
    if path is None or not Path(path).exists():
        return None
    try:
        import numpy as np

        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(_SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True,
            timeout=300,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        frame = int(_SAMPLE_RATE * FRAME_SECONDS)
        count = len(samples) // frame
        if count == 0:
            return None
        rms = np.sqrt(np.mean(samples[: count * frame].reshape(count, frame) ** 2, axis=1))
        loud = rms[rms > 1e-4]
        if loud.size == 0:
            return VoiceActivity(tuple(False for _ in range(count)))
        # Speech sits well above the floor of a TTS track; 8% of a loud frame is a safe cut.
        threshold = max(0.004, float(np.percentile(loud, 90)) * 0.08)
        voiced = rms > threshold
        # Close the short gaps between syllables so a word is not split mid-way.
        bridged = voiced.copy()
        gap = 0
        for index, value in enumerate(voiced):
            if value:
                if 0 < gap <= 6 and index - gap - 1 >= 0 and voiced[index - gap - 1]:
                    bridged[index - gap : index] = True
                gap = 0
            else:
                gap += 1
        return VoiceActivity(tuple(bool(value) for value in bridged))
    except Exception:
        return None


def word_times(
    words: list[str],
    start: float,
    end: float,
    activity: VoiceActivity | None = None,
) -> list[tuple[float, float]]:
    """When each word is spoken, between start and end (seconds, absolute)."""
    if not words:
        return []
    end = max(end, start + 0.05)
    # Each word takes its own weight, then the pause after it (not after the last word).
    marks: list[tuple[float, float]] = []
    cursor = 0.0
    for index, word in enumerate(words):
        weight = word_weight(word)
        marks.append((cursor, cursor + weight))
        cursor += weight + (pause_after(word) if index < len(words) - 1 else 0.0)
    total = cursor or 1.0

    frames = activity.voiced_frames(start, end) if activity else []
    voiced_seconds = len(frames) * FRAME_SECONDS
    if frames and voiced_seconds >= max(0.2, 0.2 * (end - start)):
        # Lay the words on the voiced frames only: a pause in speech pauses the words.
        def at(position: float) -> float:
            offset = min(len(frames) - 1, max(0, int(position / total * len(frames))))
            return max(start, min(end, frames[offset] * FRAME_SECONDS))

        times = []
        for index, (begin, finish) in enumerate(marks):
            word_start = at(begin)
            word_end = (
                min(end, frames[-1] * FRAME_SECONDS + FRAME_SECONDS)
                if index == len(marks) - 1
                else at(finish)
            )
            times.append((word_start, max(word_start + 0.04, word_end)))
        return _monotonic(times, end)

    span = end - start
    return _monotonic(
        [(start + span * begin / total, start + span * finish / total) for begin, finish in marks],
        end,
    )


def _monotonic(times: list[tuple[float, float]], end: float) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    previous = 0.0
    for word_start, word_end in times:
        word_start = max(word_start, previous)
        word_end = min(end, max(word_end, word_start + 0.01))
        result.append((round(word_start, 3), round(word_end, 3)))
        previous = word_start
    return result


def _ms(value: float) -> int:
    return max(0, round(value * 1000))


def ass_line_text(
    lines: list[str],
    page_start: float,
    page_end: float,
    mode: str,
    *,
    text_tag: str,
    highlight_tag: str,
    outline_alpha: str,
    shadow_alpha: str | None,
    activity: VoiceActivity | None = None,
) -> str:
    """The ASS text of one page with per-word timing tags.

    highlight: every word is shown; the word being spoken takes the highlight colour.
    reveal: words appear one by one as they are spoken, and stay.
    Times inside \\t are milliseconds from the event start.
    """
    words = [word for line in lines for word in line.split()]
    times = word_times(words, page_start, page_end, activity)
    out_lines: list[str] = []
    position = 0
    for line in lines:
        parts: list[str] = []
        for word in line.split():
            word_start, word_end = times[position]
            position += 1
            begin = _ms(word_start - page_start)
            if mode == "reveal":
                shown = f"\\1a&H00&\\3a&H{outline_alpha}&" + (f"\\4a&H{shadow_alpha}&" if shadow_alpha else "")
                hidden = "\\1a&HFF&\\3a&HFF&" + ("\\4a&HFF&" if shadow_alpha else "")
                parts.append("{" + f"{hidden}\\t({begin},{begin + 1},{shown})" + "}" + _escape(word))
            else:
                finish = _ms(word_end - page_start)
                parts.append(
                    "{"
                    + f"\\1c{text_tag}\\t({begin},{begin + 1},\\1c{highlight_tag})"
                    + f"\\t({finish},{finish + 1},\\1c{text_tag})"
                    + "}"
                    + _escape(word)
                )
        out_lines.append(" ".join(parts))
    # Reset so whatever follows (the bilingual line) is drawn normally.
    reset = "{" + f"\\1c{text_tag}\\1a&H00&\\3a&H{outline_alpha}&" + (f"\\4a&H{shadow_alpha}&" if shadow_alpha else "") + "}"
    return r"\N".join(out_lines) + reset


def _escape(word: str) -> str:
    # Braces open an override block in ASS.
    return word.replace("{", "(").replace("}", ")")
