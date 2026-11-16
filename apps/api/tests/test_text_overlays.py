"""Editor text boxes: normalization, ASS generation, burn-in and workspace round trips."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import editor, renderer
from app.services.text_overlays import normalize_text_overlays, write_overlays_ass

from test_editor_workspace import job_storage  # noqa: F401  (fixture)


def test_normalize_clamps_fields_and_drops_empty_boxes() -> None:
    items = normalize_text_overlays(
        [
            {"id": "b", "text": "Second", "start": 4, "end": 2, "size": 9, "color": "red", "align": "diagonal"},
            {"id": "a", "text": "First", "start": -3, "end": 99, "x": 2, "bg_opacity": -1},
            {"id": "c", "text": "   "},
            "junk",
        ],
        duration=10,
    )
    assert [item["id"] for item in items] == ["a", "b"]
    first, second = items
    assert (first["start"], first["end"], first["x"], first["bg_opacity"]) == (0.0, 10.0, 1.0, 0.0)
    assert second["end"] == 4.1  # At least 0.1 s long.
    assert (second["size"], second["color"], second["align"]) == (0.3, "#FFFFFF", "center")


def test_ass_places_boxes_in_frame_pixels(tmp_path) -> None:
    overlays = normalize_text_overlays(
        [
            {"text": "Hello {world}\nline two", "start": 1, "end": 2.5, "x": 0.25, "y": 0.1, "size": 0.05, "align": "left"},
            {"text": "plain", "bg_opacity": 0, "color": "#FF8800"},
        ],
        duration=10,
    )
    path = write_overlays_ass(overlays, (1920, 1080), tmp_path / "o.ass")
    text = path.read_text(encoding="utf-8")
    assert "PlayResX: 1920" in text and "PlayResY: 1080" in text
    boxed = next(line for line in text.splitlines() if "Hello" in line)
    # Sorted by start, so "plain" (start 0) is layer 10 and this one is drawn above it.
    assert boxed.startswith("Dialogue: 11,0:00:01.00,0:00:02.50,Box,")
    assert "\\an4\\pos(480,108)\\fs54" in boxed and "Hello (world)\\Nline two" in boxed
    plain = next(line for line in text.splitlines() if "plain" in line)
    assert ",Plain," in plain and "\\1c&H0088FF&" in plain
    assert write_overlays_ass([], (10, 10), tmp_path / "none.ass") is None


BUNDLED_BIN = Path(__file__).resolve().parents[2] / "desktop" / "src-tauri" / "resources" / "bin"


def _ffmpeg_with_libass() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " ass " in filters


def test_overlays_burn_into_the_render_filter(tmp_path, monkeypatch) -> None:
    # The app ships an ffmpeg with libass; Homebrew's build has no `ass` filter.
    if (BUNDLED_BIN / "ffmpeg").exists():
        monkeypatch.setenv("PATH", f"{BUNDLED_BIN}{os.pathsep}{os.environ.get('PATH', '')}")
    if not _ffmpeg_with_libass():
        pytest.skip("ffmpeg without libass")
    monkeypatch.setattr(renderer, "ensure_storage", lambda: tmp_path)
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1", str(video)], check=True)
    overlays = normalize_text_overlays([{"text": "Xin chào", "start": 0, "end": 1, "y": 0.5, "bg_opacity": 1, "bg_color": "#FFFFFF", "color": "#FFFFFF"}], 1)

    graph = renderer._video_filter(video, None, None, "source", text_overlays=overlays)
    # Text alone also gets the crisp 1080p canvas.
    assert graph.startswith("scale=1920:1080:flags=lanczos,") and "overlays-" in graph

    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", graph, "-frames:v", "1", str(frame)], check=True)
    # The (all-white) box is drawn at the centre of the black frame.
    probe = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(frame), "-vf", "crop=4:4:958:538,scale=1:1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    )
    assert probe.stdout[0] > 200
    assert renderer._video_filter(video, None, None, "source", text_overlays=[]) == ""


def test_workspace_patch_and_undo_state_keep_text_overlays(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.build_workspace(job, config)
    assert workspace["text_overlays"] == []

    workspace = editor.patch_workspace(workspace, None, None, overlays=[{"id": "t1", "text": "Title", "start": 1, "end": 2}])
    assert [item["id"] for item in editor.load_workspace(job)["text_overlays"]] == ["t1"]
    assert editor.with_issues(workspace)["text_overlays"][0]["text"] == "Title"

    state = {key: workspace[key] for key in editor.EDITABLE_KEYS}
    state["text_overlays"] = []
    assert editor.replace_state(workspace, state)["text_overlays"] == []


def test_voice_change_rebuilds_workspace_and_keeps_voice_independent_edits(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.build_workspace(job, config)
    workspace = editor.patch_workspace(
        workspace,
        [{"id": "seg-0001", "text": "Sửa tay"}],
        None,
        audio={"original_volume": 40},
        overlays=[{"id": "t1", "text": "Title", "start": 0, "end": 1}],
    )

    assert editor.retire_for_new_voice(job, "Giọng mới")
    assert editor.load_workspace(job) is None
    drafts = list(editor.drafts_dir(job).glob("*.json"))
    assert len(drafts) == 1 and "Giọng mới" in drafts[0].read_text(encoding="utf-8")

    rebuilt = editor.apply_carry_over(editor.build_workspace(job, config))
    # Cues come from the new takes; mix, styling and text boxes survive.
    assert rebuilt["segments"][0]["text"] != "Sửa tay"
    assert rebuilt["audio"]["original_volume"] == 40
    assert [item["id"] for item in rebuilt["text_overlays"]] == ["t1"]
    assert not (editor.workspace_dir(job) / editor.CARRY_OVER_FILE).exists()
    assert not editor.retire_for_new_voice("no-such-job", "x")
