import re
import subprocess

from app.services import karaoke
from app.services.karaoke import FRAME_SECONDS, VoiceActivity, load_voice_activity, word_times
from app.services.renderer import _srt_to_ass, normalize_subtitle_style

SRT = """1
00:00:01,000 --> 00:00:03,000
Xin chào các bạn.
"""


def _dialogues(path):
    return [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue:")]


def test_style_defaults_off_and_rejects_unknown_modes():
    assert normalize_subtitle_style({})["karaoke_mode"] == "off"
    assert normalize_subtitle_style({"karaoke_mode": "bounce"})["karaoke_mode"] == "off"
    assert normalize_subtitle_style({"karaoke_mode": "Reveal"})["karaoke_mode"] == "reveal"


def test_off_keeps_the_plain_text(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text(SRT, encoding="utf-8")
    ass = _srt_to_ass(srt, normalize_subtitle_style({}), (1920, 1080), 2)
    assert _dialogues(ass)[0].endswith("}Xin chào các bạn.")


def test_highlight_colours_each_word_while_it_is_spoken(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text(SRT, encoding="utf-8")
    style = normalize_subtitle_style({"karaoke_mode": "highlight", "text_color": "#FFFFFF", "karaoke_color": "#FF0000"})
    text = _dialogues(_srt_to_ass(srt, style, (1920, 1080), 2))[0]
    assert text.count("\\1c&H0000FF&") == 4  # one highlight per word
    starts = [int(value) for value in re.findall(r"\\t\((\d+),\d+,\\1c&H0000FF&\)", text)]
    assert starts[0] == 0 and starts == sorted(starts) and starts[-1] < 2000
    assert "Xin" in text and "bạn." in text


def test_reveal_hides_words_until_spoken_and_keeps_the_bilingual_line_visible(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text(SRT, encoding="utf-8")
    (tmp_path / "a.source.srt").write_text(SRT.replace("Xin chào các bạn.", "Hello everyone."), encoding="utf-8")
    style = normalize_subtitle_style({"karaoke_mode": "reveal", "bilingual_enabled": True})
    text = _dialogues(_srt_to_ass(srt, style, (1920, 1080), 2))[0]
    assert text.count("\\1a&HFF&") == 4
    # The reset before the source line makes it visible from the start.
    head, source = text.split("Hello")[0].rsplit("\\N", 1)
    assert head.endswith("\\1a&H00&\\3a&H00&}")


def test_words_follow_the_voice_and_skip_its_pauses():
    # Voice from 1.0–1.6 s, silence, then 2.4–3.0 s.
    voiced = [False] * 200
    for index in list(range(50, 80)) + list(range(120, 150)):
        voiced[index] = True
    times = word_times(["một", "hai,", "ba", "bốn"], 0.5, 3.5, VoiceActivity(tuple(voiced)))
    assert times[0][0] >= 1.0
    assert all(not (1.6 < start < 2.4) for start, _ in times)
    assert times[-1][1] <= 3.0 + FRAME_SECONDS


def test_without_voice_words_share_the_page_by_syllables():
    times = word_times(["Năm", "2017,", "hãng", "ra", "đời."], 0.0, 4.0)
    lengths = [end - start for start, end in times]
    assert lengths[1] > lengths[0] * 3  # a year is read as many syllables
    assert times[0][0] == 0.0 and times[-1][1] <= 4.0


def test_voice_activity_from_a_real_track(tmp_path):
    path = tmp_path / "voice.wav"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=1",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=16000:duration=1",
            "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(path),
        ],
        check=True,
    )
    activity = load_voice_activity(path)
    assert activity is not None
    assert not any(activity.voiced[5:45]) and all(activity.voiced[55:95])
    assert karaoke.load_voice_activity(tmp_path / "missing.wav") is None
