"""Proper names and numbers: keep them right and consistent across a whole dub.

Translation runs in many small batches, so each batch guessed names on its own
(VinFast / Winfast / "Tập đoàn Vinh" in one video). This module:

1. **Term sheet** (``prepare_terms``): one LLM pass over the whole transcript lists
   every proper name and convertible amount with the canonical target form, using
   context and world knowledge. The user's glossary and the shared dictionary win.
2. **Glossary in every batch** (``profile_with_terms``).
3. **Review** (``review_translation``): fixes known wrong spellings deterministically,
   then flags segments whose source mentions a term the translation lacks, or whose
   numbers do not match, and asks the LLM to repair only those names and numbers.

A term: ``{"target", "source": [...], "wrong": [...], "kind", "note", "origin"}``
(``origin``: user | dictionary | auto).
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from .storage import ensure_storage

logger = logging.getLogger(__name__)

KINDS = ("person", "brand", "organization", "place", "product", "number", "other")
MAX_TERMS = 80
_TRANSCRIPT_CHUNK = 14000
_REPAIR_BATCH = 15
_WORD = r"[\wÀ-ỹ]"
_dictionary_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("AETHER_TERM_CHECK", "1").strip().lower() not in {"0", "false", "no", "off"}


# ----------------------------------------------------------------------- normalizing


def _clean(value: Any, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _strings(values: Any, limit: int = 12) -> list[str]:
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values or []:
        text = _clean(value)
        if text and text.casefold() not in {item.casefold() for item in out}:
            out.append(text)
    return out[:limit]


def key(text: str) -> str:
    """Spelling-insensitive key: no case, accents, spaces or punctuation."""
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in decomposed if ch.isalnum() and not unicodedata.combining(ch)).casefold()


def normalize_term(raw: Any, origin: str = "auto") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    target = _clean(raw.get("target"))
    if not target:
        return None
    kind = str(raw.get("kind") or "other").strip().lower()
    # "Tata Motors" is not a misspelling of "Tata": never replace a longer name that contains the target.
    # Neither is "Maruti" (part of "Maruti Suzuki"): replacing it would double the rest of the name.
    wrong = [
        item
        for item in _strings(raw.get("wrong"))
        if item != target and not contains(item, target) and not contains(target, item)
    ]
    return {
        "target": target,
        "source": _strings(raw.get("source")),
        "wrong": wrong,
        "kind": kind if kind in KINDS else "other",
        "note": _clean(raw.get("note"), 160),
        "origin": str(raw.get("origin") or origin),
    }


def merge_terms(*groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Earlier groups win on the target form; sources and wrong forms are pooled.

    A later term whose target is a known *wrong* form of an earlier one folds into it.
    """
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for group in groups:
        for raw in group:
            term = normalize_term(raw, str(raw.get("origin") or "auto")) if isinstance(raw, dict) else None
            if not term:
                continue
            keys = {key(term["target"])} | {key(item) for item in term["wrong"]}
            existing = next((by_key[k] for k in keys if k in by_key), None)
            if existing is None:
                merged.append(term)
                existing = term
            else:
                existing["source"] = _strings(existing["source"] + term["source"])
                extra = [item for item in [term["target"], *term["wrong"]] if item != existing["target"]]
                existing["wrong"] = _strings(existing["wrong"] + extra)
                existing["note"] = existing["note"] or term["note"]
            for k in {key(existing["target"])} | {key(item) for item in existing["wrong"]}:
                if k:
                    by_key.setdefault(k, existing)
    return merged[:MAX_TERMS]


def glossary_terms(glossary: Any) -> list[dict[str, Any]]:
    """The production profile's ``{source: target}`` glossary as terms."""
    if not isinstance(glossary, dict):
        return []
    return [
        {"target": target, "source": [source], "origin": "user"}
        for source, target in glossary.items()
        if _clean(source) and _clean(target)
    ]


# ------------------------------------------------------------------ shared dictionary


def dictionary_path() -> Path:
    return ensure_storage() / "terms-dictionary.json"


def load_dictionary() -> list[dict[str, Any]]:
    path = dictionary_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        return []
    entries = payload.get("terms") if isinstance(payload, dict) else payload
    return [term for term in (normalize_term(item, "dictionary") for item in entries or []) if term]


def save_dictionary(entries: Iterable[Any]) -> list[dict[str, Any]]:
    terms = [term for term in (normalize_term(item, "dictionary") for item in entries) if term]
    for term in terms:
        term["origin"] = "dictionary"
    terms = merge_terms(terms)
    with _dictionary_lock:
        path = dictionary_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "terms": terms}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return terms


# ---------------------------------------------------------------------- matching


def _boundary(pattern: str) -> str:
    return rf"(?<!{_WORD}){pattern}(?!{_WORD})"


def _phrase_pattern(text: str) -> str:
    parts = [re.escape(part) for part in text.split()]
    return _boundary(r"\s+".join(parts))


def contains(text: str, phrase: str) -> bool:
    return bool(phrase) and re.search(_phrase_pattern(phrase), text, flags=re.IGNORECASE) is not None


def _target_present(text: str, term: dict[str, Any]) -> bool:
    if term["kind"] == "number":
        expected = numbers(term["target"])
        return bool(expected) and expected <= numbers(text)
    if contains(text, term["target"]):
        return True
    # "VinFast" and "Vin Fast"/"vinfast" are the same name for this check.
    target_key = key(term["target"])
    return len(target_key) >= 4 and target_key in key(text)


def source_mentions(source: str, term: dict[str, Any]) -> bool:
    lowered = source.casefold()
    for form in term["source"]:
        form = form.strip()
        if len(form) < 2:
            continue
        # Non-Latin scripts (Telugu, CJK…) have no reliable word boundaries.
        if form.casefold() in lowered if not form.isascii() else contains(source, form):
            return True
    return False


def _capitalized_ngrams(text: str, max_words: int = 3):
    """Runs of 1..max_words words starting with a capital, joined only by spaces/hyphens."""
    tokens = list(re.finditer(rf"{_WORD}+", text))
    for i, first in enumerate(tokens):
        if not first.group(0)[0].isupper():
            continue
        for size in range(1, max_words + 1):
            if i + size > len(tokens):
                break
            last = tokens[i + size - 1]
            if size > 1 and not re.fullmatch(r"[ \-]+", text[tokens[i + size - 2].end() : last.start()]):
                break
            yield first.start(), last.end(), text[first.start() : last.end()]


def _lookalike(wrong: str, target: str) -> bool:
    """A misspelling of the target itself (Winfast/VinFast), not a different word (Hoàng)."""
    a, b = key(wrong), key(target)
    return len(a) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def wrong_spans(text: str, term: dict[str, Any], source: str | None = None) -> list[tuple[int, int, str]]:
    """Spans in ``text`` that spell this term wrongly (known wrong forms, odd spacing/case).

    Wrong forms that are other words ("Hoàng" for "Phạm Nhật Vượng") only count where the
    ``source`` line mentions the term; look-alike misspellings count everywhere.
    """
    if term["kind"] == "number":
        return []  # Amount wording varies too much to rewrite mechanically; the review handles it.
    spans: list[tuple[int, int, str]] = []
    mentioned = source is not None and source_mentions(source, term)
    for wrong in sorted(term["wrong"], key=len, reverse=True):
        if not (mentioned or _lookalike(wrong, term["target"])):
            continue
        for match in re.finditer(_phrase_pattern(wrong), text, flags=re.IGNORECASE):
            spans.append((match.start(), match.end(), match.group(0)))
    target = term["target"]
    target_key = key(target)
    # Same letters, different case/spacing ("Vin Group", "VinGroup" for "Vingroup"). Names only:
    # short or Vietnamese-accented targets would also match ordinary words.
    if len(target_key) >= 5 and term["kind"] != "number":
        for start, end, found in _capitalized_ngrams(text, max(1, len(target.split()) + 1)):
            if found != target and key(found) == target_key:
                spans.append((start, end, found))
    # Text already spelled right is never touched, even where a wrong form matches inside it.
    correct = [(m.start(), m.end()) for m in re.finditer(_phrase_pattern(target), text)]
    spans.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    result: list[tuple[int, int, str]] = []
    for span in spans:
        if result and span[0] < result[-1][1]:
            continue
        if any(start < span[1] and span[0] < end for start, end in correct):
            continue
        result.append(span)
    return result


def fix_spelling(text: str, terms: list[dict[str, Any]], source: str | None = None) -> str:
    for term in terms:
        spans = wrong_spans(text, term, source)
        for start, end, _ in reversed(spans):
            text = text[:start] + term["target"] + text[end:]
    return text


_NUMBER_RE = re.compile(r"\d+(?:[.,\s]\d{3})*(?:[.,]\d+)?")


def numbers(text: str) -> set[str]:
    """Numbers as digit strings ("1.400" and "1400" match; "1,4" stays "14")."""
    found = set()
    for match in _NUMBER_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0)).lstrip("0") or "0"
        found.add(digits)
    return found


def issues_for(source: str, text: str, terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for term in terms:
        mentioned = source_mentions(source, term)
        if term["kind"] == "number":
            if mentioned and numbers(term["target"]) and not _target_present(text, term):
                issues.append({"type": "number", "target": term["target"]})
            continue
        if mentioned and not _target_present(text, term):
            issues.append({"type": "name", "target": term["target"]})
        elif wrong_spans(text, term, source):
            issues.append({"type": "spelling", "target": term["target"]})
    # Amounts the sheet converts (140 crore -> 1,4 tỷ) legitimately change digits.
    explained: set[str] = set()
    for term in terms:
        if term["kind"] == "number" and source_mentions(source, term):
            for form in term["source"]:
                explained |= numbers(form)
    missing = sorted(numbers(source) - numbers(text) - explained)
    if missing:
        issues.append({"type": "number", "values": missing[:6]})
    return issues


# --------------------------------------------------------------------------- LLM


# Names need world knowledge more than translation does: on OpenRouter use the stronger
# writing model (deepseek flash by default) rather than the small translation model.
_FALLBACK_OPENROUTER_MODEL = "deepseek/deepseek-v4.1-flash-1017"


def terms_model() -> str:
    from . import translator

    explicit = os.getenv("AETHER_TERMS_MODEL", "").strip()
    if explicit:
        return explicit
    if translator._translation_provider() != "openrouter":
        return translator._translation_model()
    try:
        from .assistant.settings import content_model_for, load_settings

        configured = content_model_for(load_settings(), "openai_compatible")
    except Exception:  # noqa: BLE001 - assistant settings are optional here
        configured = ""
    return configured or _FALLBACK_OPENROUTER_MODEL


def _complete(system: str, user: str) -> dict[str, Any]:
    from . import translator

    model = terms_model()
    try:
        content = translator._request_translation_completion(model, system, user)
    except Exception:
        fallback = translator._translation_model()
        if fallback == model:
            raise
        logger.warning("Terms model %s failed; using the translation model %s", model, fallback)
        content = translator._request_translation_completion(fallback, system, user)
    return translator._extract_json_object(content)


_EXTRACT_SYSTEM = "You build the terminology sheet a dubbing translator must follow. Answer with JSON only."


def _extract_prompt(transcript: str, source_language: str, target_language: str, context: str, known: list[dict[str, Any]]) -> str:
    known_lines = json.dumps(
        [{"target": term["target"], "source": term["source"][:3]} for term in known[:40]], ensure_ascii=False
    )
    return (
        f"Transcript language: {source_language}. Translation language: {target_language}.\n"
        "The transcript comes from speech recognition: names are often spelled phonetically or in another script.\n\n"
        "List every proper name (people, companies, brands, products, places, organisations, programmes) and every "
        "amount whose unit must be converted (Indian lakh/crore, Chinese 万/亿, Japanese 万/億, Korean 만/억...). "
        "Do not list plain amounts, prices, years, percentages or durations.\n"
        "For each item:\n"
        "- source: the spellings exactly as they appear in the transcript (copy the characters, max 4).\n"
        f"- target: the form to use in {target_language}. Work out the real-world entity from context and your "
        "knowledge, then use its established public spelling and official capitalisation (brands like VinFast, "
        "Vingroup, Vinhomes; Vietnamese people with full diacritics, e.g. Phạm Nhật Vượng; well-known foreign "
        "places in their usual target-language form). For amounts, the value converted into "
        "ordinary target-language units, never the foreign unit (1 lakh = 100 nghìn, 1 crore = 10 triệu, "
        "1万 = 10 nghìn, 1亿 = 100 triệu: 140 crore -> 1,4 tỷ; 17,000 crore -> 170 nghìn tỷ).\n"
        "- wrong: renderings a translator might produce by mistake (phonetic guesses, wrong letters, partial "
        "translations, other names the speaker's accent suggests), max 5.\n"
        "- kind: person | brand | organization | place | product | number | other.\n"
        "- note: at most 12 words (who/what it is, or the unit rule).\n"
        "Skip common nouns. Only include an entity when you are confident who/what it is; otherwise keep the "
        "transcript spelling as target. Use the known terms below exactly as given.\n"
        f"Context: {context or '-'}\n"
        f"Known terms: {known_lines}\n\n"
        'Return {"terms":[{"source":["..."],"target":"...","wrong":["..."],"kind":"...","note":"..."}]}\n\n'
        f"Transcript:\n{transcript}"
    )


def _transcript_chunks(texts: list[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if current and size + len(text) > _TRANSCRIPT_CHUNK:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks[:6]


def extract_terms(
    source_texts: list[str],
    source_language: str,
    target_language: str,
    *,
    context: str = "",
    known: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    known = known or []
    found: list[dict[str, Any]] = []
    chunks = _transcript_chunks(source_texts)

    def run(chunk: str) -> dict[str, Any]:
        return _complete(_EXTRACT_SYSTEM, _extract_prompt(chunk, source_language, target_language, context, known))

    with ThreadPoolExecutor(max_workers=min(3, max(1, len(chunks)))) as pool:
        payloads = list(pool.map(run, chunks))
    for payload in payloads:
        found.extend(item for item in payload.get("terms") or [] if isinstance(item, dict))
    for item in found:
        item["origin"] = "auto"
    return [term for term in merge_terms(found) if _useful(term)]


def _useful(term: dict[str, Any]) -> bool:
    target = term["target"]
    if term["kind"] == "number":
        # Only unit conversions belong on the sheet (140 crore -> 1,4 tỷ); plain amounts are
        # covered by the digit check.
        expected = numbers(target)
        given = set().union(*(numbers(form) for form in term["source"])) if term["source"] else set()
        return bool(expected) and expected != given
    # Proper names are capitalised (or contain digits); "và"/"anh ấy" are not names.
    return any(ch.isupper() for ch in target)


def prepare_terms(
    source_texts: list[str],
    source_language: str,
    target_language: str,
    profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The user's glossary, the shared dictionary, then names found in this transcript."""
    profile = profile or {}
    known = merge_terms(glossary_terms(profile.get("glossary")), load_dictionary())
    try:
        auto = extract_terms(
            source_texts, source_language, target_language, context=str(profile.get("context") or ""), known=known
        )
    except Exception as exc:  # noqa: BLE001 - names are a bonus; never block the translation
        logger.warning("Term extraction failed: %s", exc)
        auto = []
    # Shared-dictionary entries only matter when this video mentions them (or their wrong forms).
    return merge_terms(known, auto)


def profile_with_terms(profile: dict[str, Any] | None, terms: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not terms:
        return profile
    glossary = dict((profile or {}).get("glossary") or {})
    for term in terms:
        label = term["source"][0] if term["source"] else term["target"]
        value = term["target"] + (f" ({term['note']})" if term["kind"] == "number" and term["note"] else "")
        glossary.setdefault(label, value)
    return {**(profile or {}), "glossary": glossary}


_REPAIR_SYSTEM = "You correct proper names and numbers in dubbing translations. Answer with JSON only."


def _repair_prompt(items: list[dict[str, Any]], terms: list[dict[str, Any]], source_language: str, target_language: str) -> str:
    sheet = [
        {"target": term["target"], "source": term["source"][:3], "not": term["wrong"][:4], "note": term["note"]}
        for term in terms
    ]
    return (
        f"Source language: {source_language}. Translation language: {target_language}.\n"
        "Each item has the source line, its translation and the problems a checker found.\n"
        "Fix ONLY proper names and numbers so they match the terminology sheet and the source: use the sheet's "
        "target spelling exactly; keep amounts equal to the source (convert lakh/crore/万/亿 correctly). Keep all "
        "other wording, length and tone. If the translation is already right, return it unchanged.\n"
        f"Terminology sheet: {json.dumps(sheet, ensure_ascii=False)}\n"
        f"Items: {json.dumps(items, ensure_ascii=False)}\n"
        'Return {"fixes":[{"id":"...","text":"..."}]}'
    )


def review_translation(
    items: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    source_language: str,
    target_language: str,
    *,
    repair: bool = True,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Check ``items`` (``id``, ``source``, ``text``); return changed texts by id and a report.

    The report lists ``fixed`` (id, before, after, how) and ``remaining`` (id, issues).
    """
    from .translator import _has_untranslated_cjk

    changed: dict[str, str] = {}
    fixed: list[dict[str, Any]] = []
    current = {item["id"]: item["text"] for item in items}
    sources = {item["id"]: item.get("source") or "" for item in items}

    for item_id, text in current.items():
        spelled = fix_spelling(text, terms, sources[item_id])
        if spelled != text:
            fixed.append({"id": item_id, "before": text, "after": spelled, "how": "spelling"})
            changed[item_id] = current[item_id] = spelled

    flagged = {
        item_id: problems
        for item_id, text in current.items()
        if (problems := issues_for(sources[item_id], text, terms))
    }
    if repair and flagged:
        ids = list(flagged)
        for start in range(0, len(ids), _REPAIR_BATCH):
            batch = ids[start : start + _REPAIR_BATCH]
            payload_items = [
                {"id": item_id, "source": sources[item_id], "translation": current[item_id], "problems": flagged[item_id]}
                for item_id in batch
            ]
            try:
                payload = _complete(_REPAIR_SYSTEM, _repair_prompt(payload_items, terms, source_language, target_language))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Term repair failed: %s", exc)
                continue
            for fix in payload.get("fixes") or []:
                if not isinstance(fix, dict):
                    continue
                item_id = str(fix.get("id") or "")
                text = re.sub(r"\s+", " ", str(fix.get("text") or "")).strip()
                if item_id not in batch or not text or text == current[item_id]:
                    continue
                if _has_untranslated_cjk(text, target_language) or len(text) > 2.5 * len(current[item_id]) + 40:
                    continue
                text = fix_spelling(text, terms, sources[item_id])
                # Keep a fix only when it solves a flagged problem and touches nothing but names/numbers.
                if len(issues_for(sources[item_id], text, terms)) >= len(flagged[item_id]):
                    continue
                if not _only_names_changed(current[item_id], text, terms):
                    continue
                fixed.append({"id": item_id, "before": current[item_id], "after": text, "how": "ai"})
                changed[item_id] = current[item_id] = text

    remaining = [
        {"id": item_id, "issues": problems}
        for item_id, text in current.items()
        # Plain digit mismatches are often legitimate rewording; keep them out of the to-review list.
        if (problems := [issue for issue in issues_for(sources[item_id], text, terms) if issue.get("target")])
    ]
    report = {"terms": len(terms), "fixed": fixed, "remaining": remaining}
    return changed, report


def _words(text: str) -> list[str]:
    return [word.casefold() for word in re.findall(rf"{_WORD}+", text)]


def _only_names_changed(before: str, after: str, sheet: list[dict[str, Any]], slack: int = 2) -> bool:
    """True when the words that changed are (nearly all) parts of names or amounts."""
    allowed = set()
    for term in sheet:
        for form in [term["target"], *term["wrong"]]:
            allowed.update(_words(form))
    old, new = _words(before), _words(after)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    unexplained = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        for word in old[i1:i2] + new[j1:j2]:
            if word not in allowed and not word.isdigit():
                unexplained += 1
    return unexplained <= slack


def summary_line(report: dict[str, Any] | None) -> str:
    if not report:
        return ""
    fixed = report.get("fixed") or []
    spelling = sum(1 for item in fixed if item.get("how") == "spelling")
    ai = len(fixed) - spelling
    remaining = len(report.get("remaining") or [])
    return (
        f"Kiểm tra tên riêng & con số: {report.get('terms', 0)} tên/số, sửa chính tả {spelling} câu, "
        f"AI sửa {ai} câu, còn {remaining} câu cần xem lại."
    )


# ------------------------------------------------------------------------ editor


def overview(terms: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per term: how many lines use it, lines that should but don't, wrong spellings found."""
    # Older jobs split one source sentence over several lines: a name said in that sentence
    # only has to appear in one of them.
    sentence_text: dict[str, str] = {}
    for segment in segments:
        source = segment.get("source_text") or ""
        sentence_text[source] = f"{sentence_text.get(source, '')} {segment.get('text') or ''}"
    result = []
    for term in terms:
        uses = 0
        missing: list[str] = []
        variants: dict[str, list[str]] = {}
        missing_sentences: set[str] = set()
        for segment in segments:
            text = segment.get("text") or ""
            source = segment.get("source_text") or ""
            if _target_present(text, term):
                uses += 1
            elif (
                source not in missing_sentences
                and source_mentions(source, term)
                and not _target_present(sentence_text[source], term)
                # A misspelling is listed under variants; "Sửa" there resolves it.
                and not wrong_spans(sentence_text[source], term, source)
            ):
                missing_sentences.add(source)
                missing.append(segment["id"])
            for _, _, found in wrong_spans(text, term, segment.get("source_text") or ""):
                variants.setdefault(found, []).append(segment["id"])
        result.append(
            {
                **term,
                "uses": uses,
                "missing_ids": missing,
                "variants": [{"text": text, "ids": ids} for text, ids in variants.items()],
            }
        )
    return result


def segment_issue_ids(terms: list[dict[str, Any]], segments: list[dict[str, Any]]) -> set[str]:
    flagged: set[str] = set()
    for item in overview(terms, segments):
        flagged.update(item["missing_ids"])
        for variant in item["variants"]:
            flagged.update(variant["ids"])
    return flagged
