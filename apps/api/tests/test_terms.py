import json

from app.services import terms, translator

VINFAST = {"target": "VinFast", "source": ["విన్ఫాస్ట్"], "wrong": ["Winfast", "WinFast"], "kind": "brand"}
VINGROUP = {"target": "Vingroup", "source": ["విన్ గ్రూప్"], "wrong": ["Win Group", "Tập đoàn Vinh"], "kind": "organization"}
VUONG = {"target": "Phạm Nhật Vượng", "source": ["ఫార్మ్ నాట్ వాంగ్", "హువాంగ్"], "wrong": ["Hoàng"], "kind": "person"}
CRORE = {"target": "1,4 tỷ người", "source": ["140 కోట్ల"], "kind": "number", "note": "1 crore = 10 triệu"}


def _sheet(*items):
    return terms.merge_terms(items)


def test_lookalike_misspellings_are_fixed_everywhere_other_words_only_where_the_source_says_so():
    sheet = _sheet(VINFAST, VINGROUP, VUONG)
    assert terms.fix_spelling("Winfast, Win Group, VinGroup và Hoàng", sheet) == "VinFast, Vingroup, Vingroup và Hoàng"
    assert (
        terms.fix_spelling("Tập đoàn Vinh trả tiền cho Hoàng", sheet, "విన్ గ్రూప్ ... హువాంగ్ కి")
        == "Vingroup trả tiền cho Phạm Nhật Vượng"
    )
    # Ordinary words that merely share letters with a name stay.
    assert terms.fix_spelling("hình vuông", sheet) == "hình vuông"


def test_missing_names_and_unconverted_amounts_are_flagged():
    sheet = _sheet(VUONG, CRORE)
    assert terms.issues_for("హువాంగ్ కి డబ్బులు", "mang lại tiền cho ông ấy", sheet) == [
        {"type": "name", "target": "Phạm Nhật Vượng"}
    ]
    assert terms.issues_for("దాదాపు 140 కోట్లకి", "hơn 140 triệu người", sheet) == [
        {"type": "number", "target": "1,4 tỷ người"}
    ]
    # The converted amount is right, and its changed digits are not a mismatch.
    assert terms.issues_for("దాదాపు 140 కోట్లకి", "hơn 1,4 tỷ người", sheet) == []
    assert terms.issues_for("2017 లో", "Năm 2018", []) == [{"type": "number", "values": ["2017"]}]


def test_merge_folds_a_term_whose_target_is_a_known_wrong_form():
    merged = terms.merge_terms([VINFAST], [{"target": "Winfast", "source": ["vinfast"], "kind": "brand"}])
    assert len(merged) == 1
    assert merged[0]["target"] == "VinFast"
    assert "vinfast" in merged[0]["source"]


def test_review_repairs_only_flagged_lines(monkeypatch):
    calls = []

    def fake_complete(system, user):
        calls.append(user)
        return {"fixes": [{"id": "2", "text": "Tiền về tay Phạm Nhật Vượng."}, {"id": "1", "text": "bịa"}]}

    monkeypatch.setattr(terms, "_complete", fake_complete)
    sheet = _sheet(VINFAST, VUONG)
    changed, report = terms.review_translation(
        [
            {"id": "1", "source": "విన్ఫాస్ట్ కార్స్", "text": "Xe Winfast"},
            {"id": "2", "source": "హువాంగ్ కి డబ్బులు", "text": "Tiền về tay Hoàng."},
            {"id": "3", "source": "ఏదో", "text": "Không có tên."},
        ],
        sheet,
        "te",
        "vi",
    )
    # Line 2 is fixed by spelling already (source mentions the person), so no LLM call is needed.
    assert changed == {"1": "Xe VinFast", "2": "Tiền về tay Phạm Nhật Vượng."}
    assert calls == []
    assert report["remaining"] == []


def test_review_asks_the_model_for_names_it_cannot_fix_alone(monkeypatch):
    monkeypatch.setattr(
        terms, "_complete", lambda system, user: {"fixes": [{"id": "1", "text": "Ông Phạm Nhật Vượng mở hãng mì."}]}
    )
    changed, report = terms.review_translation(
        [{"id": "1", "source": "ఫార్మ్ నాట్ వాంగ్ నూడిల్", "text": "Ông ấy mở hãng mì."}], _sheet(VUONG), "te", "vi"
    )
    assert changed == {"1": "Ông Phạm Nhật Vượng mở hãng mì."}
    assert report["fixed"][0]["how"] == "ai"


def test_translation_uses_one_sheet_for_every_batch(monkeypatch):
    monkeypatch.setenv("AETHER_TERM_CHECK", "1")
    monkeypatch.setenv("AETHER_SOURCE_FIRST_TRANSLATION_BATCH_CUES", "1")
    monkeypatch.setattr(terms, "load_dictionary", lambda: [])
    monkeypatch.setattr(
        terms,
        "_complete",
        lambda system, user: {"terms": [VINFAST]} if "terminology sheet a dubbing" in system else {"fixes": []},
    )
    glossaries = []

    def fake_batch(cues, source, target, rate, before, after, review, profile):
        glossaries.append((profile or {}).get("glossary"))
        return {cue.index: "Hãng Winfast" for cue in cues}

    monkeypatch.setattr(translator, "_translate_contextual_batch", fake_batch)
    srt = "1\n00:00:00,000 --> 00:00:02,000\nవిన్ఫాస్ట్ ఒకటి\n\n2\n00:00:02,000 --> 00:00:04,000\nవిన్ఫాస్ట్ రెండు\n"
    translated, payload = translator._translate_contextual_segments(srt, "te", "vi", None, None, None)

    assert glossaries == [{"విన్ఫాస్ట్": "VinFast"}] * 2
    assert translated.count("Hãng VinFast") == 2
    assert payload["terms"][0]["target"] == "VinFast"
    assert len(payload["term_review"]["fixed"]) == 2
    assert "sửa chính tả 2 câu" in terms.summary_line(payload["term_review"])


def test_extraction_failure_never_blocks_translation(monkeypatch):
    monkeypatch.setenv("AETHER_TERM_CHECK", "1")
    monkeypatch.setattr(terms, "load_dictionary", lambda: [])

    def boom(system, user):
        raise RuntimeError("offline")

    monkeypatch.setattr(terms, "_complete", boom)
    monkeypatch.setattr(translator, "_translate_contextual_batch", lambda cues, *a: {c.index: "xin chào" for c in cues})
    srt = "1\n00:00:00,000 --> 00:00:02,000\nhello\n"
    translated, payload = translator._translate_contextual_segments(srt, "en", "vi", None, None, None)
    assert "xin chào" in translated
    assert payload["terms"] == []


def test_dictionary_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(terms, "dictionary_path", lambda: tmp_path / "terms-dictionary.json")
    saved = terms.save_dictionary([VINFAST, {"target": "Winfast"}, {"target": " "}])
    assert [term["target"] for term in saved] == ["VinFast"]
    assert terms.load_dictionary()[0]["origin"] == "dictionary"
    assert json.loads((tmp_path / "terms-dictionary.json").read_text())["version"] == 1


def test_editor_overview_flags_lines():
    sheet = _sheet(VINFAST, VUONG)
    segments = [
        {"id": "a", "text": "VinFast ra mắt", "source_text": "విన్ఫాస్ట్"},
        {"id": "b", "text": "Winfast bán xe", "source_text": "విన్ఫాస్ట్"},
        {"id": "c", "text": "Ông ấy giàu", "source_text": "హువాంగ్"},
    ]
    view = {item["target"]: item for item in terms.overview(sheet, segments)}
    assert view["VinFast"]["uses"] == 1
    assert view["VinFast"]["variants"] == [{"text": "Winfast", "ids": ["b"]}]
    assert view["Phạm Nhật Vượng"]["missing_ids"] == ["c"]
    assert terms.segment_issue_ids(sheet, segments) == {"b", "c"}


def test_a_name_split_over_several_lines_counts_once():
    sheet = _sheet(VINFAST)
    segments = [
        {"id": "a", "text": "Năm 2017, hãng", "source_text": "2017 లో విన్ఫాస్ట్"},
        {"id": "b", "text": "VinFast ra đời", "source_text": "2017 లో విన్ఫాస్ట్"},
        {"id": "c", "text": "Hãng xe", "source_text": "విన్ఫాస్ట్ కార్స్"},
        {"id": "d", "text": "này", "source_text": "విన్ఫాస్ట్ కార్స్"},
    ]
    view = terms.overview(sheet, segments)[0]
    assert view["missing_ids"] == ["c"]


def test_terms_model_prefers_the_writing_model_on_openrouter(monkeypatch):
    monkeypatch.delenv("AETHER_TERMS_MODEL", raising=False)
    monkeypatch.setattr(translator, "_translation_provider", lambda: "openrouter")
    assert terms.terms_model()  # deepseek by default, or the configured writing model
    monkeypatch.setenv("AETHER_TERMS_MODEL", "x/y")
    assert terms.terms_model() == "x/y"
