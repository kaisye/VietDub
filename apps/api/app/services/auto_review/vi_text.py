"""Vietnamese text helpers for the auto review: syllables, phrase-aware breaks.

Everything here is pure and cheap so the detector can run over hundreds of cues.
"""
from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_DIGITS_RE = re.compile(r"^\d+$")
_SENTENCE_END_RE = re.compile(r"[.!?…。！？]['\"”’)\]]*$")
_CLAUSE_END_RE = re.compile(r"[,;:–—-]['\"”’)\]]*$")
_VI_MARKS_RE = re.compile(
    r"[ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ]", re.IGNORECASE
)
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+", re.IGNORECASE)

# Words that bind to what follows: a line or cue must not END on them
# ("…của | anh ấy", "…và | tôi"). Mostly function words, classifiers and
# pre-verbal markers; English ones cover untranslated fragments.
DANGLING_WORDS = frozenset(
    """
    của và là thì những các một để với cho mà nhưng hoặc hay nên vì do bởi từ đến tới
    trong trên dưới về theo khi nếu rằng sự việc cuộc mỗi mọi từng rất đã đang sẽ bị
    chiếc cái tại ở như lúc giữa cùng
    the a an of and to in on at for with by or but
    """.split()
)

# Sentence-final particles: a line must not START with them ("…phải không | nhỉ").
LEADING_FORBIDDEN = frozenset("à ạ nhé nhỉ chứ hả ư nha đấy đâu".split())

# Vietnamese numbers are several words: "hai | mươi", "một | trăm", "ba | nghìn".
NUMBER_WORDS = frozenset("một hai ba bốn năm sáu bảy tám chín mười mươi trăm nghìn ngàn triệu tỷ tỉ".split())
NUMBER_TAILS = frozenset("mươi trăm nghìn ngàn triệu tỷ tỉ lăm mốt tư rưỡi linh lẻ".split())

# Clause openers: a natural place to cut BEFORE them.
CLAUSE_OPENERS = frozenset(
    "nhưng và mà vì nên để khi nếu rồi thì hoặc còn bởi tuy song but and because so when if while".split()
)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).replace("đ", "d").replace("Đ", "D")


def words(text: str) -> list[str]:
    return (text or "").split()


def bare(word: str) -> str:
    """Lower-cased word without surrounding punctuation."""
    return word.strip(" \t\"'“”‘’()[]{}«»,.;:!?…-–—").lower()


def syllable_count(text: str) -> int:
    """Spoken syllables: one per Vietnamese word; numbers, acronyms and Latin words estimated."""
    total = 0
    for token in _TOKEN_RE.findall(text or ""):
        if _DIGITS_RE.match(token):
            # "10" → mười (1-2), "2026" → hai nghìn không trăm hai mươi sáu (7).
            total += max(1, round(len(token) * 1.6))
        elif token.isupper() and 1 < len(token) <= 5 and token.isascii():
            total += len(token)  # Acronyms are spelled out: "AI" → a-i.
        elif token.isascii() and len(token) > 4 and not _VI_MARKS_RE.search(token):
            total += max(1, len(_VOWEL_GROUP_RE.findall(token)))  # "Cybercab" → 3.
        else:
            total += 1
    return total


def ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END_RE.search((text or "").strip()))


def ends_clause(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(_SENTENCE_END_RE.search(stripped) or _CLAUSE_END_RE.search(stripped))


def starts_lowercase(text: str) -> bool:
    stripped = (text or "").lstrip(" \"'“‘([-–—")
    return bool(stripped) and stripped[0].isalpha() and stripped[0].islower()


def _is_name_word(word: str) -> bool:
    core = word.strip("\"'“”‘’()[]")
    return bool(core) and core[0].isupper() and not core.isupper()


def break_penalty(left: str, right: str) -> tuple[int, str | None]:
    """How bad it is to break a line (or a cue) between ``left`` and ``right`` words.

    Returns (penalty, reason). Negative penalties reward natural boundaries.
    """
    if not left or not right:
        return 0, None
    last, first = bare(left), bare(right)
    if _CLAUSE_END_RE.search(left) or _SENTENCE_END_RE.search(left):
        return -10, None
    if last in DANGLING_WORDS:
        return 14, "dangling"
    if first in LEADING_FORBIDDEN:
        return 12, "particle"
    if _DIGITS_RE.match(last.replace(".", "").replace(",", "")) and first.isalpha():
        return 12, "number"  # "10 | năm", "20 | triệu".
    if last in NUMBER_WORDS and first in NUMBER_TAILS:
        return 14, "number"  # "hai | mươi", "ba | trăm".
    if _is_name_word(left) and _is_name_word(right):
        return 10, "name"  # "Trung | Quốc", "Sao | Hỏa".
    if first in CLAUSE_OPENERS:
        return -5, None
    return 0, None


def wrap_break_issue(lines: list[str]) -> tuple[str, str] | None:
    """The first bad break inside already-wrapped display lines, as (reason, around-the-break)."""
    for upper, lower in zip(lines, lines[1:]):
        upper_words, lower_words = upper.split(), lower.split()
        if not upper_words or not lower_words:
            continue
        penalty, reason = break_penalty(upper_words[-1], lower_words[0])
        if reason and penalty > 0:
            return reason, f"{upper_words[-1]} | {lower_words[0]}"
    return None


def best_split_index(text: str, min_ratio: float = 0.25) -> int | None:
    """Character index of the most natural place to cut a cue in two (None when nothing fits).

    Prefers punctuation and clause openers near the middle and never leaves a
    dangling function word at the end of the first part.
    """
    tokens = words(text)
    if len(tokens) < 4:
        return None
    total = len(text)
    best: tuple[float, int] | None = None
    offset = 0
    positions: list[int] = []
    for token in tokens:
        offset = text.index(token, offset) + len(token)
        positions.append(offset)
    for index in range(1, len(tokens)):
        cut = positions[index - 1]
        ratio = cut / total
        if ratio < min_ratio or ratio > 1 - min_ratio:
            continue
        penalty, _ = break_penalty(tokens[index - 1], tokens[index])
        score = abs(ratio - 0.5) * 40 + penalty
        if best is None or score < best[0]:
            best = (score, cut)
    if best is None or best[0] > 14:
        return None
    return best[1]
