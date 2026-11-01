"""LLM helpers for viral discovery, on top of the assistant's configured provider.

All functions degrade gracefully: when the assistant is not configured (or the call
fails) keyword expansion falls back to the raw topic and translation is skipped.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..assistant.engine import parse_json_object
from ..assistant.providers import ProviderError, TurnResult, build_provider
from ..assistant.settings import load_settings, provider_configured

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def llm_status() -> dict[str, Any]:
    settings = load_settings()
    name = settings.get("active_provider")
    ok, reason = provider_configured(settings, name)
    section = settings.get(name) or {}
    return {"configured": ok, "reason": reason, "provider": name, "model": section.get("model", "")}


async def _complete(system: str, user: str) -> str:
    """One non-streaming-style turn (we drain the provider's stream). Tests patch this."""
    settings = load_settings()
    name = settings.get("active_provider")
    ok, reason = provider_configured(settings, name)
    if not ok:
        raise LLMUnavailable("not_configured", reason)
    provider = build_provider(settings, name)
    result: TurnResult | None = None
    try:
        async for event in provider.stream_turn(system, [{"role": "user", "content": user}], None, effort="low"):
            if isinstance(event, TurnResult):
                result = event
    except ProviderError as exc:
        raise LLMUnavailable(exc.code, exc.message) from exc
    if result is None:
        raise LLMUnavailable("provider_error", "Mô hình không trả lời.")
    if result.stop_reason == "refusal":
        raise LLMUnavailable("refusal", "Mô hình từ chối yêu cầu này.")
    return result.text or ""


async def complete_json(system: str, user: str) -> dict[str, Any]:
    raw = await _complete(system, user)
    data = parse_json_object(raw)
    if data is None:
        raw = await _complete(system, user + "\n\nChỉ trả về đúng một đối tượng JSON hợp lệ, không thêm chữ nào khác.")
        data = parse_json_object(raw)
    if data is None:
        raise LLMUnavailable("invalid_output", "Mô hình không trả về JSON hợp lệ.")
    return data


def run_sync(coro):
    """Run an async helper from a worker thread (no running loop there)."""
    return asyncio.run(coro)


# ------------------------------------------------------------------ keyword expansion

EXPAND_SYSTEM = (
    "You are a social-video research assistant for Vietnamese creators who dub foreign videos "
    "into Vietnamese. Given a topic (usually in Vietnamese), produce short search queries that "
    "native creators would actually type. Return JSON only."
)


def _clean_list(value: Any, limit: int, max_len: int = 80) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n;]+", value)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip().strip("#\"'")
        if text and text.lower() not in {o.lower() for o in out} and len(text) <= max_len:
            out.append(text)
    return out[:limit]


def fallback_keywords(topic: str) -> dict[str, list[str]]:
    topic = topic.strip()
    return {"youtube": [topic] if topic else [], "douyin": [topic] if topic else []}


async def expand_keywords(topic: str) -> dict[str, Any]:
    topic = (topic or "").strip()[:200]
    user = (
        f"Topic: {topic}\n\n"
        "Return JSON: {\"youtube\": [3-5 English YouTube search queries], "
        "\"douyin\": [3-5 Simplified Chinese Douyin search queries], "
        "\"synonyms_vi\": [2-4 related Vietnamese phrasings]}. "
        "Queries: 1-4 words each, high search intent, no hashtags, no quotes."
    )
    try:
        data = await complete_json(EXPAND_SYSTEM, user)
    except LLMUnavailable as exc:
        return {**fallback_keywords(topic), "synonyms_vi": [], "source": "fallback", "error": exc.code, "message": exc.message}
    youtube = _clean_list(data.get("youtube"), 6) or [topic]
    douyin = _clean_list(data.get("douyin"), 6) or [topic]
    return {"youtube": youtube, "douyin": douyin, "synonyms_vi": _clean_list(data.get("synonyms_vi"), 5), "source": "llm"}


# ------------------------------------------------------------------- title translation

TRANSLATE_SYSTEM = "You translate short social-video titles into natural Vietnamese. Return JSON only."


async def translate_titles(titles: dict[str, str]) -> dict[str, str]:
    """``{id: title}`` -> ``{id: vietnamese title}`` (missing ids = not translated)."""
    if not titles:
        return {}
    lines = "\n".join(f"{key}\t{value[:300]}" for key, value in titles.items())
    user = (
        "Translate each title to natural, concise Vietnamese (keep names/brands, drop hashtags). "
        "Input lines are `id<TAB>title`. Return JSON {\"<id>\": \"<vietnamese>\", ...}.\n\n" + lines
    )
    data = await complete_json(TRANSLATE_SYSTEM, user)
    return {str(k): str(v).strip() for k, v in data.items() if str(k) in titles and str(v or "").strip()}


# ---------------------------------------------------------------------- AI analysis

ANALYZE_SYSTEM = (
    "Bạn là chuyên gia chiến lược nội dung video ngắn và YouTube cho nhà sáng tạo Việt Nam chuyên "
    "lồng tiếng video nước ngoài. Phân tích ngắn gọn, cụ thể, dựa trên số liệu được cung cấp. "
    "Chỉ trả về JSON."
)


async def analyze_video(video: dict[str, Any]) -> dict[str, Any]:
    stats = video.get("stats") or {}
    breakdown = video.get("breakdown") or {}
    user = (
        "Video:\n"
        f"- Nền tảng: {video.get('platform')}\n"
        f"- Tiêu đề gốc: {video.get('title')}\n"
        f"- Mô tả: {(video.get('description') or '')[:800]}\n"
        f"- Kênh: {video.get('creator_name')} ({video.get('followers') or 'không rõ'} người theo dõi)\n"
        f"- Thời lượng: {video.get('duration') or 'không rõ'} giây; đăng: {video.get('published_at') or 'không rõ'}\n"
        f"- Lượt xem: {stats.get('views')}, thích: {stats.get('likes')}, bình luận: {stats.get('comments')}, chia sẻ: {stats.get('shares')}\n"
        f"- Điểm viral: {video.get('score')}/100; chỉ số: {breakdown.get('metrics')}\n\n"
        "Trả về JSON với các khóa (tiếng Việt):\n"
        "{\"why_viral\": [2-4 lý do ngắn], \"hook\": \"mô tả hook 3 giây đầu có thể có\", "
        "\"title_vi\": \"tiêu đề tiếng Việt hấp dẫn cho bản lồng tiếng\", \"alt_titles_vi\": [2 tiêu đề khác], "
        "\"angle\": \"góc khai thác cho khán giả Việt\", \"dub_tips\": [1-3 lưu ý khi lồng tiếng], "
        "\"risks\": [0-2 rủi ro: bản quyền, nội dung nhạy cảm, khó lồng tiếng]}"
    )
    data = await complete_json(ANALYZE_SYSTEM, user)
    return {
        "why_viral": _clean_list(data.get("why_viral"), 5, 400),
        "hook": str(data.get("hook") or "").strip(),
        "title_vi": str(data.get("title_vi") or "").strip(),
        "alt_titles_vi": _clean_list(data.get("alt_titles_vi"), 3, 200),
        "angle": str(data.get("angle") or "").strip(),
        "dub_tips": _clean_list(data.get("dub_tips"), 4, 400),
        "risks": _clean_list(data.get("risks"), 3, 400),
    }
