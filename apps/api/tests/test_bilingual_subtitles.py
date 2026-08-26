"""Tests for bilingual subtitle burn-in.

The translated line and the source line are drawn as ONE ASS Dialogue event —
the source text is appended after ``\\N`` with an inline ``{\\fs..\\1c..}``
override — so libass centres both languages as a single block on ``position_y``.
Two separate events would drift apart whenever the translated line wrapped to a
different number of lines.

The source text comes from the ``<stem>.render.source.srt`` companion that
``translate_subtitle`` writes, and is matched to each display cue by TIME rather
than by index, because ``prepare_display_subtitle`` re-splits cues before render.
"""

import re

import pytest

from app.services.renderer import (
    _secondary_text_for_span,
    _srt_to_ass,
    normalize_subtitle_style,
    source_language_subtitle_path,
)
from app.services.translator import _write_source_language_subtitle

TRANSLATED_SRT = """1
00:00:01,000 --> 00:00:03,000
Xin chào các bạn.

2
00:00:03,000 --> 00:00:05,000
Hôm nay chúng ta học tiếng Anh.
"""

SOURCE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello everyone.

2
00:00:03,000 --> 00:00:05,000
Today we are learning English.
"""


def _write_pair(tmp_path, *, with_source=True):
    subtitle = tmp_path / "job.render.srt"
    subtitle.write_text(TRANSLATED_SRT, encoding="utf-8")
    if with_source:
        source_language_subtitle_path(subtitle).write_text(SOURCE_SRT, encoding="utf-8")
    return subtitle


def _dialogue_lines(ass_path):
    return [
        line for line in ass_path.read_text(encoding="utf-8-sig").splitlines()
        if line.startswith("Dialogue:")
    ]


def test_source_companion_resolves_for_render_and_display_subtitles(tmp_path):
    render = tmp_path / "job.render.srt"
    display = tmp_path / "job.render.display.srt"
    assert source_language_subtitle_path(render).name == "job.render.source.srt"
    # prepare_display_subtitle re-wraps the render subtitle; both must resolve to
    # the same source companion, otherwise bilingual silently drops out whenever
    # a voice is generated.
    assert source_language_subtitle_path(display) == source_language_subtitle_path(render)


def test_bilingual_disabled_renders_only_the_translation(tmp_path):
    subtitle = _write_pair(tmp_path)
    style = normalize_subtitle_style({"bilingual_enabled": False})
    ass_path = _srt_to_ass(subtitle, style, (1920, 1080), 2)
    dialogues = _dialogue_lines(ass_path)
    assert dialogues
    assert all("Hello everyone" not in line for line in dialogues)
    assert all("\\fs" not in line for line in dialogues)


def test_bilingual_appends_smaller_source_line_below_the_translation(tmp_path):
    subtitle = _write_pair(tmp_path)
    style = normalize_subtitle_style(
        {"bilingual_enabled": True, "font_size": 20, "bilingual_font_scale": 0.5}
    )
    ass_path = _srt_to_ass(subtitle, style, (1920, 1080), 2)
    dialogues = _dialogue_lines(ass_path)
    assert len(dialogues) == 2

    first = dialogues[0]
    # Translation first, then a line break, then the size/colour override.
    assert re.search(r"Xin chào các bạn\.\\N\{\\fs\d+\\1c&H[0-9A-F]{6}&\}Hello everyone\.", first)
    # font_size 20 at width 1920 -> 20 * 1920 / 512 = 75; scaled by 0.5 -> 38.
    assert "\\fs38" in first
    assert "Today we are learning English." in dialogues[1]


def test_bilingual_without_a_source_companion_falls_back_to_one_language(tmp_path):
    subtitle = _write_pair(tmp_path, with_source=False)
    style = normalize_subtitle_style({"bilingual_enabled": True})
    dialogues = _dialogue_lines(_srt_to_ass(subtitle, style, (1920, 1080), 2))
    assert dialogues
    assert all("\\fs" not in line for line in dialogues)


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (1.0, 3.0, "Hello everyone."),          # exact cue
        (0.0, 6.0, "Hello everyone. Today we are learning English."),  # spans both
        (1.0, 2.0, "Hello"),                    # first half of a split cue
        (2.0, 3.0, "everyone."),                # second half of the same cue
        (5.5, 6.0, ""),                         # past the last cue
    ],
)
def test_source_text_is_sliced_to_match_a_split_display_cue(start, end, expected):
    cues = [(1.0, 3.0, "Hello everyone."), (3.0, 5.0, "Today we are learning English.")]
    assert _secondary_text_for_span(cues, start, end) == expected


def test_source_companion_is_written_from_translated_segments(tmp_path):
    destination = tmp_path / "job.render.srt"
    payload = {
        "segments": [
            {"id": "1", "start": 1.0, "end": 3.0, "source_text": "Hello everyone.", "translated_text": "Xin chào."},
            {"id": "2", "start": 3.0, "end": 5.0, "source_text": "", "translated_text": "Bỏ qua."},
        ]
    }
    path = _write_source_language_subtitle(destination, payload)
    assert path == source_language_subtitle_path(destination)
    text = path.read_text(encoding="utf-8")
    assert "Hello everyone." in text
    assert "00:00:01,000 --> 00:00:03,000" in text
    # The empty segment is skipped and indexes stay contiguous.
    assert text.strip().splitlines()[0] == "1"
    assert "2\n" not in text


def test_untranslated_job_removes_a_stale_source_companion(tmp_path):
    # Same-language jobs (and local pass-through generation) copy the source into
    # translated_text; burning both lines would show the same sentence twice.
    destination = tmp_path / "job.render.srt"
    stale = source_language_subtitle_path(destination)
    stale.write_text(SOURCE_SRT, encoding="utf-8")
    payload = {
        "segments": [
            {"start": 1.0, "end": 3.0, "source_text": "Hello.", "translated_text": "Hello."},
            {"start": 3.0, "end": 5.0, "source_text": "Goodbye.", "translated_text": "Goodbye."},
        ]
    }

    assert _write_source_language_subtitle(destination, payload) is None
    # Left in place it would be picked up by the next bilingual render.
    assert not stale.exists()
