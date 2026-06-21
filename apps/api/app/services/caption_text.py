from __future__ import annotations

import re
import unicodedata


_BRACKETED_TEXT_RE = re.compile(
    r"\[(?P<square>[^\[\]\r\n]{1,100})\]"
    r"|\((?P<round>[^()\r\n]{1,100})\)"
    r"|【(?P<cjk_square>[^【】\r\n]{1,100})】"
    r"|（(?P<cjk_round>[^（）\r\n]{1,100})）"
)

_NORMALIZED_SOUND_KEYWORDS = (
    "nhac",
    "am nhac",
    "vo tay",
    "tieng vo tay",
    "tieng cuoi",
    "reo ho",
    "co vu",
    "tieng dong",
    "hieu ung am thanh",
    "music",
    "background music",
    "bgm",
    "applause",
    "applauding",
    "clapping",
    "laughter",
    "laughing",
    "laughs",
    "cheering",
    "sound effect",
    "sfx",
    "instrumental",
    "singing",
    "humming",
)

_RAW_SOUND_KEYWORDS = ("cười",)

_CJK_SOUND_KEYWORDS = (
    "音乐",
    "音樂",
    "掌声",
    "掌聲",
    "笑声",
    "笑聲",
    "欢呼",
    "歡呼",
    "拍手",
    "音楽",
    "笑い",
    "歓声",
    "음악",
    "박수",
    "웃음",
    "환호",
)


def remove_non_speech_tags(text: str | None) -> str:
    """Remove bracketed sound descriptions while preserving spoken text."""
    value = text or ""

    def replace_tag(match: re.Match[str]) -> str:
        content = next((group for group in match.groups() if group is not None), "")
        return " " if _is_sound_description(content) else match.group(0)

    value = _BRACKETED_TEXT_RE.sub(replace_tag, value)
    value = re.sub(r"[♪♫♬♩]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([,.;:!?…])", r"\1", value)
    value = re.sub(r"^[\s\-–—:;|]+", "", value)
    value = re.sub(r"[\s\-–—:;|]+$", "", value)
    if not re.search(r"\w", value, flags=re.UNICODE):
        return ""
    return value.strip()


def _is_sound_description(value: str) -> bool:
    normalized = _normalize_for_matching(value)
    if any(keyword in normalized for keyword in _NORMALIZED_SOUND_KEYWORDS):
        return True
    if any(keyword in value.casefold() for keyword in _RAW_SOUND_KEYWORDS):
        return True
    return any(keyword in value for keyword in _CJK_SOUND_KEYWORDS)


def _normalize_for_matching(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    ascii_like = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", ascii_like).strip()
