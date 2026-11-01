"""Source-language detection for discovered videos (pure, no dependencies).

Signals, strongest first:
1. Script: counts letters per Unicode block in title (x2) + description. A dominant
   non-Latin script decides (Han/Kana/Hangul/Arabic/Devanagari/Kannada/Tamil/...).
2. Platform hint (yt-dlp ``language``, YouTube API ``defaultAudioLanguage`` /
   ``defaultLanguage``) — used for Latin-script text, or when it agrees with the script.
3. Latin heuristics: Vietnamese diacritics, Spanish markers/stop-words, romanized
   Hindi/Urdu stop-words, English stop-words.

Returns an ISO-639-1-ish code ("en", "zh", "ja", "ko", "es", "vi", "hi", "ar", "kn",
"ta", ...), "other" for unrecognized Latin text, or ``None`` when there is no text.
"""
from __future__ import annotations

import re

# Languages the "Ngôn ngữ gốc" filter offers explicitly; anything else is "other".
FILTER_LANGUAGES = ("en", "zh", "ja", "ko", "es")
PLATFORM_DEFAULT = {"youtube": "en", "douyin": "zh"}

_SCRIPTS: list[tuple[str, str]] = [
    ("ja", r"぀-ヿㇰ-ㇿ"),  # kana (checked before Han)
    ("zh", r"㐀-䶿一-鿿豈-﫿"),
    ("ko", r"가-힯ᄀ-ᇿ㄰-㆏"),
    ("ar", r"؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿"),  # Arabic script (Arabic, Urdu, Persian)
    ("hi", r"ऀ-ॿ"),  # Devanagari
    ("bn", r"ঀ-৿"),
    ("pa", r"਀-੿"),
    ("gu", r"઀-૿"),
    ("ta", r"஀-௿"),
    ("te", r"ఀ-౿"),
    ("kn", r"ಀ-೿"),
    ("ml", r"ഀ-ൿ"),
    ("th", r"฀-๿"),
    ("ru", r"Ѐ-ӿ"),
]
_SCRIPT_RE = {code: re.compile(f"[{ranges}]") for code, ranges in _SCRIPTS}
_LATIN_RE = re.compile(r"[A-Za-zÀ-ɏḀ-ỿ]")
# Letters that only Vietnamese uses (â/ê/ô are shared with French, so they are left out).
_VI_RE = re.compile(r"[ăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.I)
_WORD_RE = re.compile(r"[a-zñáéíóúü']+", re.I)

_EN = {"the", "and", "how", "to", "with", "you", "this", "of", "in", "for", "is", "my", "your", "what",
       "best", "easy", "these", "that", "it", "on", "i", "we", "make", "why", "never", "every", "will"}
_ES = {"de", "la", "que", "el", "los", "las", "para", "con", "cómo", "como", "receta", "una", "por",
       "fácil", "más", "trucos", "cocina", "y", "en", "del"}
_HI_LATIN = {"ka", "ki", "ke", "hai", "aur", "kaise", "kya", "se", "mein", "ko", "ho", "nahi", "bhi",
             "wala", "wali", "karne", "banaye", "tarika", "kare", "karen", "karein", "jaldi", "tez", "tareeka",
             "tareen", "ghar", "saaf", "apne", "liye", "kaam", "bahut", "sabse", "jaye", "jayega"}


def normalize_code(value: str | None) -> str | None:
    if not value:
        return None
    code = str(value).strip().lower().replace("_", "-").split("-")[0]
    if code in {"zxx", "und", "mul", ""}:
        return None
    return {"iw": "he", "in": "id", "ur": "ar", "fa": "ar"}.get(code, code)


def _script_counts(text: str) -> dict[str, int]:
    counts = {code: len(rx.findall(text)) for code, rx in _SCRIPT_RE.items()}
    if counts["ja"]:  # Japanese mixes kana + kanji: count the Han too.
        counts["ja"] += counts["zh"]
        counts["zh"] = 0
    return counts


def _latin_guess(text: str) -> str:
    if len(_VI_RE.findall(text)) >= 2:
        return "vi"
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return "other"
    en = sum(w in _EN for w in words)
    es = sum(w in _ES for w in words) + (3 if re.search(r"[ñ¿¡]", text) else 0)
    hi = sum(w in _HI_LATIN for w in words)
    # Romanized Hindi/Urdu titles mix in English words ("How To Clean ... Ka Tarika"):
    # a handful of Hindi function words is a strong signal on its own.
    if hi >= 3 and hi >= 0.5 * en:
        return "hi"
    best = max((en, "en"), (es, "es"), (hi, "hi"))
    if best[0] == 0:
        # No stop-words: short ASCII titles are most often English on YouTube search.
        return "en" if all(ord(c) < 128 for c in text if c.isalpha()) else "other"
    if best[1] == "es" and es <= en:
        return "en"
    return best[1]


def detect_language(title: str = "", description: str = "", hint: str | None = None) -> str | None:
    title = title or ""
    description = (description or "")[:1500]
    text = f"{title}\n{title}\n{description}"  # title counts double
    hint_code = normalize_code(hint)
    counts = _script_counts(text)
    latin = len(_LATIN_RE.findall(text))
    script, script_n = max(counts.items(), key=lambda kv: kv[1])
    total = latin + sum(counts.values())
    if not total:
        return hint_code
    if script_n and script_n >= 0.2 * total:
        return script
    title_latin = _latin_guess(title) if _LATIN_RE.search(title) else None
    guess = "vi" if title_latin == "vi" else _latin_guess(text)  # a Vietnamese title wins
    if hint_code and hint_code not in {"zh", "ja", "ko"}:
        # Channel metadata often says "en" by default: trust strong text evidence first.
        return guess if guess in {"vi", "hi"} else hint_code
    return guess


def filter_bucket(code: str | None) -> str | None:
    """Map a detected language to the filter options (en/zh/ja/ko/es/other)."""
    if code is None:
        return None
    return code if code in FILTER_LANGUAGES else "other"


def expected_language(choice: str | None, platform: str) -> str | None:
    """Resolve the "Ngôn ngữ gốc" choice for a platform. ``None`` = any."""
    if not choice or choice == "auto":
        return PLATFORM_DEFAULT.get(platform)
    if choice == "any":
        return None
    return choice


def matches(code: str | None, expected: str | None) -> bool:
    """Unknown language (no text) is kept; otherwise the bucket must match."""
    if expected is None or code is None:
        return True
    return filter_bucket(code) == expected
