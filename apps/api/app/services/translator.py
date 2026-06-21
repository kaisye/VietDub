from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from .caption_text import remove_non_speech_tags
from .runtime_settings import get_runtime_settings
from .speech_rate import SpeechRateProfile, speech_rate_translation_hint
from .storage import ensure_storage
from .subtitle import SourceTranscriptArtifacts


@dataclass(frozen=True)
class IndexedSubtitleCue:
    index: str
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SentenceGroup:
    cues: list[IndexedSubtitleCue]


def translate_subtitle(
    subtitle_path: Path,
    target_language: str,
    source_language: str | None = None,
    speech_rate: SpeechRateProfile | None = None,
    source_artifacts: SourceTranscriptArtifacts | None = None,
    translation_profile: dict[str, Any] | None = None,
) -> Path:
    """Return the localized subtitle file used for rendering and TTS.

    TODO: Replace the NVIDIA chat-completion adapter with the final translation
    provider boundary once provider selection and glossary controls are added.
    """
    root = ensure_storage()
    destination = root / "subtitles" / f"{subtitle_path.stem}.render.srt"
    return _translate_subtitle_source_first(
        subtitle_path,
        destination,
        target_language,
        source_language,
        speech_rate,
        source_artifacts,
        translation_profile,
    )


def _translate_subtitle_source_first(
    subtitle_path: Path,
    destination: Path,
    target_language: str,
    source_language: str | None,
    speech_rate: SpeechRateProfile | None,
    source_artifacts: SourceTranscriptArtifacts | None,
    translation_profile: dict[str, Any] | None,
) -> Path:
    semantic_path = normalize_subtitle_for_translation(
        subtitle_path,
        force_semantic=_is_hardsub_ocr_source(subtitle_path),
    )
    source_text = semantic_path.read_text(encoding="utf-8", errors="ignore")
    effective_source_language = (
        source_artifacts.dominant_language
        if source_artifacts and source_artifacts.dominant_language not in {"", "unknown", "mixed"}
        else source_language or "auto"
    )

    if _same_language(effective_source_language, target_language):
        translated = source_text
        translated_segments = _segments_payload(
            _parse_indexed_srt_cues(source_text),
            effective_source_language,
            target_language,
            source_artifacts,
        )
    elif os.getenv("AETHER_ALLOW_LOCAL_SUBTITLE_GENERATION") == "1":
        translated = source_text
        translated_segments = _segments_payload(
            _parse_indexed_srt_cues(source_text),
            effective_source_language,
            target_language,
            source_artifacts,
        )
    else:
        translated, translated_segments = _translate_contextual_segments(
            source_text,
            effective_source_language,
            target_language,
            speech_rate,
            source_artifacts,
            translation_profile,
            dedupe_near_duplicates=_is_hardsub_ocr_source(subtitle_path),
        )

    destination.write_text(_format_render_subtitles(translated), encoding="utf-8")
    translated_segments_path(destination).write_text(
        json.dumps(translated_segments, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination


def translated_segments_path(render_subtitle_path: Path) -> Path:
    return render_subtitle_path.with_suffix(".translated-segments.json")


def _translate_contextual_segments(
    source_srt: str,
    source_language: str,
    target_language: str,
    speech_rate: SpeechRateProfile | None,
    source_artifacts: SourceTranscriptArtifacts | None,
    translation_profile: dict[str, Any] | None = None,
    *,
    dedupe_near_duplicates: bool = False,
) -> tuple[str, dict[str, object]]:
    cues = _parse_indexed_srt_cues(source_srt)
    if not cues:
        raise RuntimeError("Source-first translation found no semantic source segments.")
    cues = _prepare_contextual_source_cues(cues, translation_profile)
    if not cues:
        raise RuntimeError("Source-first translation found no spoken segments after removing non-speech captions.")

    max_blocks = _optional_positive_int("AETHER_TRANSLATION_MAX_BLOCKS")
    if max_blocks:
        cues = cues[:max_blocks]

    translated_by_id: dict[str, str] = {}
    batches = _contextual_translation_batches(cues)
    for batch_index, batch in enumerate(batches):
        previous_context = batches[batch_index - 1][-2:] if batch_index > 0 else []
        next_context = batches[batch_index + 1][:2] if batch_index + 1 < len(batches) else []
        translated_by_id.update(
            _translate_contextual_batch_resilient(
                batch,
                source_language,
                target_language,
                speech_rate,
                previous_context,
                next_context,
                bool(source_artifacts and source_artifacts.needs_review),
                translation_profile,
            )
        )

    blocks: list[str] = []
    translated_cues: list[IndexedSubtitleCue] = []
    for cue in cues:
        translated_text = _normalize_display_text(translated_by_id.get(cue.index, ""))
        if not translated_text:
            raise RuntimeError(f"Source-first translation returned no text for segment {cue.index}.")
        translated_cue = IndexedSubtitleCue(
            index=cue.index,
            start=cue.start,
            end=cue.end,
            text=translated_text,
        )
        translated_cues.append(translated_cue)

    if dedupe_near_duplicates:
        translated_cues = _dedupe_near_duplicate_cues(translated_cues)

    for translated_cue in translated_cues:
        blocks.append(
            f"{translated_cue.index}\n"
            f"{_format_srt_time(translated_cue.start)} --> {_format_srt_time(translated_cue.end)}\n"
            f"{translated_cue.text}"
        )

    translated_srt = "\n\n".join(blocks).strip() + "\n"
    return translated_srt, _segments_payload(
        translated_cues,
        source_language,
        target_language,
        source_artifacts,
        source_cues=cues,
    )


def _contextual_translation_batches(
    cues: list[IndexedSubtitleCue],
) -> list[list[IndexedSubtitleCue]]:
    max_cues = _optional_positive_int("AETHER_SOURCE_FIRST_TRANSLATION_BATCH_CUES") or 70
    max_chars = _optional_positive_int("AETHER_SOURCE_FIRST_TRANSLATION_BATCH_CHARS") or 5000
    batches: list[list[IndexedSubtitleCue]] = []
    current: list[IndexedSubtitleCue] = []
    current_chars = 0
    for cue in cues:
        if current and (len(current) >= max_cues or current_chars + len(cue.text) > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(cue)
        current_chars += len(cue.text)
    if current:
        batches.append(current)
    return batches


def _dedupe_near_duplicate_cues(cues: list[IndexedSubtitleCue]) -> list[IndexedSubtitleCue]:
    deduped: list[IndexedSubtitleCue] = []
    previous_key = ""
    for cue in cues:
        key = _duplicate_text_key(cue.text)
        duration = max(0.0, cue.end - cue.start)
        gap = cue.start - deduped[-1].end if deduped else 999.0
        if key and key == previous_key and duration <= 2.0 and gap <= 1.0:
            continue
        deduped.append(cue)
        previous_key = key
    return deduped


def _duplicate_text_key(text: str) -> str:
    normalized = unicodedata.normalize("NFD", _normalize_display_text(text).casefold())
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _dedup_consecutive_ocr_cues(cues: list[IndexedSubtitleCue]) -> list[IndexedSubtitleCue]:
    """Merge only lossless consecutive OCR reveals.

    Similar-looking OCR variants may contain the only correct reading of a name
    or idiom. Keep conflicting variants for semantic reconstruction; merge only
    exact repetitions or progressive reveals where one text contains the other.
    """
    if not cues:
        return cues

    merged: list[list] = []  # each entry: [start, end, text]
    for cue in cues:
        if merged:
            prev = merged[-1]
            gap = cue.start - prev[1]
            if gap <= 0.15:
                previous_key = _duplicate_text_key(prev[2])
                current_key = _duplicate_text_key(cue.text)
                is_exact = bool(previous_key and previous_key == current_key)
                is_progressive = bool(
                    previous_key
                    and current_key
                    and (
                        previous_key in current_key
                        or current_key in previous_key
                    )
                )
                if is_exact or is_progressive:
                    prev[1] = cue.end
                    # Keep the longer reveal; equal variants retain the first.
                    if len(cue.text) > len(prev[2]):
                        prev[2] = cue.text
                    continue
        merged.append([cue.start, cue.end, cue.text])

    return [
        IndexedSubtitleCue(index=str(i), start=s, end=e, text=t)
        for i, (s, e, t) in enumerate(merged, start=1)
    ]


def _prepare_contextual_source_cues(
    cues: list[IndexedSubtitleCue],
    translation_profile: dict[str, Any] | None,
) -> list[IndexedSubtitleCue]:
    if translation_profile is not None and not bool(translation_profile.get("remove_sound_tags", True)):
        return cues

    prepared: list[IndexedSubtitleCue] = []
    for cue in cues:
        spoken_text = remove_non_speech_tags(cue.text)
        if not spoken_text:
            continue
        prepared.append(
            IndexedSubtitleCue(
                index=cue.index,
                start=cue.start,
                end=cue.end,
                text=spoken_text,
            )
        )
    return prepared


def _translate_contextual_batch_resilient(
    cues: list[IndexedSubtitleCue],
    source_language: str,
    target_language: str,
    speech_rate: SpeechRateProfile | None,
    previous_context: list[IndexedSubtitleCue],
    next_context: list[IndexedSubtitleCue],
    source_needs_review: bool,
    translation_profile: dict[str, Any] | None,
) -> dict[str, str]:
    try:
        return _translate_contextual_batch(
            cues,
            source_language,
            target_language,
            speech_rate,
            previous_context,
            next_context,
            source_needs_review,
            translation_profile,
        )
    except Exception as exc:
        if len(cues) <= 1:
            raise RuntimeError(f"Contextual translation failed for segment {cues[0].index}: {exc}") from exc
        midpoint = len(cues) // 2
        left = _translate_contextual_batch_resilient(
            cues[:midpoint],
            source_language,
            target_language,
            speech_rate,
            previous_context,
            cues[midpoint : midpoint + 2],
            source_needs_review,
            translation_profile,
        )
        right = _translate_contextual_batch_resilient(
            cues[midpoint:],
            source_language,
            target_language,
            speech_rate,
            cues[max(0, midpoint - 2) : midpoint],
            next_context,
            source_needs_review,
            translation_profile,
        )
        return {**left, **right}


def _translate_contextual_batch(
    cues: list[IndexedSubtitleCue],
    source_language: str,
    target_language: str,
    speech_rate: SpeechRateProfile | None,
    previous_context: list[IndexedSubtitleCue],
    next_context: list[IndexedSubtitleCue],
    source_needs_review: bool,
    translation_profile: dict[str, Any] | None,
) -> dict[str, str]:
    segments = _contextual_segment_payload(cues, target_language)
    context_before = [cue.text for cue in previous_context]
    context_after = [cue.text for cue in next_context]
    review_rule = (
        "- Some source spans were flagged as uncertain. Resolve obvious ASR wording from scene context, "
        "but do not invent names, facts, or events.\n"
        if source_needs_review
        else ""
    )
    remove_sound_tags = translation_profile is None or bool(
        translation_profile.get("remove_sound_tags", True)
    )
    sound_tag_rule = (
        "Do not output non-speech labels such as [music], [applause], [laughter], "
        "or their translated equivalents.\n"
        if remove_sound_tags
        else ""
    )
    prompt = (
        "Translate speech segments for dubbing.\n"
        f"Source language: {source_language}\n"
        f"Target language: {target_language}\n\n"
        f"{_core_translation_rules(source_language, target_language, translation_profile)}"
        f"{sound_tag_rule}"
        "Return one item per input ID. Do not create timestamps or change IDs.\n"
        f"{review_rule}"
        f"{_speech_rate_prompt_section(speech_rate)}"
        f"{_contextual_output_limit(target_language)}"
        "Return only JSON in this exact shape:\n"
        '{"translations":[{"id":"1","text":"translated text"}]}\n\n'
        f"Previous context:\n{_json_compact(context_before)}\n\n"
        f"Translate:\n{_json_compact(segments)}\n\n"
        f"Next context:\n{_json_compact(context_after)}"
    )
    content = _request_translation_completion(
        model=_translation_model(),
        system_prompt="You are a context-aware audiovisual localization translator.",
        user_prompt=prompt,
    )
    payload = _extract_json_object(content)
    raw_translations = payload.get("translations")
    if not isinstance(raw_translations, list):
        raise RuntimeError("Contextual translation did not return a translations list.")

    expected_ids = [cue.index for cue in cues]
    translated: dict[str, str] = {}
    for item in raw_translations:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        text = _normalize_display_text(str(item.get("text") or ""))
        if item_id in expected_ids and text:
            translated[item_id] = text
    if set(translated) != set(expected_ids):
        missing = ", ".join(item for item in expected_ids if item not in translated)
        raise RuntimeError(f"Contextual translation omitted segment IDs: {missing}.")
    return translated


def _contextual_segment_payload(
    cues: list[IndexedSubtitleCue],
    target_language: str | None = None,
) -> list[dict[str, object]]:
    is_vi = _language_key(target_language) == "vi"
    result: list[dict[str, object]] = []
    for cue in cues:
        entry: dict[str, object] = {"id": cue.index, "text": cue.text}
        if is_vi:
            duration = max(0.0, cue.end - cue.start)
            source_units = max(1, _word_count(cue.text))
            ratio_max = max(1, round(source_units * _translation_max_syllable_ratio()))
            duration_max = max(1, round(duration * 3.8))
            entry["max"] = min(ratio_max, duration_max)
        result.append(entry)
    return result


def _json_compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _contextual_output_limit(target_language: str | None = None) -> str:
    if _language_key(target_language) != "vi":
        return ""
    return (
        "Each segment has a max field derived from its time slot. Keep the Vietnamese output at or below "
        "that many words/syllables while preserving the full meaning.\n"
    )


def _core_translation_rules(
    source_language: str,
    target_language: str,
    translation_profile: dict[str, Any] | None,
) -> str:
    return (
        "Core translation rules:\n"
        "- Translate for audiovisual dubbing/TTS, not word-by-word subtitles.\n"
        "- Use full scene context, neighboring segments, genre, intent, idioms, and references before translating.\n"
        "- Preserve factual content: names, places, numbers, units, chronology, actions, technical terms, and cause/effect.\n"
        "- Match spoken length to the cue's time slot: you may paraphrase, expand with natural phrasing, or condense wording "
        "so the translation reads at a natural pace within the cue duration. "
        "Never add or remove facts, names, numbers, or events — only adapt wording.\n"
        "- Do not add commentary, invented details, or information not present in the source.\n"
        "- Source may come from OCR/ASR and may contain broken characters, duplicated fragments, or punctuation errors. "
        "Resolve only obvious corruption from context; do not invent unsupported names, facts, or events.\n"
        "- Preserve numeric formats and exact values unless the target language convention clearly requires wording them out. "
        "Never insert spaces inside grouped numbers, decimals, fractions, or units.\n"
        f"- Translate naturally into {target_language}; keep the original meaning from {source_language}.\n"
        f"{_translation_profile_prompt(translation_profile, include_remove_sound_tags=False)}"
    )


def _translation_profile_prompt(
    profile: dict[str, Any] | None,
    *,
    include_remove_sound_tags: bool = True,
) -> str:
    if not profile:
        return ""
    tone = str(profile.get("tone") or "").strip()
    context = str(profile.get("context") or "").strip()
    proper_names = str(profile.get("proper_names") or "preserve").strip()
    fidelity = str(profile.get("semantic_fidelity") or "balanced").strip()
    glossary = profile.get("glossary")
    lines = ["\nProduction translation profile:"]
    if tone and tone.casefold() != "natural and context-aware":
        lines.append(f"- Tone: {tone}")
    if fidelity and fidelity != "balanced":
        lines.append(f"- Semantic fidelity: {fidelity}")
    proper_name_rule = _proper_names_prompt_rule(proper_names)
    if proper_name_rule:
        lines.append(proper_name_rule)
    if context:
        lines.append(f"- Project/scene context: {context}")
    if isinstance(glossary, dict) and glossary:
        lines.append(f"- Required glossary: {json.dumps(glossary, ensure_ascii=False)}")
    if include_remove_sound_tags and bool(profile.get("remove_sound_tags", True)):
        lines.append("- Remove non-speech labels from the translated output.")
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def _proper_names_prompt_rule(policy: str) -> str:
    if policy == "preserve":
        return (
            "- Proper names: preserve established names from the glossary/context and use canonical public names "
            "(for people, places, theories, products). Do not invent Vietnamese phonetic spellings from OCR-damaged text; "
            "if a name is clearly recognizable from context, use the established form."
        )
    if policy == "transliterate":
        return "- Proper names: transliterate consistently, unless the glossary/context specifies an established form."
    if policy == "localize":
        return "- Proper names: localize only when natural and conventional; otherwise preserve the established form."
    return ""


def _segments_payload(
    translated_cues: list[IndexedSubtitleCue],
    source_language: str,
    target_language: str,
    source_artifacts: SourceTranscriptArtifacts | None,
    *,
    source_cues: list[IndexedSubtitleCue] | None = None,
) -> dict[str, object]:
    source_by_id = {cue.index: cue for cue in source_cues or translated_cues}
    return {
        "version": 1,
        "source_language": source_language,
        "target_language": target_language,
        "source_quality": {
            "needs_review": bool(source_artifacts and source_artifacts.needs_review),
            "timestamp_quality": source_artifacts.timestamp_quality if source_artifacts else "unknown",
        },
        "segments": [
            {
                "id": cue.index,
                "start": cue.start,
                "end": cue.end,
                "source_text": source_by_id.get(cue.index, cue).text,
                "translated_text": cue.text,
            }
            for cue in translated_cues
        ],
    }


def _is_hardsub_ocr_source(subtitle_path: Path) -> bool:
    """Hard-sub OCR writes ``*.ocr.srt``. OCR splits one sentence across many
    short on-screen captions, so its cues must be merged into semantic sentences
    before translation — otherwise each fragment is translated out of context."""
    return ".ocr." in subtitle_path.name.lower()


def normalize_subtitle_for_translation(subtitle_path: Path, *, force_semantic: bool = False) -> Path:
    if subtitle_path.name.endswith(".normalized.srt"):
        return subtitle_path

    root = ensure_storage()
    source_text = subtitle_path.read_text(encoding="utf-8", errors="ignore")
    normalized = _normalize_subtitle_source(source_text, force_semantic=force_semantic)
    if not normalized.strip() or normalized == source_text:
        return subtitle_path

    normalized_path = root / "subtitles" / f"{subtitle_path.stem}.normalized.srt"
    normalized_path.write_text(normalized, encoding="utf-8")
    return normalized_path


# CJK noise word filter ──────────────────────────────────────────────────────
# YouTube ASR sometimes inserts the English word "you" into Chinese auto-captions
# as a false recognition of phonemes like 有/又/哟. Strip these deterministically
# before any structural normalization. Only active when the dominant script is CJK
# to avoid false-positives in English or mixed-language content.

_CJK_CHAR_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


def _cjk_noise_words() -> list[str]:
    raw = os.getenv("AETHER_CJK_NOISE_WORDS", "you").strip()
    return [w.strip() for w in raw.split(",") if w.strip()]


def _is_cjk_dominant_srt(cues: list[IndexedSubtitleCue]) -> bool:
    all_text = "".join(cue.text for cue in cues)
    if not all_text:
        return False
    return len(_CJK_CHAR_RE.findall(all_text)) / len(all_text) >= 0.15


def _filter_cjk_noise_from_srt(srt_text: str, words: list[str]) -> str:
    patterns = [re.compile(r"(?i)\b" + re.escape(w) + r"\b") for w in words]

    def _filter_block(block: str) -> str:
        lines = block.splitlines()
        timing_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing_idx is None:
            return block
        header = lines[: timing_idx + 1]
        text_lines = []
        for ln in lines[timing_idx + 1 :]:
            filtered = ln
            for pat in patterns:
                filtered = pat.sub("", filtered)
            filtered = re.sub(r"  +", " ", filtered).strip()
            if filtered:
                text_lines.append(filtered)
        return "\n".join(header + text_lines)

    filtered_blocks = [_filter_block(b) for b in _split_srt_blocks(srt_text)]
    return "\n\n".join(filtered_blocks) + "\n" if filtered_blocks else srt_text


def _normalize_subtitle_source(source_text: str, *, force_semantic: bool = False) -> str:
    cues = _parse_indexed_srt_cues(source_text)
    if not cues:
        return source_text

    noise_words = _cjk_noise_words()
    if noise_words and _is_cjk_dominant_srt(cues):
        source_text = _filter_cjk_noise_from_srt(source_text, noise_words)
        cues = _parse_indexed_srt_cues(source_text)

    if force_semantic and _is_cjk_dominant_srt(cues):
        source_text = _filter_cjk_ocr_artifacts_from_srt(source_text)
        cues = _parse_indexed_srt_cues(source_text)

    normalized = source_text
    if _rolling_caption_normalization_enabled() and _looks_like_rolling_captions(cues):
        normalized = _normalize_rolling_captions_deterministic(cues)
        cues = _parse_indexed_srt_cues(normalized)

    # For OCR sources, merge consecutive near-duplicate cues caused by rolling
    # reveals before the semantic LLM step. The LLM would otherwise receive the
    # same sentence 3-5 times and either pass all through (causing overlap in
    # the translation) or silently drop most (losing the timing span).
    if force_semantic:
        deduped = _dedup_consecutive_ocr_cues(cues)
        if len(deduped) < len(cues):
            blocks = [
                f"{i}\n{_format_srt_time(c.start)} --> {_format_srt_time(c.end)}\n{c.text}"
                for i, c in enumerate(deduped, start=1)
            ]
            normalized = "\n\n".join(blocks).strip() + "\n"
            cues = deduped

    if _semantic_cue_normalization_enabled() and (force_semantic or _needs_semantic_cue_normalization(cues)):
        normalized = _normalize_semantic_cues(cues, is_ocr=force_semantic)

    return normalized if normalized.strip() else source_text


def _filter_cjk_ocr_artifacts_from_srt(srt_text: str) -> str:
    blocks: list[str] = []
    for block in _split_srt_blocks(srt_text):
        lines = block.splitlines()
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        text = _clean_cjk_ocr_artifact_text(" ".join(lines[timing_index + 1 :]))
        if not text:
            continue
        blocks.append("\n".join([*lines[: timing_index + 1], text]))
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def _clean_cjk_ocr_artifact_text(text: str) -> str:
    value = _normalize_display_text(text)
    if not value:
        return ""
    value = re.sub(r"^[、，,。．.;；:：\s]+", "", value)
    value = re.sub(r"^[8８]{2,}(?=\d{3,4}\s*年)", "", value)
    value = re.sub(r"(?<=[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFFA-Za-z])\s*[8８]{2,}\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if re.fullmatch(r"[8８\s:：,，.。;；、·．]+", value):
        return ""
    return value


def _needs_semantic_cue_normalization(cues: list[IndexedSubtitleCue]) -> bool:
    max_duration = _optional_float("AETHER_SEMANTIC_CUE_MAX_DURATION", 18.0, 6.0, 90.0)
    max_units = int(_optional_float("AETHER_SEMANTIC_CUE_MAX_UNITS", 180.0, 40.0, 2000.0))
    return any(
        cue.end - cue.start > max_duration or _word_count(cue.text) > max_units
        for cue in cues
    )


def _normalize_semantic_cues(cues: list[IndexedSubtitleCue], *, is_ocr: bool = False) -> str:
    batches = _semantic_normalization_batches(cues)
    segments: list[IndexedSubtitleCue] = []
    for batch in batches:
        try:
            texts = _semantic_segments_with_llm(batch, is_ocr=is_ocr)
        except Exception:
            texts = _semantic_segments_fallback(batch)
        timed = _time_semantic_segments(batch, texts)
        segments.extend(_refine_overlong_semantic_cues(timed))

    blocks = [
        f"{index}\n{_format_srt_time(cue.start)} --> {_format_srt_time(cue.end)}\n{cue.text}"
        for index, cue in enumerate(segments, start=1)
        if cue.text and cue.end > cue.start
    ]
    return "\n\n".join(blocks).strip() + "\n" if blocks else ""


def _semantic_normalization_batches(cues: list[IndexedSubtitleCue]) -> list[list[IndexedSubtitleCue]]:
    max_cues = _optional_positive_int("AETHER_SEMANTIC_NORMALIZE_BATCH_CUES") or 40
    max_chars = _optional_positive_int("AETHER_SEMANTIC_NORMALIZE_BATCH_CHARS") or 2000
    batches: list[list[IndexedSubtitleCue]] = []
    current: list[IndexedSubtitleCue] = []
    current_chars = 0
    for cue in cues:
        cue_chars = len(cue.text)
        if current and (len(current) >= max_cues or current_chars + cue_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(cue)
        current_chars += cue_chars
    if current:
        batches.append(current)
    return batches


def _semantic_segments_with_llm(cues: list[IndexedSubtitleCue], *, is_ocr: bool = False) -> list[str]:
    source = " ".join(cue.text.strip() for cue in cues if cue.text.strip())
    ocr_extra = (
        "For OCR, fix obvious word-fusion or missing-space errors.\n"
    ) if is_ocr else ""
    prompt = (
        "Reconstruct the intended source-language speech before translation.\n"
        "Merge broken fragments across unreliable cue boundaries into complete clauses/sentences.\n"
        "Repair only contextually certain OCR/ASR errors: lookalike or homophone characters, "
        "broken names, idioms, and fixed expressions. Restore clear intended wording instead of reading corrupted tokens literally.\n"
        f"{ocr_extra}"
        "Remove exact or near-duplicate rolling fragments.\n"
        "Preserve order, facts, names, numbers, meaning, and tone. If uncertain, keep the source wording; "
        "do not translate, summarize, embellish, or guess.\n"
        "Return only compact JSON in this shape:\n"
        '{"segments":["first source segment","second source segment"]}\n\n'
        f"Transcript:\n{source}"
    )
    content = _request_translation_completion(
        model=_translation_model(),
        system_prompt="You faithfully reconstruct noisy OCR/ASR subtitles in their source language.",
        user_prompt=prompt,
    )
    payload = _extract_json_object(content)
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        raise RuntimeError("Semantic subtitle normalization did not return a segment list.")

    segments = [_normalize_display_text(str(segment)) for segment in raw_segments]
    segments = [segment for segment in segments if segment]
    if not _semantic_segments_look_safe(source, segments, is_ocr=is_ocr):
        raise RuntimeError("Semantic subtitle normalization changed the source transcript.")
    return segments


def _extract_json_object(content: str) -> dict[str, object]:
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("Semantic subtitle normalization did not return JSON.")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise RuntimeError("Semantic subtitle normalization returned invalid JSON.")
    return payload


def _semantic_content_key(text: str) -> str:
    return "".join(
        character.casefold()
        for character in text
        if not unicodedata.category(character).startswith(("P", "Z"))
    )


def _semantic_segments_look_safe(source: str, segments: list[str], *, is_ocr: bool = False) -> bool:
    if not segments:
        return False
    source_units = max(1, _word_count(source))
    segment_units = max(1, _word_count(" ".join(segments)))
    # OCR sources legitimately gain word count when fused tokens are split
    # ("spacefight" → "space fight"), so we allow a much looser upper bound.
    upper = 2.0 if is_ocr else 1.25
    if segment_units > source_units * upper:
        return False
    if segment_units < source_units * 0.45:
        return False
    return True


def _semantic_segments_fallback(cues: list[IndexedSubtitleCue]) -> list[str]:
    max_duration = _optional_float("AETHER_SEMANTIC_CUE_MAX_DURATION", 18.0, 6.0, 90.0)
    max_chars = int(_optional_float("AETHER_SEMANTIC_FALLBACK_MAX_CHARS", 220.0, 80.0, 800.0))
    segments: list[str] = []
    for cue in cues:
        text = _normalize_display_text(cue.text)
        sentence_parts = [
            part.strip()
            for part in re.split(r"(?<=[.!?。！？…])\s*", text)
            if part.strip()
        ]
        if len(sentence_parts) > 1:
            segments.extend(sentence_parts)
            continue

        minimum_parts = max(1, math.ceil((cue.end - cue.start) / max_duration))
        target_chars = min(max_chars, max(40, len(text) // minimum_parts))
        segments.extend(_fallback_split_semantic_text(text, target_chars))
    return segments


def _fallback_split_semantic_text(text: str, target_chars: int) -> list[str]:
    if len(text) <= target_chars:
        return [text]

    if re.search(r"\s", text):
        return _split_long_phrase(text, target_chars)

    parts: list[str] = []
    cursor = 0
    while len(text) - cursor > target_chars:
        target = cursor + target_chars
        search_start = max(cursor + target_chars // 2, target - target_chars // 3)
        search_end = min(len(text), target + target_chars // 3)
        boundary = max(
            (position + 1 for position in range(search_start, search_end) if text[position] in "，、；：,;:"),
            default=target,
        )
        parts.append(text[cursor:boundary].strip())
        cursor = boundary
    if cursor < len(text):
        parts.append(text[cursor:].strip())
    return [part for part in parts if part]


def _time_semantic_segments(
    source_cues: list[IndexedSubtitleCue],
    segments: list[str],
) -> list[IndexedSubtitleCue]:
    if not source_cues or not segments:
        return []

    cue_units = [max(1, _word_count(cue.text)) for cue in source_cues]
    segment_units = [max(1, _word_count(segment)) for segment in segments]
    source_total = sum(cue_units)
    segment_total = sum(segment_units)
    cursor = 0
    timed: list[IndexedSubtitleCue] = []

    for index, (segment, units) in enumerate(zip(segments, segment_units)):
        start_offset = source_total * cursor / segment_total
        cursor += units
        end_offset = source_total if index == len(segments) - 1 else source_total * cursor / segment_total
        start = _time_at_content_offset(source_cues, cue_units, start_offset)
        end = _time_at_content_offset(source_cues, cue_units, end_offset)
        end = max(start + 0.2, end)
        timed.append(
            IndexedSubtitleCue(
                index=str(index + 1),
                start=start,
                end=end,
                text=segment,
            )
        )
    return timed


def _time_at_content_offset(
    cues: list[IndexedSubtitleCue],
    cue_units: list[int],
    offset: float,
) -> float:
    remaining = max(0.0, offset)
    for cue, units in zip(cues, cue_units):
        if remaining <= units:
            ratio = remaining / max(1, units)
            return cue.start + (cue.end - cue.start) * ratio
        remaining -= units
    return cues[-1].end


def _refine_overlong_semantic_cues(cues: list[IndexedSubtitleCue]) -> list[IndexedSubtitleCue]:
    hard_max = _optional_float("AETHER_SEMANTIC_CUE_HARD_MAX_DURATION", 24.0, 10.0, 90.0)
    sparse_max_units = int(_optional_float("AETHER_SEMANTIC_SPARSE_MAX_UNITS", 12.0, 2.0, 80.0))
    sparse_units_per_second = _optional_float("AETHER_SEMANTIC_SPARSE_UNITS_PER_SECOND", 1.2, 0.5, 6.0)
    refined: list[IndexedSubtitleCue] = []

    for cue in cues:
        duration = cue.end - cue.start
        units = max(1, _word_count(cue.text))
        if units <= sparse_max_units:
            natural_duration = max(2.0, units / sparse_units_per_second)
            if duration > max(8.0, natural_duration * 2.5):
                refined.append(
                    IndexedSubtitleCue(
                        index=cue.index,
                        start=cue.start,
                        end=min(cue.end, cue.start + natural_duration),
                        text=cue.text,
                    )
                )
                continue

        if duration <= hard_max:
            refined.append(cue)
            continue

        target_chars = max(40, int(len(cue.text) * hard_max / duration * 0.9))
        parts = _fallback_split_semantic_text(cue.text, target_chars)
        if len(parts) <= 1:
            refined.append(cue)
            continue
        refined.extend(_time_semantic_segments([cue], parts))

    return refined


def translation_limit_label() -> str | None:
    max_blocks = _optional_positive_int("AETHER_TRANSLATION_MAX_BLOCKS")
    if not max_blocks:
        return None
    return f"Translation test mode is enabled: first {max_blocks} subtitle blocks only."


def _looks_like_rolling_captions(cues: list[IndexedSubtitleCue]) -> bool:
    if len(cues) < 8:
        return False

    overlaps = 0
    previous_max_end = cues[0].end
    for cue in cues[1:]:
        if cue.start < previous_max_end:
            overlaps += 1
        previous_max_end = max(previous_max_end, cue.end)

    ratio = overlaps / max(1, len(cues) - 1)
    threshold = _optional_float("AETHER_ROLLING_CAPTION_OVERLAP_RATIO", 0.25, 0.0, 1.0)
    return ratio >= threshold


def _normalize_rolling_captions_deterministic(cues: list[IndexedSubtitleCue]) -> str:
    groups = _reconstruct_sentence_groups(cues)
    if not groups:
        return ""

    min_duration = _optional_float("AETHER_ROLLING_CAPTION_MIN_DURATION", 0.6, 0.1, 5.0)
    blocks: list[str] = []
    previous_end = 0.0
    for index, group in enumerate(groups, start=1):
        raw_start = group.cues[0].start
        start = max(raw_start, previous_end)
        if index < len(groups):
            next_start = groups[index].cues[0].start
            end = next_start if next_start > start else group.cues[-1].end
        else:
            end = group.cues[-1].end
        end = max(end, start + min_duration)

        text = _normalize_display_text(" ".join(cue.text for cue in group.cues))
        blocks.append(f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}")
        previous_end = end

    return "\n\n".join(blocks).strip() + "\n"


def _speech_rate_prompt_section(speech_rate: SpeechRateProfile | None) -> str:
    return speech_rate_translation_hint(speech_rate)


def _nvidia_fallback_model() -> str:
    settings = get_runtime_settings()
    return (
        settings.translation_nvidia_model
        or os.getenv("NVIDIA_MODEL")
        or "openai/gpt-oss-120b"
    ).strip() or "openai/gpt-oss-120b"


def _request_translation_completion(model: str, system_prompt: str, user_prompt: str) -> str:
    provider = _translation_provider()
    if provider == "nvidia":
        response = _request_nvidia_translation(model, system_prompt, user_prompt)
    elif provider in {"openai-compatible", "openai_compatible", "local", "ollama", "lmstudio", "vllm"}:
        try:
            response = _request_openai_compatible_translation(model, system_prompt, user_prompt)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            if _nvidia_api_key():
                import logging
                fallback = _nvidia_fallback_model()
                logging.getLogger(__name__).warning(
                    "Local translation endpoint unreachable (%s). Falling back to NVIDIA %s.", exc, fallback,
                )
                response = _request_nvidia_translation(fallback, system_prompt, user_prompt)
            else:
                raise RuntimeError(
                    f"Local translation endpoint is not reachable and NVIDIA_API_KEY is not configured. "
                    f"Start your local LLM or add an NVIDIA API key in Settings. Error: {exc}"
                ) from exc
    else:
        raise RuntimeError(
            "Unsupported AETHER_TRANSLATION_PROVIDER. "
            "Use 'nvidia' or 'openai-compatible'."
        )

    response.raise_for_status()
    return _extract_chat_completion_content(response.json())


def _request_nvidia_translation(model: str, system_prompt: str, user_prompt: str) -> httpx.Response:
    api_key = _nvidia_api_key()
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is required when AETHER_TRANSLATION_PROVIDER=nvidia.")

    return httpx.post(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": _optional_positive_int("AETHER_TRANSLATION_MAX_TOKENS") or 32768,
            "temperature": 0.2,
            "top_p": 0.95,
            "top_k": 20,
            "presence_penalty": 0,
            "repetition_penalty": 1,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=_optional_positive_int("AETHER_TRANSLATION_TIMEOUT_SECONDS") or 300,
    )


def _request_openai_compatible_translation(model: str, system_prompt: str, user_prompt: str) -> httpx.Response:
    api_key = _local_translation_api_key()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return httpx.post(
        _local_translation_chat_url(),
        headers=headers,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": _optional_positive_int("AETHER_TRANSLATION_MAX_TOKENS") or 8192,
            "temperature": _optional_float("AETHER_TRANSLATION_TEMPERATURE", 0.2, 0.0, 2.0),
            "top_p": _optional_float("AETHER_TRANSLATION_TOP_P", 0.95, 0.0, 1.0),
            "stream": False,
        },
        timeout=_optional_positive_int("AETHER_TRANSLATION_TIMEOUT_SECONDS") or 600,
    )


def _extract_chat_completion_content(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Translation provider returned an invalid chat-completion response.") from exc
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Translation provider returned an empty translation.")
    return content.strip()

def _source_text_from_block(lines: list[str]) -> str:
    timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
    if timing_index is None:
        return ""
    text = " ".join(line for line in lines[timing_index + 1 :] if line and not line.isdigit())
    return remove_non_speech_tags(re.sub(r"\s+", " ", text).strip())


def _parse_indexed_srt_cues(srt_text: str) -> list[IndexedSubtitleCue]:
    cues: list[IndexedSubtitleCue] = []
    for position, block in enumerate(_split_srt_blocks(srt_text), start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        cue_index = lines[0] if timing_index > 0 else str(position)
        start_text, end_text = lines[timing_index].split("-->", 1)
        text = _source_text_from_block(lines)
        start = _srt_time_to_seconds(start_text.strip())
        end = _srt_time_to_seconds(end_text.strip().split()[0])
        if text and end > start:
            cues.append(IndexedSubtitleCue(index=cue_index, start=start, end=end, text=text))
    return cues


def _reconstruct_sentence_groups(cues: list[IndexedSubtitleCue]) -> list[SentenceGroup]:
    if not cues:
        return []

    groups: list[SentenceGroup] = []
    current: list[IndexedSubtitleCue] = []
    max_duration = _optional_float("AETHER_SENTENCE_MAX_DURATION", 18.0, 4.0, 90.0)
    max_chars = int(_optional_float("AETHER_SENTENCE_MAX_CHARS", 520.0, 120.0, 2000.0))

    for index, cue in enumerate(cues):
        current.append(cue)
        next_cue = cues[index + 1] if index + 1 < len(cues) else None
        current_text = " ".join(item.text for item in current)
        current_duration = current[-1].end - current[0].start
        should_close = (
            next_cue is None
            or _ends_sentence(cue.text)
            or _pause_after(cue, next_cue) >= _sentence_pause_threshold()
            or current_duration >= max_duration
            or len(current_text) >= max_chars
        )
        if should_close:
            groups.append(SentenceGroup(cues=current))
            current = []

    if current:
        groups.append(SentenceGroup(cues=current))
    return groups


def _pause_after(current: IndexedSubtitleCue, next_cue: IndexedSubtitleCue) -> float:
    return max(0.0, next_cue.start - current.end)


def _sentence_pause_threshold() -> float:
    return _optional_float("AETHER_SENTENCE_PAUSE_THRESHOLD", 0.65, 0.0, 5.0)


def _translation_max_syllable_ratio() -> float:
    return _optional_float("AETHER_TRANSLATION_MAX_SYLLABLE_RATIO", 1.35, 0.6, 2.2)


def _rolling_caption_normalization_enabled() -> bool:
    return os.getenv("AETHER_NORMALIZE_ROLLING_CAPTIONS", "1").strip().lower() not in {"0", "false", "off", "no"}


def _semantic_cue_normalization_enabled() -> bool:
    return os.getenv("AETHER_NORMALIZE_SEMANTIC_CUES", "1").strip().lower() not in {"0", "false", "off", "no"}


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.!?。！？…]['\")\]]?\s*$", text.strip()))


def _word_count(text: str) -> int:
    cjk_chars = re.findall(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text)
    if cjk_chars:
        text_without_cjk = re.sub(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", " ", text)
        latin_tokens = re.findall(r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*", text_without_cjk)
        return len(cjk_chars) + len(latin_tokens)
    return len(re.findall(r"\S+", text))


def _format_render_subtitles(srt_text: str) -> str:
    cues = _parse_srt_cues(srt_text)
    if not cues:
        return srt_text

    display_cues: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        parts = _split_display_text(text)
        if len(parts) == 1:
            display_cues.append((start, end, parts[0]))
            continue

        duration = max(0.1, end - start)
        weights = [max(1, len(part)) for part in parts]
        total_weight = sum(weights)
        cursor = start
        for index, (part, weight) in enumerate(zip(parts, weights)):
            if index == len(parts) - 1:
                part_end = end
            else:
                part_end = min(end, cursor + duration * weight / total_weight)
            if part_end <= cursor:
                part_end = min(end, cursor + 0.1)
            display_cues.append((cursor, part_end, part))
            cursor = part_end

    normalized: list[tuple[float, float, str]] = []
    for index, (start, end, text) in enumerate(display_cues):
        if index + 1 < len(display_cues):
            next_start = display_cues[index + 1][0]
            if start < next_start < end and next_start - start >= 0.35:
                end = next_start
        if end <= start:
            end = start + 0.1
        normalized.append((start, end, text))

    blocks = [
        f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}"
        for index, (start, end, text) in enumerate(normalized, start=1)
    ]
    return "\n\n".join(blocks).strip() + "\n"


def _parse_srt_cues(srt_text: str) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for block in _split_srt_blocks(srt_text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index]
        start_text, end_text = timing.split("-->", 1)
        start = _srt_time_to_seconds(start_text.strip())
        end = _srt_time_to_seconds(end_text.strip().split()[0])
        text = _normalize_display_text(" ".join(lines[timing_index + 1 :]))
        if text and end > start:
            cues.append((start, end, text))
    return cues


def _split_display_text(text: str) -> list[str]:
    text = _normalize_display_text(text)
    if not text:
        return []

    max_chars = _subtitle_line_max_chars()
    sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?。！？…])\s+", text) if part.strip()]
    if len(sentence_parts) <= 1:
        sentence_parts = [text]

    result: list[str] = []
    for part in sentence_parts or [text]:
        if len(part) <= max_chars:
            result.append(part)
            continue
        result.extend(_split_long_phrase(part, max_chars))
    return [part for part in result if part]


def _split_long_phrase(text: str, max_chars: int) -> list[str]:
    text = _normalize_display_text(text)
    if len(text) <= max_chars:
        return [text]

    words = text.split()
    if len(words) <= 1:
        return [text]

    split_index = _best_phrase_split_index(words, max_chars)
    if split_index <= 0 or split_index >= len(words):
        return _greedy_split_long_phrase(text, max_chars)

    left = " ".join(words[:split_index]).strip()
    right = " ".join(words[split_index:]).strip()
    result: list[str] = []
    for part in (left, right):
        if len(part) <= max_chars:
            result.append(part)
        else:
            result.extend(_split_long_phrase(part, max_chars))
    return result or [text]


def _best_phrase_split_index(words: list[str], max_chars: int) -> int:
    joined = " ".join(words)
    total_length = len(joined)
    target = total_length / 2
    min_part_length = min(max(28, int(max_chars * 0.35)), max_chars - 8)
    candidates: list[tuple[bool, float, int]] = []

    for index in range(1, len(words)):
        left = " ".join(words[:index])
        right = " ".join(words[index:])
        left_length = len(left)
        right_length = len(right)
        if left_length > max_chars or right_length <= 0:
            continue

        score = abs(left_length - target)
        if right_length > max_chars:
            score += (right_length - max_chars) * 2.0
        if left_length < min_part_length:
            score += (min_part_length - left_length) * 3.0
        if right_length < min_part_length:
            score += (min_part_length - right_length) * 3.0

        previous_word = words[index - 1].rstrip()
        next_word = words[index].lower().strip(" ,.;:!?()[]\"'")
        if re.search(r"[,;:]$", previous_word):
            score -= 18
        if previous_word.endswith(("-", "—", "–")):
            score -= 12
        if next_word in {"và", "nhưng", "hoặc", "rồi", "nên", "để", "and", "but", "or", "so", "while"}:
            score -= 10

        balanced = left_length >= min_part_length and right_length >= min_part_length
        candidates.append((balanced, score, index))

    balanced_candidates = [candidate for candidate in candidates if candidate[0]]
    if balanced_candidates:
        return min(balanced_candidates, key=lambda item: item[1])[2]
    if candidates:
        return min(candidates, key=lambda item: item[1])[2]
    return -1


def _greedy_split_long_phrase(text: str, max_chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > max_chars:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def _normalize_display_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\{\\.*?\}", " ", text)
    text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", text)
    text = re.sub(r"(?<=\d)([.,])\s+(?=\d{3}(?:\D|$))", r"\1", text)

    def space_after_punctuation(match: re.Match[str]) -> str:
        punctuation = match.group(1)
        previous_is_digit = match.start() > 0 and text[match.start() - 1].isdigit()
        next_is_digit = match.end() < len(text) and text[match.end()].isdigit()
        return punctuation if previous_is_digit and next_is_digit else f"{punctuation} "

    text = re.sub(r"([,.;:!?…])(?=\S)", space_after_punctuation, text)
    return remove_non_speech_tags(re.sub(r"\s+", " ", text).strip())


def _subtitle_line_max_chars() -> int:
    raw = (os.getenv("AETHER_SUBTITLE_LINE_MAX_CHARS") or "140").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 140
    return max(48, min(180, value))


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


def _format_srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _split_srt_blocks(srt_text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", srt_text.strip()) if "-->" in block]


def _translation_provider() -> str:
    settings = get_runtime_settings()
    return (settings.translation_provider or os.getenv("AETHER_TRANSLATION_PROVIDER", "nvidia")).strip().lower() or "nvidia"


def _translation_model() -> str:
    settings = get_runtime_settings()
    if _translation_provider() == "nvidia":
        return (settings.translation_nvidia_model or os.getenv("NVIDIA_MODEL", "openai/gpt-oss-120b")).strip() or "openai/gpt-oss-120b"
    return (
        settings.local_translation_model
        or os.getenv("AETHER_LOCAL_TRANSLATION_MODEL")
        or os.getenv("LOCAL_TRANSLATION_MODEL")
        or os.getenv("OLLAMA_TRANSLATION_MODEL")
        or "qwen2.5:14b"
    ).strip()


def _local_translation_chat_url() -> str:
    settings = get_runtime_settings()
    raw = (
        settings.local_translation_base_url
        or os.getenv("AETHER_LOCAL_TRANSLATION_BASE_URL")
        or os.getenv("LOCAL_TRANSLATION_BASE_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or "http://127.0.0.1:11434/v1"
    ).strip().rstrip("/")
    if raw.endswith("/chat/completions"):
        return raw
    if raw.endswith("/v1"):
        return f"{raw}/chat/completions"
    return f"{raw}/v1/chat/completions"


def _local_translation_api_key() -> str | None:
    settings = get_runtime_settings()
    api_key = (
        settings.local_translation_api_key
        or os.getenv("AETHER_LOCAL_TRANSLATION_API_KEY")
        or os.getenv("LOCAL_TRANSLATION_API_KEY")
        or os.getenv("OLLAMA_API_KEY")
        or ""
    ).strip().strip('"').strip("'")
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()
    return api_key or None


def _same_language(source_language: str | None, target_language: str | None) -> bool:
    source = _language_key(source_language)
    target = _language_key(target_language)
    return bool(source and target and source == target)


def _language_key(language: str | None) -> str:
    return (language or "").strip().lower().split("-")[0]


def _nvidia_api_key() -> str | None:
    api_key = (os.getenv("NVIDIA_API_KEY") or "").strip().strip('"').strip("'")
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:].strip()
    return api_key or None


def _optional_positive_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a positive integer.")
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


def _optional_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            raise RuntimeError(f"{name} must be a number.")
    return max(minimum, min(maximum, value))
