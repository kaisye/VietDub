"""Auto review — condense / expand a Vietnamese dub line with an LLM.

Uses whatever is configured, in order: the translation backend that dubbed the
video (OpenRouter or the local OpenAI-compatible router), then the assistant
provider (Anthropic / OpenAI-compatible). Every failure degrades to "no
rewrite": the timing-only fixes still work without any LLM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

from . import vi_text

log = logging.getLogger(__name__)

Completer = Callable[[str, str], str]
BATCH_SIZE = 8
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")

SYSTEM_PROMPT = """You edit Vietnamese voice-over (dubbing) scripts.
Each line must be spoken by a TTS voice inside a fixed time slot, so its length is
measured in Vietnamese syllables (one syllable = one written word).

For every item rewrite the Vietnamese line "current" so it has about
"target_syllables" syllables:
- mode "condense": AT MOST target_syllables. First remove filler and intensifiers
  ("thực sự", "vô cùng", "cực kỳ", "đồng thời", "thậm chí"…), redundancy and
  repetitions, then use shorter synonyms. Never drop facts, numbers, names, the
  main verb or the point (who does / thinks what).
- mode "expand": close to target_syllables. Make it fuller and more natural
  (complete the phrasing, add natural connectives) without inventing facts.
Rules: keep the meaning of "source" (the original line), keep proper names,
brands, numbers and technical terms exactly, keep the tone and the speaker's
voice, stay consistent with "previous" and "next" (the line must still join
them naturally: if "current" continues a sentence, the rewrite continues it too;
if it does not end the sentence, keep its first and last words unchanged).
Keep the line's ending punctuation style. Write natural spoken Vietnamese only.
Never move words from "previous" or "next" into the line: they are spoken anyway.
Count syllables carefully; slightly shorter than the target is fine, longer is not.

Reply with JSON only: {"lines": [{"id": "...", "text": "..."}]}"""


def translator_completer() -> Completer | None:
    """The configured translation endpoint (what translated this video)."""
    try:
        from .. import translator

        model = translator._translation_model()
    except Exception:  # noqa: BLE001
        return None

    def complete(system: str, user: str) -> str:
        return translator._request_translation_completion(model, system, user)

    return complete


def assistant_completer() -> Completer | None:
    """The assistant provider, when it has a usable configuration."""
    try:
        from ..assistant.engine import resolve_provider_name
        from ..assistant.providers import TurnResult, build_provider
        from ..assistant.settings import load_settings

        settings = load_settings()
        provider = build_provider(settings, resolve_provider_name(settings, None))
    except Exception:  # noqa: BLE001 — not configured.
        return None

    def complete(system: str, user: str) -> str:
        async def run() -> str:
            text = ""
            async for event in provider.stream_turn(system, [{"role": "user", "content": user}], None, effort="low"):
                if isinstance(event, TurnResult):
                    text = event.text
            return text

        return asyncio.run(run())

    return complete


def default_completers() -> list[tuple[str, Completer]]:
    found: list[tuple[str, Completer]] = []
    for name, factory in (("translator", translator_completer), ("assistant", assistant_completer)):
        completer = factory()
        if completer is not None:
            found.append((name, completer))
    return found


def build_user_prompt(items: list[dict[str, Any]], source_language: str) -> str:
    payload = [
        {
            "id": item["id"],
            "mode": item["mode"],
            "target_syllables": item["target_syllables"],
            "current_syllables": item["syllables"],
            "source": item.get("source", ""),
            "previous": item.get("previous", ""),
            "current": item["text"],
            "next": item.get("next", ""),
        }
        for item in items
    ]
    return (
        f"Original language: {source_language or 'auto'}. Target language: Vietnamese.\n"
        + json.dumps({"items": payload}, ensure_ascii=False)
    )


def parse_lines(text: str) -> dict[str, str]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return {}
    lines = data.get("lines") if isinstance(data, dict) else None
    result: dict[str, str] = {}
    for line in lines or []:
        if isinstance(line, dict) and line.get("id") and isinstance(line.get("text"), str):
            result[str(line["id"])] = re.sub(r"\s+", " ", line["text"]).strip()
    return result


def accept(item: dict[str, Any], text: str) -> str | None:
    """The rewrite when it is usable, else None."""
    if not text or _CJK_RE.search(text) or not any(char.isalpha() for char in text):
        return None
    original = item["text"].strip()
    if text == original:
        return None
    if not vi_text.ends_sentence(original) and vi_text.ends_sentence(text) and text.endswith("."):
        text = text[:-1].rstrip()  # The line continues in the next cue.
    count = vi_text.syllable_count(text)
    if item["mode"] == "condense":
        # Models overshoot syllable targets by a few; any real shortening helps and the
        # runner checks the predicted pacing before proposing it.
        if count > item["syllables"] * 0.92:
            return None
        if count < max(2, round(item["syllables"] * 0.45)):
            return None  # Gutted: meaning is surely lost.
    else:
        if count <= item["syllables"] or count > item["target_syllables"] + 3:
            return None
    if not vi_text.ends_sentence(original) and _last_word(text) != _last_word(original):
        return None  # The sentence continues in the next cue: its last word must stay.
    if vi_text.starts_lowercase(original) and _first_word(text) != _first_word(original):
        return None  # It continues the previous cue: keep the joining word.
    if _borrows_from_neighbours(original, text, item.get("previous", ""), item.get("next", "")):
        return None  # Pulled words from the next / previous line: they would be said twice.
    return text


def _last_word(text: str) -> str:
    tokens = [vi_text.bare(token) for token in text.split()]
    tokens = [token for token in tokens if token]
    return tokens[-1] if tokens else ""


def _first_word(text: str) -> str:
    tokens = [vi_text.bare(token) for token in text.split()]
    tokens = [token for token in tokens if token]
    return tokens[0] if tokens else ""


def _bigrams(text: str) -> set[tuple[str, str]]:
    tokens = [vi_text.bare(token) for token in text.split()]
    tokens = [token for token in tokens if token]
    return set(zip(tokens, tokens[1:]))


def _borrows_from_neighbours(original: str, text: str, previous: str, following: str) -> bool:
    new = _bigrams(text) - _bigrams(original)
    return bool(new & (_bigrams(previous) | _bigrams(following)))


def rewrite(
    items: list[dict[str, Any]],
    source_language: str,
    completers: list[tuple[str, Completer]] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Rewrites for ``items`` (id, mode, text, syllables, target_syllables, source, previous, next).

    Returns ({id: new text}, info) — info says which provider answered and what failed.
    """
    completers = default_completers() if completers is None else completers
    info: dict[str, Any] = {"available": bool(completers), "provider": None, "requested": len(items), "accepted": 0, "errors": []}
    results: dict[str, str] = {}
    if not completers or not items:
        return results, info
    active = list(completers)
    done = 0
    for offset in range(0, len(items), BATCH_SIZE):
        batch = items[offset : offset + BATCH_SIZE]
        prompt = build_user_prompt(batch, source_language)
        answered: dict[str, str] = {}
        while active:
            name, complete = active[0]
            try:
                answered = parse_lines(complete(SYSTEM_PROMPT, prompt))
                info["provider"] = name
                break
            except Exception as exc:  # noqa: BLE001 — try the next provider.
                message = str(exc)[:300]
                log.warning("Auto review rewrite via %s failed: %s", name, message)
                info["errors"].append(f"{name}: {message}")
                active.pop(0)
        if not active:
            break
        for item in batch:
            accepted = accept(item, answered.get(item["id"], ""))
            if accepted:
                results[item["id"]] = accepted
        done += len(batch)
        if on_progress:
            on_progress(done)
    info["accepted"] = len(results)
    info["available"] = bool(active) or bool(results)
    return results, info
