from __future__ import annotations

import pytest

from app.services.subtitle import _detect_text_language, _dominant_transcript_language, _srt_to_transcript_spans


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Casi medio billón de dólares. Exactamente perdió mucho valor de mercado", "es"),
        ("¿El director general?", "es"),
        ("C'est une entreprise qui ne fait pas de bruit", "fr"),
        ("Isso não é para você", "pt"),
        ("Das ist nicht ein Problem", "de"),
        ("Thank you for watching the video", "en"),
        ("Gần nửa nghìn tỷ đô la đã bốc hơi", "vi"),
        ("Anh là bạn tôi", "vi"),
        ("黄仁勋是谁", "zh"),
    ],
)
def test_detect_text_language(text: str, expected: str) -> None:
    assert _detect_text_language(text) == expected


def test_spanish_track_is_not_mistaken_for_vietnamese() -> None:
    # Accented vowels shared with Vietnamese (á é í ó ú) used to make a Spanish
    # YouTube track look Vietnamese, so a VI-target job skipped translation.
    srt = (
        "1\n00:00:00,216 --> 00:00:04,357\nCasi medio billón de dólares. Exactamente\n\n"
        "2\n00:00:02,379 --> 00:00:06,185\nperdió mucho valor de mercado\n\n"
        "3\n00:00:04,357 --> 00:00:08,283\nNvidia en tan solo cuatro sesiones de negociación,\n\n"
        "4\n00:00:08,283 --> 00:00:11,025\ndespués de que Jensen Huang dejó a la empresa\n"
    )
    spans = _srt_to_transcript_spans(srt)
    assert _dominant_transcript_language(spans, "auto") == "es"
