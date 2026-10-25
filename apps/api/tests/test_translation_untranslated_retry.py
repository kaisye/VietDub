import pytest

from app.services import translator
from app.services.translator import IndexedSubtitleCue


def _cues(count: int) -> list[IndexedSubtitleCue]:
    return [IndexedSubtitleCue(index=str(i), start=float(i), end=i + 1.0, text=f"中文{i}") for i in range(1, count + 1)]


def _resilient(cues):
    return translator._translate_contextual_batch_resilient(cues, "zh", "vi", None, [], [], False, None)


def test_detects_leftover_cjk_only_for_non_cjk_targets() -> None:
    assert translator._has_untranslated_cjk("bị đánh很多次", "vi")
    assert not translator._has_untranslated_cjk("Biết không, tài sản của Musk", "vi")
    assert not translator._has_untranslated_cjk("你好", "zh")


def test_only_echoed_segments_are_retranslated(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_batch(cues, *args):
        ids = [cue.index for cue in cues]
        calls.append(ids)
        if len(calls) == 1:
            # Model echoes segment 2 back in Chinese.
            return {i: ("中文2" if i == "2" else f"câu {i}") for i in ids}
        return {i: f"câu {i} đã dịch" for i in ids}

    monkeypatch.setattr(translator, "_translate_contextual_batch", fake_batch)

    result = _resilient(_cues(3))

    assert calls == [["1", "2", "3"], ["2"]]
    assert result == {"1": "câu 1", "2": "câu 2 đã dịch", "3": "câu 3"}


def test_stubborn_segment_fails_instead_of_reaching_tts(monkeypatch) -> None:
    monkeypatch.setattr(
        translator, "_translate_contextual_batch", lambda cues, *args: {c.index: c.text for c in cues}
    )

    with pytest.raises(RuntimeError, match="chữ gốc"):
        _resilient(_cues(1))


def test_openrouter_uses_smaller_batches(monkeypatch) -> None:
    monkeypatch.setattr(translator, "_translation_provider", lambda: "openrouter")
    monkeypatch.delenv("AETHER_SOURCE_FIRST_TRANSLATION_BATCH_CUES", raising=False)
    monkeypatch.delenv("AETHER_SOURCE_FIRST_TRANSLATION_BATCH_CHARS", raising=False)

    batches = translator._contextual_translation_batches(_cues(100))

    assert max(len(batch) for batch in batches) == 40
