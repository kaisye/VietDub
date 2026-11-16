from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services import renderer, translator
from app.services.renderer import _wrap_cue_for_ass
from app.services.sentence_units import group_sentences, page_cue


def _cue(start: float, end: float, text: str) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, text=text)


def _group(cues, **kwargs):
    return group_sentences(cues, start=lambda c: c.start, end=lambda c: c.end, text=lambda c: c.text, **kwargs)


def test_cues_join_into_whole_sentences_with_exact_boundaries() -> None:
    units = _group(
        [
            _cue(0.147, 1.179, "In Singapore,"),
            _cue(1.199, 6.146, "the boss of the most valuable company is having dinner."),
            _cue(6.166, 8.550, "He's Jensen Huang, who runs Nvidia."),
        ]
    )
    assert [(u.start, u.end) for u in units] == [(0.147, 6.146), (6.166, 8.550)]
    assert units[0].text == "In Singapore, the boss of the most valuable company is having dinner."


def test_pause_and_length_limit_close_a_unit() -> None:
    units = _group(
        [
            _cue(0.0, 2.0, "first part without an end"),
            _cue(4.0, 6.0, "After a long pause."),
            _cue(6.1, 9.0, "One"),
            _cue(9.1, 14.0, "two"),
            _cue(14.1, 19.0, "three."),
        ],
        pause_seconds=0.6,
        max_seconds=10.0,
    )
    assert [(u.start, u.end) for u in units] == [(0.0, 2.0), (4.0, 6.0), (6.1, 14.0), (14.1, 19.0)]


def test_unfinished_sentence_continues_across_a_breath() -> None:
    # Soniox cut these at breaths; the voice must read each sentence in one go.
    units = _group(
        [
            _cue(22.95, 27.57, "It powers the data centers behind OpenAI, Anthropic, Meta, Google, and"),
            _cue(28.05, 28.47, "Microsoft."),
            _cue(28.59, 32.55, "It sits at a valuation north of $4 trillion and makes up a massive"),
            _cue(32.73, 34.35, "slice of the S&P 500."),
            _cue(1186.29, 1187.79, "There were movements around the world"),
            _cue(1188.87, 1189.07, "that"),
            _cue(1189.10, 1193.43, "interpreted open models as unsafe."),
        ]
    )
    assert [(u.start, u.end) for u in units] == [(22.95, 28.47), (28.59, 34.35), (1186.29, 1193.43)]


def test_abbreviation_followed_by_lowercase_does_not_end_the_sentence() -> None:
    units = _group(
        [
            _cue(771.51, 773.91, "tech world and enters the highest echelon of U.S."),
            _cue(774.09, 774.57, "politics."),
        ]
    )
    assert [(u.start, u.end, u.text) for u in units] == [
        (771.51, 774.57, "tech world and enters the highest echelon of U.S. politics.")
    ]


def test_very_short_sentence_joins_the_next_one() -> None:
    units = _group([_cue(0.0, 0.4, "Yes."), _cue(0.5, 3.0, "That is right."), _cue(4.0, 6.0, "Next.")])
    assert [(u.start, u.end) for u in units] == [(0.0, 3.0), (4.0, 6.0)]


def test_overlapping_rolling_captions_keep_the_latest_end() -> None:
    units = _group([_cue(0.0, 4.3, "Casi medio billón"), _cue(2.3, 6.1, "de dólares.")])
    assert [(u.start, u.end) for u in units] == [(0.0, 6.1)]


def test_translator_keeps_sound_tags_out_of_sentences() -> None:
    srt = (
        "1\n00:00:00,000 --> 00:00:03,000\n[Music]\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nHello\n\n"
        "3\n00:00:04,000 --> 00:00:05,500\nthere.\n"
    )
    cues = translator._parse_indexed_srt_cues(translator._group_source_sentences(srt))
    spoken = [(c.start, c.end, c.text) for c in cues if "Music" not in c.text]
    # The sentence starts when speech starts, not when the music caption began.
    assert spoken == [(3.0, 5.5, "Hello there.")]


def test_short_sentence_is_one_page_for_the_whole_cue() -> None:
    assert page_cue("Tại Singapore,", 0.1, 1.2, 42, 2) == [(0.1, 1.2, "Tại Singapore,")]


def test_long_sentence_pages_break_at_clauses_and_follow_the_voice() -> None:
    text = (
        "Còn cô ấy là Alice Huang, không cùng huyết thống, và cô ấy điều hành "
        "một trong những công ty gián điệp lớn nhất châu Á, chuyên thu thập dữ liệu cho chính phủ."
    )
    pages = page_cue(text, 8.0, 12.0, 42, 2, voice_start=8.0, voice_end=14.0)
    assert len(pages) >= 2
    assert " ".join(page for _, _, page in pages) == text
    assert pages[0][2].endswith(",")
    assert pages[0][0] == 8.0
    assert pages[-1][1] == 14.0  # held while the overrunning voice still speaks
    assert all(a[1] == b[0] for a, b in zip(pages, pages[1:]))
    # Without a voice the same pages share the cue window.
    assert page_cue(text, 8.0, 12.0, 42, 2)[-1][1] == 12.0
    assert page_cue(text, 8.0, 12.0, 42, 2, voice_start=8.0, voice_end=14.0, hold_for_voice=False)[-1][1] == 12.0


def test_ass_pages_fit_max_lines() -> None:
    text = " ".join(["một hai ba bốn năm sáu bảy tám chín mười"] * 6)
    for _, _, lines in _wrap_cue_for_ass(text, 0.0, 12.0, 30, 2):
        assert len(lines.split("\n")) <= 2


def test_display_pages_follow_voice_spans(tmp_path: Path, monkeypatch) -> None:
    subtitle = tmp_path / "job.vi.render.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\n"
        "Đây là một câu rất dài, có nhiều mệnh đề, và nó cần được chia thành nhiều trang phụ đề khác nhau.\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nNgắn.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(renderer, "_probe_dimensions", lambda path: (640, 360))
    path = renderer.prepare_display_subtitle(
        tmp_path / "raw.mp4", subtitle, None, max_lines=1, voice_spans=[(0.0, 5.5), (5.0, 5.8)]
    )
    cues = renderer._parse_srt_for_ass(path)
    assert len(cues) >= 3
    assert cues[-1] == (5.0, 6.0, "Ngắn.")
    # The long sentence's last page is held for its voice but never covers the next sentence.
    assert cues[-2][1] == 5.0

