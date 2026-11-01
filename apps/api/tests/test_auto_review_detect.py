"""Auto review — detectors, Vietnamese line-break rules and the quality score."""
from __future__ import annotations

from app.services.auto_review import vi_text
from app.services.auto_review.detect import CueView, Layout, detect, summarize
from app.services.renderer import _wrap_text_lines

LAYOUT = Layout(chars_per_line=42, max_lines=2, duration=120.0)


def voiced(cue_id, start, end, text, *, natural, voice_len=None, voice_start=None):
    return CueView(
        id=cue_id,
        start=start,
        end=end,
        text=text,
        voiced=True,
        voice_start=start if voice_start is None else voice_start,
        voice_len=(end - start) if voice_len is None else voice_len,
        natural=natural,
    )


def types_of(cues, layout=LAYOUT):
    found: dict[str, list[str]] = {}
    for issue in detect(cues, layout):
        found.setdefault(issue.cue_id, []).append(issue.type)
    return found


def test_syllables_count_numbers_acronyms_and_latin_words() -> None:
    assert vi_text.syllable_count("Xin chào các bạn.") == 4
    assert vi_text.syllable_count("AI") == 2  # a-i
    assert vi_text.syllable_count("năm 2026") == 1 + 6
    assert vi_text.syllable_count("Cybercab") == 3


def test_break_rules_flag_dangling_words_names_and_numbers() -> None:
    assert vi_text.break_penalty("của", "anh")[1] == "dangling"
    assert vi_text.break_penalty("Trung", "Quốc,")[1] == "name"
    assert vi_text.break_penalty("hai", "mươi")[1] == "number"
    assert vi_text.break_penalty("10", "năm")[1] == "number"
    assert vi_text.break_penalty("không", "à?")[1] == "particle"
    assert vi_text.break_penalty("hôm", "nay")[1] is None
    assert vi_text.break_penalty("bạn,", "nhưng")[0] < 0  # After a comma: a good place.


def test_renderer_wrap_never_ends_a_line_on_a_function_word() -> None:
    text = "Điều thú vị nhất của cuộc phỏng vấn này không nằm ở những lời hay ý đẹp"
    lines = _wrap_text_lines(text, 38, 2)
    assert len(lines) == 2
    assert vi_text.bare(lines[0].split()[-1]) not in vi_text.DANGLING_WORDS
    assert vi_text.wrap_break_issue(lines) is None
    # Just over one line: balanced instead of pushing a single word down.
    short = _wrap_text_lines("Cơ thể người có giới hạn sinh học, nhưng robot thì không.", 54, 2)
    assert short == ["Cơ thể người có giới hạn sinh học,", "nhưng robot thì không."]


def test_voice_fast_needs_both_speed_and_a_high_speaking_rate() -> None:
    rushed = voiced("a", 0, 2.5, "Ông cho rằng Trung Quốc có nền tảng sản xuất vô cùng vững chắc", natural=4.0)
    fine = voiced("b", 10, 13, "Xin chào các bạn", natural=3.9)  # 1.3× but 4 syllables in 3 s.
    found = types_of([rushed, fine])
    issue = next(item for item in detect([rushed, fine], LAYOUT) if item.type == "voice_fast")
    assert issue.cue_id == "a" and issue.severity == "error" and issue.values["speed"] == 1.6
    assert "voice_fast" not in found.get("b", [])


def test_voice_slow_overlap_offset_and_dead_air() -> None:
    slow = voiced("slow", 0, 3, "Một hai ba bốn năm sáu bảy", natural=2.4, voice_len=3.0)
    overlapping = voiced("ov", 5, 7, "Câu này đọc lấn sang câu sau", natural=2.6, voice_len=2.6)
    following = voiced("next", 7.2, 9, "Câu sau", natural=0.8)
    late = voiced("late", 20, 22, "Đến muộn", natural=0.8, voice_start=21.0, voice_len=0.8)
    quiet = voiced("quiet", 30, 36, "Ngắn thôi", natural=1.0, voice_len=1.0)
    found = types_of([slow, overlapping, following, late, quiet])
    assert "voice_slow" in found["slow"]
    assert "voice_overlap" in found["ov"]
    assert "voice_offset" in found["late"]
    assert "dead_air" in found["quiet"]


def test_subtitle_reading_speed_duration_and_lines() -> None:
    dense = voiced("dense", 0, 1.5, "Một câu rất dài được hiển thị quá nhanh để đọc kịp", natural=1.5)
    short = voiced("short", 5, 5.5, "Ồ, thật à?", natural=0.5)
    long = voiced("long", 10, 19, "Câu này hiện trên màn hình quá lâu.", natural=2.0)
    wordy = voiced(
        "wordy",
        30,
        36,
        "Một câu phụ đề cực kỳ dài, dài tới mức không thể nào nằm gọn trong hai dòng chữ "
        "của khung hình, nên sẽ bị cắt thành nhiều màn hình nối tiếp nhau rất khó theo dõi",
        natural=6.0,
    )
    found = types_of([dense, short, long, wordy])
    assert "sub_fast" in found["dense"]
    assert "sub_short" in found["short"]
    assert "sub_long" in found["long"]
    assert "sub_lines" in found["wordy"]


def test_orphans_fragments_empty_cues_and_missing_voice() -> None:
    orphan = voiced("orphan", 0, 0.3, "Và", natural=0.3)
    cont = voiced("cont", 0.4, 3.0, "huấn luyện bạn trên đám mây.", natural=2.0)
    cut = voiced("cut", 10, 12, "dự đoán về tương lai mười đến hai", natural=2.0)
    rest = voiced("rest", 12, 14.5, "mươi năm tới: AI, robot.", natural=2.0)
    dots = voiced("dots", 20, 20.2, "...", natural=0.3)
    silent = CueView("silent", 30, 32, "Câu chưa có giọng")
    found = types_of([orphan, cont, cut, rest, dots, silent])
    assert "sub_orphan" in found["orphan"]
    assert "sub_fragment" in found["cut"]
    assert found["dots"] == ["sub_empty"]
    assert found["silent"] == ["voice_missing"]


def test_bad_line_break_is_reported_from_the_wrapped_lines() -> None:
    def wrap(text, cpl, lines):  # A layout engine that cuts after "của".
        return ["Đây là chiếc xe của", "anh ấy hôm qua."]

    cue = voiced("x", 0, 4, "Đây là chiếc xe của anh ấy hôm qua.", natural=2.5)
    issues = detect([cue], Layout(chars_per_line=20, max_lines=2, duration=10, wrap=wrap))
    bad = [issue for issue in issues if issue.type == "sub_bad_break"]
    assert bad and bad[0].values == {"reason": "dangling", "at": "của | anh"}


def test_summary_scores_clean_videos_100_and_counts_by_type() -> None:
    clean = [voiced(f"c{index}", index * 4, index * 4 + 3, "Xin chào các bạn thân mến.", natural=2.0, voice_len=2.0) for index in range(5)]
    assert summarize(clean, detect(clean, LAYOUT))["score"] == 100
    broken = clean + [voiced("bad", 30, 31.0, "Ông cho rằng Trung Quốc có nền tảng sản xuất vô cùng vững chắc", natural=3.0)]
    summary = summarize(broken, detect(broken, LAYOUT))
    assert summary["score"] < 100
    assert summary["by_type"]["voice_fast"] == 1 and summary["by_severity"]["error"] >= 1
    assert summary["cues_with_issues"] == 1
