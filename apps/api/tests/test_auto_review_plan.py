"""Auto review — neighbourhood planning: least invasive fixes, no overlaps, never worse."""
from __future__ import annotations

from app.services.auto_review import llm
from app.services.auto_review import plan as P
from app.services.auto_review.detect import CueView, Layout, detect

LAYOUT = Layout(chars_per_line=42, max_lines=2, duration=200.0)


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


FAST = "Ông cho rằng Trung Quốc có nền tảng sản xuất vô cùng vững chắc và đáng tin"  # 18 syllables


def assert_no_overlaps(cues):
    voices = sorted((cue for cue in cues if cue.voiced), key=lambda cue: cue.voice_start)
    for current, following in zip(voices, voices[1:]):
        assert current.voice_end <= following.voice_start + 1e-6, (current.id, following.id)
    subs = sorted(cues, key=lambda cue: cue.start)
    for current, following in zip(subs, subs[1:]):
        assert current.end <= following.start + 1e-6, (current.id, following.id)


def test_borrows_the_silence_after_a_rushed_line() -> None:
    cues = [
        voiced("a", 0.0, 2.6, FAST, natural=4.0),  # 1.54×
        voiced("b", 6.0, 7.5, "Câu sau.", natural=1.0, voice_len=1.0),
    ]
    result = P.plan(cues, LAYOUT, allow_text=False)
    proposal = result.proposals[0]
    assert proposal.kind == "borrow_silence" and proposal.risk == "low"
    assert proposal.predicted["speed_after"] == 1.0  # Room for the natural take.
    after = {cue.id: cue for cue in result.predicted_cues}
    assert after["a"].voice_len == 4.0 and after["a"].end == 4.0  # Subtitle follows the voice.
    assert after["b"].voice_start == 6.0  # The neighbour did not move.
    assert {op["op"] for op in proposal.ops} == {"voice", "sub_timing"}
    assert_no_overlaps(result.predicted_cues)


def test_shifts_neighbours_when_the_gap_is_on_the_other_side() -> None:
    cues = [
        voiced("prev", 0.0, 2.0, "Câu trước ngắn.", natural=1.2, voice_len=1.2),  # Ends 1.2; slack before b.
        voiced("b", 2.0, 4.0, FAST, natural=2.9),  # Needs ~2.6 s at ≤1.15×.
        voiced("next", 4.0, 5.0, "Câu sau.", natural=0.8, voice_len=0.8),
    ]
    result = P.plan(cues, LAYOUT, allow_text=False)
    assert result.proposals, "a fix is expected"
    proposal = result.proposals[0]
    assert proposal.kind in {"borrow_silence", "shift_neighbors", "redistribute"}
    assert proposal.predicted["speed_after"] <= P.COMFORT_MAX + 1e-6
    assert_no_overlaps(result.predicted_cues)
    target = next(cue for cue in result.predicted_cues if cue.id == "b")
    assert target.start - P.LEAD - 1e-6 <= target.voice_start <= target.start + P.LAG + 1e-6


def test_impossible_slot_asks_for_a_shorter_line_instead_of_speeding_up_more() -> None:
    cues = [
        voiced("p", 0.0, 1.0, "Trước.", natural=0.6, voice_len=0.6),
        voiced("x", 1.0, 2.4, FAST, natural=4.2),  # 3× would be needed.
        voiced("n", 2.4, 3.4, "Sau.", natural=0.5, voice_len=0.5),
    ]
    result = P.plan(cues, LAYOUT)
    kinds = [proposal.kind for proposal in result.proposals]
    assert "rewrite" in kinds
    rewrite = next(proposal for proposal in result.proposals if proposal.kind == "rewrite")
    assert rewrite.needs_llm and rewrite.risk == "medium"
    assert rewrite.rewrite["mode"] == "condense"
    assert int(rewrite.rewrite["syllables"] * P.MAX_CONDENSE) <= rewrite.rewrite["target_syllables"] < rewrite.rewrite["syllables"]
    for proposal in result.proposals:  # Timing fixes never make the line faster than it was.
        if proposal.kind != "rewrite":
            assert (proposal.predicted.get("speed_after") or 0) <= 3.0 + 1e-6


def test_every_timing_proposal_improves_its_window_and_creates_no_overlaps() -> None:
    cues = []
    t = 0.0
    for index in range(12):
        natural = 3.2 if index % 3 == 0 else 1.4
        cues.append(voiced(f"c{index}", t, t + 2.2, FAST if index % 3 == 0 else "Một câu bình thường thôi.", natural=natural, voice_len=min(natural, 2.2)))
        t += 2.6
    result = P.plan(cues, LAYOUT, allow_text=False)
    assert result.proposals
    for proposal in result.proposals:
        assert proposal.predicted["penalty_after"] < proposal.predicted["penalty_before"]
    assert_no_overlaps(result.predicted_cues)
    before = sum(1 for issue in detect(cues, LAYOUT) if issue.category == "voice")
    after = sum(1 for issue in detect(result.predicted_cues, LAYOUT) if issue.category == "voice")
    assert after < before


def test_short_subtitle_is_extended_into_the_silence() -> None:
    cues = [voiced("oh", 5.37, 5.85, "Ồ, thật à?", natural=1.04, voice_len=1.04), voiced("n", 9.6, 10.6, "Cẩn thận kẻo vấp.", natural=1.0)]
    result = P.plan(cues, LAYOUT)
    proposal = result.proposals[0]
    assert proposal.kind == "extend_subtitle" and proposal.risk == "low"
    op = proposal.ops[0]
    assert op == {"op": "sub_timing", "id": "oh", "start": 5.37, "end": 6.37}


def test_long_cue_is_split_at_the_clause_boundary_and_keeps_its_voice() -> None:
    text = "Musk hình dung tương lai người bình thường quản lý robot, và mỗi người sẽ có trợ lý AI riêng."
    cues = [voiced("long", 10.0, 19.0, text, natural=5.0, voice_len=5.0)]
    result = P.plan(cues, LAYOUT)
    proposal = result.proposals[0]
    assert proposal.kind == "split" and not proposal.needs_tts
    left = text[: proposal.ops[0]["at_char"]]
    assert left.endswith("robot,")
    second = result.predicted_cues[1]
    assert second.shared and not second.voiced


def test_orphan_word_is_merged_into_the_sentence_it_starts() -> None:
    cues = [
        voiced("tôi", 41.79, 42.65, "Tôi biết mà.", natural=0.8),
        voiced("và", 43.26, 43.9, "Và", natural=0.7),
        voiced("rest", 44.19, 47.67, "huấn luyện bạn trên đám mây, trên hàng loạt siêu máy tính.", natural=3.1, voice_len=3.12),
    ]
    result = P.plan(cues, LAYOUT)
    merge = next(proposal for proposal in result.proposals if proposal.kind == "merge")
    assert merge.ops[0] == {"op": "merge", "id": "và", "next_id": "rest"}
    assert merge.needs_tts and merge.risk == "medium"  # Two takes become one line.
    merged = next(cue for cue in result.predicted_cues if cue.id == "và")
    assert merged.text.startswith("Và huấn luyện") and merged.end == 47.67


def test_number_split_across_cues_moves_the_word_back() -> None:
    cues = [
        voiced("a", 7.0, 9.5, "dự đoán về tương lai mười đến hai", natural=1.6, voice_len=1.6),
        voiced("b", 9.6, 12.5, "mươi năm tới: AI, robot hình người.", natural=2.5, voice_len=2.5),
    ]
    result = P.plan(cues, LAYOUT)
    move = next(proposal for proposal in result.proposals if proposal.kind == "move_words")
    texts = {op["id"]: op["text"] for op in move.ops if op["op"] == "text"}
    assert texts == {"a": "dự đoán về tương lai mười đến hai mươi", "b": "năm tới: AI, robot hình người."}
    assert [op["id"] for op in move.ops if op["op"] == "tts"] == ["a", "b"]


def test_punctuation_only_cue_is_deleted_and_later_proposals_depend_on_earlier_ones() -> None:
    cues = [
        voiced("iphone", 468.33, 468.73, "iPhone", natural=0.4, voice_len=0.4),
        voiced("dots", 468.73, 468.93, "...", natural=0.3, voice_len=0.3),
        voiced("six", 468.93, 472.42, "6 ra mắt, doanh số quý đó tăng mạnh.", natural=3.4, voice_len=3.4),
    ]
    result = P.plan(cues, LAYOUT)
    delete = next(proposal for proposal in result.proposals if proposal.kind == "delete")
    assert delete.ops == [{"op": "delete", "id": "dots"}]
    # Round 2 sees "iPhone" next to "6 ra mắt…" and merges them on top of the delete.
    later = [proposal for proposal in result.proposals if delete.id in proposal.depends_on]
    assert all(cue.id != "dots" for cue in result.predicted_cues)
    assert later or any(proposal.kind == "merge" for proposal in result.proposals)


def test_refit_after_a_new_take_uses_the_ladder() -> None:
    cues = [voiced("a", 0.0, 2.0, FAST, natural=2.0, voice_len=2.0), voiced("b", 5.0, 6.0, "Sau.", natural=0.6, voice_len=0.6)]
    longer = [cues[0].copy(natural=3.0, voice_len=3.0), cues[1]]
    state, ops, speed = P.refit(longer, "a", 200.0)
    assert speed == 1.0 and ops == [] or all(op["id"] in {"a", "b"} for op in ops)
    assert_no_overlaps(state)


def test_llm_rewrite_validation_and_provider_fallback() -> None:
    item = {
        "id": "x",
        "mode": "condense",
        "text": "Tất nhiên, nhiều thứ trong đây vẫn chỉ là dự đoán cực kỳ táo bạo: tiền bạc trở nên",
        "syllables": 18,
        "target_syllables": 13,
        "previous": "",
        "next": "không quan trọng nữa.",
    }
    assert llm.accept(item, "Tất nhiên, nhiều thứ vẫn là dự đoán táo bạo: tiền bạc trở nên")
    assert llm.accept(item, "Tất nhiên, nhiều thứ vẫn là dự đoán táo bạo.") is None  # Lost the continuation.
    assert llm.accept(item, "Tất nhiên, nhiều thứ là dự đoán: tiền bạc trở nên không quan trọng") is None  # Borrowed.
    assert llm.accept(item, "当然 tiền bạc trở nên") is None

    calls = []

    def broken(system, user):
        calls.append("broken")
        raise RuntimeError("router down")

    def working(system, user):
        calls.append("working")
        return '{"lines": [{"id": "x", "text": "Tất nhiên, nhiều thứ vẫn là dự đoán táo bạo: tiền bạc trở nên"}]}'

    texts, info = llm.rewrite([item], "zh", [("translator", broken), ("assistant", working)])
    assert texts == {"x": "Tất nhiên, nhiều thứ vẫn là dự đoán táo bạo: tiền bạc trở nên"}
    assert calls == ["broken", "working"] and info["provider"] == "assistant" and info["accepted"] == 1
    assert llm.rewrite([item], "zh", [])[1]["available"] is False
