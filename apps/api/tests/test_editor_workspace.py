from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.services import editor, storage, tts


def _srt(path: Path, cues: list[tuple[str, str, str]]) -> None:
    path.write_text(
        "\n\n".join(f"{n}\n{start} --> {end}\n{text}" for n, (start, end, text) in enumerate(cues, 1)) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def job_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path)
    root = storage.ensure_storage()
    job = "job-1"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:d=6", "-c:v", "libx264", str(root / "raw-videos" / f"{job}.mp4")],
        capture_output=True,
        check=True,
    )
    _srt(
        root / "subtitles" / f"{job}.vi.render.display.srt",
        [
            ("00:00:00,000", "00:00:02,000", "Xin chào."),
            ("00:00:02,000", "00:00:03,000", "Câu dài bị tràn."),
            ("00:00:03,000", "00:00:05,000", "你好"),
        ],
    )
    _srt(root / "subtitles" / f"{job}.vi.render.source.srt", [("00:00:00,000", "00:00:05,000", "原文")])
    chunk_dir = root / "audio" / f"{job}.chunks"
    chunk_dir.mkdir()
    chunks = []
    for index, (start, end) in enumerate([(0.0, 1.5), (2.0, 3.6), (3.0, 4.0)], start=1):
        audio = chunk_dir / f"{index:04d}_zerotts_raw.wav"
        audio.write_bytes(b"RIFF")
        chunks.append({"chunk": index, "start": start, "end": end, "audio": str(audio), "text": ""})
    (root / "audio" / f"{job}.aligned.chunks.json").write_text(json.dumps({"chunks": chunks}), encoding="utf-8")
    config = {"subtitle": {"style_snapshot": {"hard_sub_blur_enabled": True, "hard_sub_blur_y": 0.9}}}
    return job, config


def test_build_maps_chunks_and_flags_issues(job_storage) -> None:
    job, config = job_storage
    workspace = editor.with_issues(editor.build_workspace(job, config))

    assert workspace["width"] == 160 and workspace["blur"]["enabled"] is True
    first, overrun, untranslated = workspace["segments"]
    assert first["source_text"] == "原文"
    assert first["audio_url"].endswith("0001_zerotts_raw.wav") and first["issues"] == []
    assert overrun["issues"] == ["overrun"]
    assert "untranslated" in untranslated["issues"]


def test_patch_text_marks_audio_stale_and_keeps_voice_in_place(job_storage) -> None:
    job, config = job_storage
    workspace = editor.build_workspace(job, config)

    workspace = editor.patch_workspace(
        workspace,
        [{"id": "seg-0001", "text": "Chào bạn.", "start": 0.5, "end": 2.5}],
        {"y": 0.8, "enabled": False},
    )
    segment = editor.with_issues(workspace)["segments"][0]

    assert segment["issues"] == ["stale_audio"]
    assert segment["audio_start"] == 0.0  # Subtitles and voice are separate tracks.
    assert workspace["blur"]["y"] == 0.8 and workspace["blur"]["enabled"] is False
    assert editor.load_workspace(job)["segments"][0]["text"] == "Chào bạn."


def test_regenerate_segment_audio_fits_to_next_cue(job_storage, monkeypatch) -> None:
    job, config = job_storage
    workspace = editor.build_workspace(job, config)
    calls = {}

    def fake_save(text, voice, output, rate, workdir, label, target_duration, overrides):
        calls["text"] = text
        output.write_bytes(b"mp3")
        return output

    def fake_fit(source, dest, max_duration):
        calls["slot"] = max_duration
        return source, 0.9

    monkeypatch.setattr(tts, "_save_tts_segment", fake_save)
    monkeypatch.setattr(tts, "_fit_audio_to_slot", fake_fit)
    workspace = editor.patch_workspace(workspace, [{"id": "seg-0002", "text": "Ngắn."}], None)

    workspace = editor.regenerate_segment_audio(
        workspace, "seg-0002", voice="v", provider="edge", voice_rate="+0%", target_language="VI", voice_overrides=None
    )
    segment = editor.with_issues(workspace)["segments"][1]

    assert calls["text"] == "Ngắn."
    assert calls["slot"] == pytest.approx(0.92)
    assert segment["issues"] == [] and segment["audio_duration"] == 0.9
    assert segment["audio_url"].startswith(f"/storage/editor/{job}/audio/")
    assert segment["previous_audio_url"].endswith("0002_zerotts_raw.wav")
    assert segment["audio_generated_at"]

    workspace = editor.swap_previous_audio(workspace, "seg-0002")
    swapped = workspace["segments"][1]
    assert swapped["audio_url"].endswith("0002_zerotts_raw.wav") and swapped["audio_duration"] == 1.6
    assert swapped["previous_audio_url"] == segment["audio_url"]


def test_write_srt_skips_empty_segments(job_storage, tmp_path) -> None:
    job, config = job_storage
    workspace = editor.patch_workspace(editor.build_workspace(job, config), [{"id": "seg-0003", "text": ""}], None)

    srt = editor.write_srt(workspace, tmp_path / "out.srt").read_text(encoding="utf-8")

    assert srt.count("-->") == 2
    assert "00:00:02,000 --> 00:00:03,000\nCâu dài bị tràn." in srt


def test_delete_segment_removes_cue_and_its_voice(job_storage) -> None:
    job, config = job_storage
    workspace = editor.delete_segment(editor.build_workspace(job, config), "seg-0002")

    assert [segment["id"] for segment in workspace["segments"]] == ["seg-0001", "seg-0003"]
    assert [segment["id"] for segment in editor.load_workspace(job)["segments"]] == ["seg-0001", "seg-0003"]
    # Pipeline chunks are shared with the original render and must survive.
    assert (storage.STORAGE_ROOT / "audio" / f"{job}.chunks" / "0002_zerotts_raw.wav").exists()
    with pytest.raises(KeyError):
        editor.delete_segment(workspace, "seg-0002")


def test_split_keeps_voice_on_first_part_and_merge_restores(job_storage) -> None:
    job, config = job_storage
    workspace = editor.patch_workspace(
        editor.build_workspace(job, config),
        [{"id": "seg-0001", "text": "Tối hôm đó, hoa và nến xuất hiện khắp nơi."}],
        None,
    )
    workspace["segments"][0]["audio_text"] = workspace["segments"][0]["text"]

    workspace = editor.split_segment(workspace, "seg-0001")
    first, second = editor.with_issues(workspace)["segments"][:2]

    assert first["text"] == "Tối hôm đó," and second["text"] == "hoa và nến xuất hiện khắp nơi."
    assert first["end"] == second["start"] and 0 < first["end"] < 2.0 and second["end"] == 2.0
    # The original take still voices both parts, so neither is flagged.
    assert first["issues"] == [] and second["issues"] == []
    assert second["shares_audio_with"] == "seg-0001"

    merged = editor.with_issues(editor.merge_with_next(workspace, "seg-0001"))["segments"][0]
    assert merged["text"] == "Tối hôm đó, hoa và nến xuất hiện khắp nơi."
    assert merged["end"] == 2.0 and merged["issues"] == []


def test_split_at_caret_never_cuts_inside_a_word(job_storage) -> None:
    job, config = job_storage
    workspace = editor.build_workspace(job, config)

    workspace = editor.split_segment(workspace, "seg-0002", at_char=6)

    assert [segment["text"] for segment in workspace["segments"][1:3]] == ["Câu dài", "bị tràn."]


def test_audio_volumes_default_from_job_and_clamp(job_storage) -> None:
    job, config = job_storage
    config["audio"] = {"original_volume": 13, "dubbed_volume": 100}
    workspace = editor.build_workspace(job, config)
    on = {"dubbed_enabled": True, "original_enabled": True}
    assert workspace["audio"] == {"original_volume": 13.0, "dubbed_volume": 100.0, **on}

    workspace = editor.patch_workspace(workspace, None, None, {"original_volume": 450})

    assert workspace["audio"] == {"original_volume": 200.0, "dubbed_volume": 100.0, **on}


def test_audio_switches_are_saved_and_old_workspaces_default_on(job_storage) -> None:
    job, config = job_storage
    workspace = editor.build_workspace(job, config)

    workspace = editor.patch_workspace(workspace, None, None, {"dubbed_enabled": False})

    assert workspace["audio"]["dubbed_enabled"] is False
    assert workspace["audio"]["original_enabled"] is True
    assert editor.normalize_audio(None, {"original_volume": 50, "dubbed_volume": 80})["dubbed_enabled"] is True


def test_display_wraps_like_the_burned_in_ass(job_storage) -> None:
    job, config = job_storage
    long_text = (
        "Tối hôm đó, trước các cửa hàng Apple trên toàn cầu, hoa và nến tự nhiên "
        "xuất hiện để tưởng nhớ người đàn ông đã thay đổi thế giới."
    )
    workspace = editor.patch_workspace(
        editor.build_workspace(job, config), [{"id": "seg-0001", "text": long_text}], None
    )
    flagged = editor.with_issues(workspace)
    layout = flagged["subtitle"]
    segment = flagged["segments"][0]

    assert layout["max_lines"] == 2 and layout["font_px"] > 0
    # Never more than max_lines on screen; overflow becomes consecutive timed displays.
    assert all(len(part["lines"]) <= 2 for part in segment["display"])
    assert " ".join(line for part in segment["display"] for line in part["lines"]) == long_text
    assert segment["display"][0]["start"] == 0.0 and segment["display"][-1]["end"] == 2.0


def test_style_patch_is_normalized_and_drives_layout(job_storage) -> None:
    job, config = job_storage
    workspace = editor.patch_workspace(
        editor.build_workspace(job, config),
        None,
        None,
        style={"box_style": "panel", "box_opacity": 0.6, "box_radius": 5, "font_size": 30, "hard_sub_blur_y": 0.1},
        max_lines=1,
    )

    assert workspace["style"]["box_style"] == "panel" and workspace["style"]["box_radius"] == 1.0
    assert "hard_sub_blur_y" not in workspace["style"]  # Blur stays in its own section.
    layout = editor.with_issues(workspace)["subtitle"]
    assert layout["font_px"] == round(30 * 160 / 512) and layout["max_lines"] == 1
    assert editor.subtitle_style(workspace)["hard_sub_blur_y"] == 0.9


def test_original_regions_are_clipped_sorted_and_non_overlapping(job_storage) -> None:
    job, config = job_storage
    workspace = editor.patch_workspace(
        editor.build_workspace(job, config),
        None,
        None,
        original_regions=[
            {"start": 4.0, "end": 9.0, "volume": 900},
            {"start": 1.0, "end": 3.0, "volume": 250},
            {"start": 2.0, "end": 2.05, "volume": 50},
            {"start": 2.5, "end": 3.5, "volume": 0},
        ],
    )

    regions = workspace["original_regions"]
    assert [(r["start"], r["end"], r["volume"]) for r in regions] == [
        (1.0, 3.0, 250.0),
        (3.0, 3.5, 0.0),
        (4.0, 6.0, 400.0),
    ]


def test_waveform_is_cached_peaks(job_storage) -> None:
    job, config = job_storage
    workspace = editor.build_workspace(job, config)

    data = editor.original_waveform(workspace, rate=10)

    assert data["rate"] == 10
    assert data["peaks"] == []  # The fixture video has no audio stream.
    assert (editor.workspace_dir(job) / "waveform-10.json").exists()


def test_drafts_snapshot_and_restore(job_storage) -> None:
    job, config = job_storage
    workspace = editor.build_workspace(job, config)
    draft = editor.save_draft(workspace, "Trước khi sửa")
    workspace = editor.patch_workspace(workspace, [{"id": "seg-0001", "text": "Đã sửa."}], None)

    restored = editor.restore_draft(workspace, draft["id"])

    assert restored["segments"][0]["text"] == "Xin chào."
    names = [item["name"] for item in editor.list_drafts(job)]
    assert names[0] == "Tự lưu trước khi khôi phục" and "Trước khi sửa" in names
    assert editor.load_workspace(job)["segments"][0]["text"] == "Xin chào."


def test_patch_request_rejects_unknown_fields() -> None:
    from pydantic import ValidationError

    from app.schemas import EditorPatchRequest

    with pytest.raises(ValidationError):
        EditorPatchRequest.model_validate({"segments": [], "brand_new_field": 1})


def test_region_fades_are_capped_by_region_length(job_storage) -> None:
    job, config = job_storage
    workspace = editor.patch_workspace(
        editor.build_workspace(job, config),
        None,
        None,
        original_regions=[{"start": 1.0, "end": 3.0, "volume": 200, "fade_in": 1.5, "fade_out": 9}],
    )

    region = workspace["original_regions"][0]
    assert region["fade_in"] == 1.5 and region["fade_out"] == 0.5


def test_replace_state_restores_an_earlier_snapshot(job_storage) -> None:
    job, config = job_storage
    workspace = editor.build_workspace(job, config)
    before = editor.with_issues(workspace)  # What the editor holds, derived fields included.
    snapshot = {key: before[key] for key in editor.EDITABLE_KEYS}
    workspace = editor.patch_workspace(
        workspace, [{"id": "seg-0001", "text": "Đã sửa."}], {"y": 0.5}, style={"font_size": 40}
    )
    workspace = editor.delete_segment(workspace, "seg-0002")

    restored = editor.replace_state(workspace, snapshot)

    assert [segment["text"] for segment in restored["segments"]] == ["Xin chào.", "Câu dài bị tràn.", "你好"]
    assert "issues" not in restored["segments"][0] and "display" not in restored["segments"][0]
    assert restored["blur"]["y"] == 0.9 and restored["style"]["font_size"] == before["style"]["font_size"]


def test_trimming_a_cue_edge_leaves_its_voice_in_place(job_storage) -> None:
    job, config = job_storage
    workspace = editor.patch_workspace(
        editor.build_workspace(job, config), [{"id": "seg-0001", "start": 0.4, "end": 1.8}], None
    )

    segment = workspace["segments"][0]
    assert (segment["start"], segment["end"], segment["audio_start"]) == (0.4, 1.8, 0.0)


def _real_take(job_storage, seconds: float = 2.0) -> None:
    job, _config = job_storage
    path = storage.STORAGE_ROOT / "audio" / f"{job}.chunks" / "0001_zerotts_raw.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=f=300:d={seconds}", str(path)], capture_output=True, check=True
    )


def test_voice_moves_without_its_subtitle(job_storage) -> None:
    job, config = job_storage
    workspace = editor.patch_workspace(
        editor.build_workspace(job, config), [{"id": "seg-0001", "audio_start": 0.6}], None
    )

    segment = workspace["segments"][0]
    assert segment["audio_start"] == 0.6 and (segment["start"], segment["end"]) == (0.0, 2.0)


def test_voice_length_retimes_from_the_unfitted_take(job_storage) -> None:
    _real_take(job_storage)
    job, config = job_storage
    workspace = editor.build_workspace(job, config)

    workspace = editor.patch_workspace(workspace, [{"id": "seg-0001", "audio_length": 1.0}], None)
    segment = workspace["segments"][0]
    assert segment["audio_duration"] == pytest.approx(1.0, abs=0.06)
    assert segment["voice_slot"] == 1.0 and segment["audio_source_url"].endswith("0001_zerotts_raw.wav")

    # Asking for far longer than 0.8× speed allows is capped at natural / 0.8.
    workspace = editor.patch_workspace(workspace, [{"id": "seg-0001", "audio_length": 9}], None)
    segment = workspace["segments"][0]
    assert segment["voice_slot"] == pytest.approx(2.5, abs=0.06)
    assert segment["audio_duration"] == pytest.approx(2.5, abs=0.08)


def test_insert_segment_adds_an_unvoiced_cue_in_order(job_storage, monkeypatch) -> None:
    job, config = job_storage
    workspace, new_id = editor.insert_segment(editor.build_workspace(job, config), 5.2, 5.9, " Câu mới ")

    ids = [segment["id"] for segment in workspace["segments"]]
    assert ids[-1] == new_id
    segment = editor.with_issues(workspace)["segments"][-1]
    assert (segment["start"], segment["end"], segment["text"]) == (5.2, 5.9, "Câu mới")
    assert segment["issues"] == ["no_audio"]

    def fake_save(text, voice, output, rate, workdir, label, target_duration, overrides):
        output.write_bytes(b"mp3")
        return output

    monkeypatch.setattr(tts, "_save_tts_segment", fake_save)
    monkeypatch.setattr(tts, "_probe_duration", lambda path: 0.6)
    monkeypatch.setattr(tts, "_fit_audio_to_slot", lambda source, dest, slot: (source, 0.6))
    workspace = editor.regenerate_segment_audio(
        workspace, new_id, voice="v", provider="edge", voice_rate="+0%", target_language="VI", voice_overrides=None
    )
    voiced = editor.with_issues(workspace)["segments"][-1]
    assert voiced["audio_start"] == 5.2 and voiced["issues"] == []


def test_editor_uses_sentences_when_chunks_match(job_storage) -> None:
    job, config = job_storage
    subtitles = storage.STORAGE_ROOT / "subtitles"
    display = next(subtitles.glob(f"{job}*.render.display.srt"))
    sentence_file = display.with_name(display.name.replace(".render.display.srt", ".render.srt"))
    manifest = next((storage.STORAGE_ROOT / "audio").glob(f"{job}*.chunks.json"))
    chunks = json.loads(manifest.read_text(encoding="utf-8"))["chunks"]
    blocks = [
        f"{index}\n00:00:{int(chunk['start']):02d},000 --> 00:00:{int(chunk['end']) + 1:02d},000\nCâu {index}."
        for index, chunk in enumerate(chunks, start=1)
    ]
    sentence_file.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    workspace = editor.build_workspace(job, config)
    assert [segment["text"] for segment in workspace["segments"]] == [f"Câu {i}." for i in range(1, len(chunks) + 1)]
    assert all(segment["audio_url"] for segment in workspace["segments"])
