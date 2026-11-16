"""Sentence units for dubbing, and display pages for subtitles.

Two layers, kept apart on purpose:

* A *sentence unit* is what gets translated and voiced: consecutive source cues
  joined until the sentence ends (or the speaker pauses). Its start and end are
  taken from the outer boundaries of whole source cues, never interpolated, so
  grouping cannot shift a timestamp. Each unit becomes exactly one TTS clip, so a
  voice is never cut in the middle of a sentence.
* A *display page* is what the viewer reads: a sentence unit wrapped to at most
  ``max_lines`` lines. Pages are timed against the dubbed voice when its span is
  known (the viewer hears the translation, whose word order differs from the
  source), and by text length across the cue otherwise.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Sequence, TypeVar

_SENTENCE_END_RE = re.compile(r"[.!?…。！？]+[\"'”’»)\]）】」』]*\s*$")
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
# Clause boundaries a page break may follow: a comma-like mark, or a conjunction.
_CLAUSE_PUNCT_RE = re.compile(r"[,;:，、；：]$")
_BREAK_BEFORE_WORDS = frozenset(
    {
        # Vietnamese
        "và", "nhưng", "hoặc", "vì", "nên", "thì", "mà", "để", "khi", "nếu", "rằng", "còn",
        # English
        "and", "but", "or", "because", "so", "when", "if", "that", "which", "while",
    }
)

T = TypeVar("T")


@dataclass(frozen=True)
class Unit:
    start: float
    end: float
    text: str
    members: int


def sentence_units_enabled() -> bool:
    return os.getenv("AETHER_SENTENCE_UNITS", "1").strip().lower() not in {"0", "false", "off", "no"}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


# Titles that are always followed by a name, so their period never ends a
# sentence ("…as Mr." / "Leatherjacket himself…"). Words that can end one
# ("etc.", "Inc.", "Jr.") are deliberately left out.
_TITLE_ABBREVIATIONS = frozenset(
    "mr mrs ms mx dr prof sen rep gov gen lt col capt sgt st mt fr rev hon pres vs".split()
)
_LAST_WORD_RE = re.compile(r"([^\W\d_]+)\.$")
_INITIAL_RE = re.compile(r"(?:^|\s)[A-Z]\.$")


def ends_sentence(text: str) -> bool:
    stripped = text.strip()
    if not _SENTENCE_END_RE.search(stripped):
        return False
    word = _LAST_WORD_RE.search(stripped)
    if word and word.group(1).casefold() in _TITLE_ABBREVIATIONS:
        return False
    # A lone capital initial ("Jensen H.") continues with the rest of the name.
    return not _INITIAL_RE.search(stripped)


def _join(texts: Sequence[str]) -> str:
    joined = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if not joined:
            joined = text
        elif _CJK_RE.search(joined[-1:]) and _CJK_RE.search(text[:1]):
            joined += text
        else:
            joined += " " + text
    return re.sub(r"\s+", " ", joined).strip()


def group_sentences(
    cues: Sequence[T],
    *,
    start: Callable[[T], float],
    end: Callable[[T], float],
    text: Callable[[T], str],
    pause_seconds: float | None = None,
    max_seconds: float | None = None,
) -> list[Unit]:
    """Join consecutive cues into sentence units with exact outer timestamps.

    A unit closes when its text ends a sentence, when the next cue starts after a
    pause of at least ``pause_seconds``, or before it would grow past
    ``max_seconds``. Sources without punctuation (auto captions) fall back to
    pauses and a shorter length limit, since sentence ends cannot be seen.
    """
    ordered = sorted(
        (cue for cue in cues if text(cue).strip() and end(cue) > start(cue)),
        key=start,
    )
    if not ordered:
        return []
    pause = pause_seconds if pause_seconds is not None else _env_float(
        "AETHER_SENTENCE_PAUSE_SECONDS", 0.6, 0.1, 5.0
    )
    limit = max_seconds if max_seconds is not None else _env_float(
        "AETHER_SENTENCE_MAX_SECONDS", 20.0, 3.0, 60.0
    )
    punctuated = sum(1 for cue in ordered if ends_sentence(text(cue)))
    if punctuated < max(1, len(ordered) // 10):
        limit = min(limit, _env_float("AETHER_SENTENCE_MAX_SECONDS_UNPUNCTUATED", 8.0, 3.0, 60.0))

    units: list[Unit] = []
    group: list[T] = []
    group_end = 0.0

    def close() -> None:
        nonlocal group
        if group:
            units.append(
                Unit(
                    start=start(group[0]),
                    end=group_end,
                    text=_join([text(cue) for cue in group]),
                    members=len(group),
                )
            )
        group = []

    # Speech recognisers cut at breaths, not sentences ("…Google, and" / "Microsoft."),
    # so an unfinished sentence keeps going across a pause up to this long.
    unfinished_pause = max(
        pause, _env_float("AETHER_SENTENCE_UNFINISHED_PAUSE_SECONDS", 1.5, 0.1, 10.0)
    )
    # A finished but very short sentence ("Yes.") joins the next one when they
    # follow each other closely, so no clip is a fraction of a second long.
    min_seconds = _env_float("AETHER_SENTENCE_MIN_SECONDS", 1.2, 0.0, 10.0)

    for cue in ordered:
        if group:
            gap = start(cue) - group_end
            too_long = max(group_end, end(cue)) - start(group[0]) > limit
            # "…the highest echelon of U.S." + "politics.": a period followed by a
            # lowercase word is an abbreviation, not the end of the sentence.
            continues = text(cue).lstrip()[:1].islower()
            if ends_sentence(text(group[-1])) and not continues:
                short = group_end - start(group[0]) < min_seconds
                should_close = not (short and gap < pause)
            else:
                should_close = gap >= unfinished_pause
            if too_long or should_close:
                close()
        if not group:
            group_end = end(cue)
        group.append(cue)
        group_end = max(group_end, end(cue))
    close()
    return units


def _page_texts(text: str, chars_per_line: int, max_lines: int) -> list[str]:
    """Split text into pages of at most max_lines lines, breaking at clauses."""
    words = text.split()
    if not words:
        return []
    capacity = max(8, chars_per_line) * max(1, max_lines)
    if len(text) <= capacity:
        return [text]
    pages: list[str] = []
    remaining = words
    while remaining:
        joined = " ".join(remaining)
        if len(joined) <= capacity:
            pages.append(joined)
            break
        # Fill one page, then back off to the nearest clause boundary in its
        # second half so a page ends at a natural pause rather than mid-phrase.
        count = 0
        length = 0
        for word in remaining:
            extra = len(word) + (1 if count else 0)
            if count and length + extra > capacity:
                break
            length += extra
            count += 1
        count = max(1, count)
        best = count
        for index in range(count, max(1, count // 2), -1):
            previous, following = remaining[index - 1], remaining[index] if index < len(remaining) else ""
            if _CLAUSE_PUNCT_RE.search(previous) or ends_sentence(previous) or following.casefold() in _BREAK_BEFORE_WORDS:
                best = index
                break
        pages.append(" ".join(remaining[:best]))
        remaining = remaining[best:]
    # Avoid a tiny trailing page ("ạ.") by moving words back from the previous one.
    if len(pages) >= 2 and len(pages[-1]) < capacity * 0.25:
        previous = pages[-2].split()
        last = pages[-1].split()
        while len(previous) > 1 and len(" ".join(last)) < len(" ".join(previous)) * 0.6:
            last.insert(0, previous.pop())
        pages[-2:] = [" ".join(previous), " ".join(last)]
    return [page for page in pages if page]


def page_cue(
    text: str,
    start: float,
    end: float,
    chars_per_line: int,
    max_lines: int,
    *,
    voice_start: float | None = None,
    voice_end: float | None = None,
    hold_for_voice: bool = True,
) -> list[tuple[float, float, str]]:
    """Display pages for one sentence unit as (start, end, page_text).

    Each page fits in ``max_lines`` lines of ``chars_per_line``; breaking a page
    into lines is left to the renderer's balanced wrapper.

    The first page appears at the cue start and the last stays until the cue end
    (or the end of the voice, if it runs longer). Boundaries between pages follow
    the dubbed voice when its span is given, otherwise the cue, split by text
    length so a long page stays up longer than a short one.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    chars_per_line = max(8, int(chars_per_line))
    max_lines = max(1, int(max_lines))
    pages = _page_texts(text, chars_per_line, max_lines)
    voiced = voice_start is not None and voice_end is not None and voice_end - voice_start > 0.2
    span_start, span_end = (voice_start, voice_end) if voiced else (start, end)
    # The pipeline keeps the last page up while an overrunning voice still speaks;
    # the editor passes hold_for_voice=False because there the cue end is the
    # user's explicit choice.
    last_end = max(end, voice_end) if voiced and hold_for_voice else end
    if len(pages) == 1:
        return [(start, last_end, pages[0])]

    weights = [max(1, len(page)) for page in pages]
    total = sum(weights)
    duration = max(0.1, span_end - span_start)
    result: list[tuple[float, float, str]] = []
    cursor = start
    consumed = 0
    for index, (page, weight) in enumerate(zip(pages, weights)):
        consumed += weight
        if index == len(pages) - 1:
            page_end = last_end
        else:
            page_end = span_start + duration * consumed / total
            page_end = min(max(cursor + 0.2, page_end), max(cursor + 0.2, last_end - 0.2))
        result.append((round(cursor, 3), round(page_end, 3), page))
        cursor = page_end
    return result
