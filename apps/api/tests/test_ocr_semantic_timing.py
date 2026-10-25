from __future__ import annotations

import pytest

from app.services.translator import IndexedSubtitleCue, _time_semantic_segments


def _cue(index: int, start: float, end: float, text: str) -> IndexedSubtitleCue:
    return IndexedSubtitleCue(index=str(index), start=start, end=end, text=text)


def test_semantic_reconstruction_preserves_observed_ocr_starts_and_gaps() -> None:
    source = [
        _cue(1, 21.0, 25.0, "何以面對先皇在天之靈啊"),
        _cue(2, 25.0, 27.5, "痛哉痛哉"),
        _cue(3, 27.5, 29.0, "痛不可当啊"),
        # The two-second gap is visible in the source video and must survive.
        _cue(4, 31.0, 33.5, "我倒是与君不同"),
        _cue(5, 35.0, 37.0, "一方面是痛"),
        _cue(6, 37.0, 39.5, "另一方面是喜"),
    ]
    reconstructed = [
        "何以面对先皇在天之灵啊？痛哉，痛哉，痛不可当啊！",
        "我倒是与君不同，一方面是痛，另一方面是喜。",
    ]

    timed = _time_semantic_segments(source, reconstructed)

    assert timed[0].start == pytest.approx(21.0)
    assert timed[0].end == pytest.approx(29.0)
    assert timed[1].start == pytest.approx(31.0)
    assert timed[1].end == pytest.approx(39.5)


def test_unrelated_reconstruction_falls_back_to_proportional_timing() -> None:
    source = [
        _cue(1, 2.0, 4.0, "原文"),
        _cue(2, 6.0, 8.0, "字幕"),
    ]

    timed = _time_semantic_segments(source, ["completely unrelated", "content"])

    assert len(timed) == 2
    assert timed[0].start == pytest.approx(2.0)
    assert timed[-1].end == pytest.approx(8.0)
