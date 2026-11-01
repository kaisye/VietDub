"""Editor audio tracks: schema normalization, ducking automation, ffmpeg graph and real renders."""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import pytest

from app.services import editor, music_library, music_mix, renderer, storage

from test_editor_workspace import job_storage  # noqa: F401  (fixture)


def _tone(path: Path, seconds: float, freq: int = 440, volume: float = 0.3) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}:r=44100",
         "-af", f"volume={volume * 8}", "-ac", "2", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def _rms_db(path: Path, start: float, end: float) -> float:
    result = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", f"atrim={start}:{end},volumedetect", "-vn", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    match = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", result.stderr)
    assert match, result.stderr[-500:]
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))


# ---- schema ---------------------------------------------------------------------------


def test_normalize_defaults_clamps_and_drops_bad_clips() -> None:
    tracks = music_mix.normalize_audio_tracks(
        [
            {
                "name": "",
                "solo": True,
                "volume": 900,
                "clips": [
                    {"asset_id": "mus-a", "start": -3, "in": 2, "duration": 50, "fade_in": 40, "fade_out": 40},
                    {"asset_id": "", "start": 1, "duration": 2},  # no asset
                    {"asset_id": "mus-b", "start": 3, "duration": 0.01},  # too short
                ],
            },
            "garbage",
        ],
        duration=10.0,
    )
    assert len(tracks) == 1
    track = tracks[0]
    assert track["name"] == "Nhạc 1" and track["volume"] == 200 and track["kind"] == "music"
    assert "solo" not in track  # preview-only, never persisted
    [clip] = track["clips"]
    assert clip["start"] == 0 and clip["duration"] == 10.0 and clip["in"] == 2
    assert clip["fade_in"] == 10.0 and clip["fade_out"] == 0.0  # fades share the clip length
    assert clip["ducking"] == {"enabled": True, "amount_db": -12.0, "attack": 0.3, "release": 0.6}
    assert music_mix.normalize_audio_tracks(None, 10) == []


def test_sfx_tracks_are_not_ducked_by_default() -> None:
    [track] = music_mix.normalize_audio_tracks([{"kind": "sfx", "clips": [{"asset_id": "x", "duration": 1}]}], 5)
    assert track["clips"][0]["ducking"]["enabled"] is False


def test_apply_ops_add_update_move_delete() -> None:
    tracks = music_mix.apply_ops(
        [],
        [
            {"op": "add_track", "track": {"id": "t1", "name": "Nhạc nền"}},
            {"op": "add_track", "track": {"id": "t2", "kind": "sfx"}},
            {"op": "add_clip", "track_id": "t1", "clip": {"id": "c1", "asset_id": "a", "start": 1, "duration": 4}},
            {"op": "update_clip", "id": "c1", "patch": {"volume": 50, "ducking": {"amount_db": -18}}},
            {"op": "move_clip", "id": "c1", "start": 2.5, "to_track": "t2"},
            {"op": "update_track", "id": "t1", "patch": {"muted": True}},
        ],
        duration=20,
    )
    t1, t2 = tracks
    assert t1["muted"] is True and t1["clips"] == []
    [clip] = t2["clips"]
    assert clip["start"] == 2.5 and clip["volume"] == 50
    assert clip["ducking"]["amount_db"] == -18 and clip["ducking"]["attack"] == 0.3
    tracks = music_mix.apply_ops(tracks, [{"op": "delete_clip", "id": "c1"}, {"op": "delete_track", "id": "t1"}], 20)
    assert [track["id"] for track in tracks] == ["t2"] and tracks[0]["clips"] == []
    with pytest.raises(KeyError):
        music_mix.apply_ops(tracks, [{"op": "delete_clip", "id": "nope"}], 20)


# ---- ducking ----------------------------------------------------------------------------


def test_voice_intervals_follow_the_voice_mix_rules() -> None:
    workspace = {
        "audio": {"dubbed_enabled": True},
        "segments": [
            {"audio_url": "/storage/a.mp3", "audio_start": 4.0, "audio_duration": 1.0},
            {"audio_url": "/storage/b.mp3", "audio_start": 1.0, "audio_duration": 2.0, "muted": True},
            {"audio_url": None, "audio_start": 0.0, "audio_duration": 0.0},
            {"audio_url": "/storage/c.mp3", "audio_start": 0.5, "audio_duration": 1.0},
        ],
    }
    assert music_mix.voice_intervals(workspace) == [(0.5, 1.5), (4.0, 5.0)]
    workspace["audio"]["dubbed_enabled"] = False
    assert music_mix.voice_intervals(workspace) == []


def test_close_lines_merge_so_the_music_does_not_pump() -> None:
    merged = music_mix.merge_intervals([(1.0, 2.0), (2.5, 3.0), (6.0, 7.0)], gap=0.9)
    assert merged == [(1.0, 3.0), (6.0, 7.0)]


def _eval_expression(expression: str, t: float) -> float:
    py = expression.replace("clip(", "_clip(")
    return eval(py, {"_clip": lambda v, lo, hi: max(lo, min(hi, v)), "min": min, "t": t})  # noqa: S307


def test_ducking_expression_matches_the_reference_envelope() -> None:
    ducking = music_mix.normalize_ducking({"amount_db": -12, "attack": 0.4, "release": 0.8})
    intervals = [(2.0, 4.0), (4.5, 5.0), (9.0, 10.0)]
    expression = music_mix.ducking_expression(intervals, ducking, (0.0, 12.0))
    merged = music_mix.merge_intervals(intervals, 1.2)
    g = 10 ** (-12 / 20)
    for t in [0.0, 1.6, 1.8, 2.0, 3.0, 4.2, 5.0, 5.4, 5.8, 8.8, 9.5, 10.5, 11.0]:
        expected = 1 - (1 - g) * music_mix.duck_envelope(t, merged, 0.4, 0.8)
        assert math.isclose(_eval_expression(expression, t), expected, abs_tol=1e-3), t
    # At full depth the gain is exactly -12 dB.
    assert math.isclose(20 * math.log10(_eval_expression(expression, 3.0)), -12, abs_tol=0.01)
    # Only intervals near the clip window are included; disabled ducking yields nothing.
    assert music_mix.ducking_expression(intervals, ducking, (6.0, 8.0)) is None
    assert music_mix.ducking_expression(intervals, {**ducking, "enabled": False}, (0, 12)) is None


def test_filter_graph_trims_offsets_loops_fades_and_ducks(tmp_path) -> None:
    track = music_mix.normalize_track({"id": "t", "volume": 50, "clips": []}, 30)
    clip = music_mix.normalize_clip(
        {"id": "c", "asset_id": "a", "start": 2.5, "in": 4, "duration": 10, "volume": 80, "fade_in": 1, "fade_out": 2, "loop": True},
        30,
    )
    graph = music_mix.build_music_filter([(track, clip, tmp_path / "a.mp3")], [(5.0, 6.0)], 30.0)
    assert graph.startswith("[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[base]")
    chain = graph.split(";\n")[1]
    assert chain.startswith("[1:a]atrim=start=4.000:duration=10.000,asetpts=PTS-STARTPTS,")
    assert "volume=0.4000" in chain  # 80 % clip × 50 % track
    assert "afade=t=in:st=0:d=1.000" in chain and "afade=t=out:st=8.000:d=2.000" in chain
    assert "adelay=2500:all=1" in chain
    assert chain.index("adelay") < chain.index("eval=frame")  # ducking runs on timeline time
    assert graph.endswith("[base][m1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,atrim=0:30.000[aout]")


def test_with_music_wraps_every_audio_mapping() -> None:
    graph = renderer._audio_map_args(1, 2, {"original_volume": 30, "dubbed_volume": 100})
    wrapped = renderer._with_music(graph, 3)
    assert wrapped[0] == "-filter_complex" and wrapped[-2:] == ["-map", "[aout]"]
    assert wrapped[1].count("[aout]") == 1 and "[premix][3:a]amix=inputs=2" in wrapped[1]
    assert wrapped[1].endswith("alimiter=limit=0.95[aout]")
    voice_only = renderer._with_music(["-map", "1:a:0"], 2)
    assert voice_only[1].startswith("[1:a]anull[premix];[premix][2:a]amix")
    assert renderer._with_music(["-an"], 1)[1] == "[1:a]alimiter=limit=0.95[aout]"
    # No music: the original mapping, untouched (old workspaces render exactly as before).
    assert renderer._with_music(graph, None) == graph


# ---- library + real renders -----------------------------------------------------------------


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path)
    storage.ensure_storage()
    return tmp_path


def test_import_normalizes_and_computes_peaks(library) -> None:
    source = _tone(library / "song.wav", 3.0)
    asset = music_library.import_file(source, tags=["Lofi", "lofi", " Chill "], original_filename="Bài hát.wav")
    assert asset["title"] == "Bài hát" and asset["tags"] == ["lofi", "chill"]
    assert abs(asset["duration"] - 3.0) < 0.1 and asset["file_url"].endswith(".mp3")
    peaks = music_library.peaks(asset["id"])
    assert peaks["rate"] == 20 and 55 <= len(peaks["peaks"]) <= 62 and max(peaks["peaks"]) > 100
    assert music_library.list_assets("bai")["items"][0]["id"] == asset["id"]  # accent-insensitive
    assert music_library.list_assets(tag="chill")["items"] and not music_library.list_assets(tag="rock")["items"]
    music_library.delete_asset(asset["id"])
    assert music_library.asset_path(asset["id"]) is None


def test_music_render_ducks_under_the_voice(library) -> None:
    asset = music_library.import_file(_tone(library / "bed.wav", 8.0), original_filename="bed.wav")
    workspace = {
        "duration": 8.0,
        "audio": {"dubbed_enabled": True},
        "segments": [{"audio_url": "/storage/v.mp3", "audio_start": 3.0, "audio_duration": 2.0}],
        "audio_tracks": [
            {
                "clips": [
                    {"asset_id": asset["id"], "start": 0.0, "duration": 8.0, "ducking": {"amount_db": -12, "attack": 0.2, "release": 0.4}},
                ]
            }
        ],
    }
    out = music_mix.render_music_track(workspace, library / "music.flac", music_library.asset_path)
    assert out and out.exists()
    free = _rms_db(out, 1.0, 2.5)
    ducked = _rms_db(out, 3.2, 4.8)
    assert -13.0 <= ducked - free <= -11.0, (free, ducked)
    assert abs(_rms_db(out, 5.6, 7.5) - free) < 0.5  # back up after the release


def test_missing_asset_fails_the_render_clearly(library) -> None:
    workspace = {
        "duration": 5.0,
        "audio_tracks": [{"name": "Nền", "clips": [{"asset_id": "mus-gone", "title": "Epic", "duration": 2}]}],
    }
    with pytest.raises(music_mix.MusicAssetMissing, match="Epic"):
        music_mix.render_music_track(workspace, library / "m.flac", music_library.asset_path)
    # Muted tracks are not rendered, so they do not need their file.
    workspace["audio_tracks"][0]["muted"] = True
    assert music_mix.render_music_track(workspace, library / "m.flac", music_library.asset_path) is None


def test_old_workspaces_load_with_no_tracks_and_render_unchanged(job_storage, monkeypatch) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.build_workspace(job, config)
    workspace.pop("audio_tracks")  # as saved by an editor without audio tracks
    editor.save_workspace(workspace)
    assert editor.with_issues(editor.load_workspace(job))["audio_tracks"] == []
    calls = []
    monkeypatch.setattr(editor, "render_video", lambda *args, **kwargs: calls.append(kwargs) or Path("x.mp4"))
    monkeypatch.setattr(editor.tts, "_mix_timed_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(editor, "prepare_background_audio", lambda *args, **kwargs: None)
    editor.render_workspace(editor.load_workspace(job), config, render_quality="fast", burn_subtitles=False)
    assert "music_audio_path" not in calls[0]


def test_patch_and_undo_state_carry_audio_tracks(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    workspace = editor.build_workspace(job, config)
    workspace = editor.patch_workspace(
        workspace, None, None, audio_tracks=[{"id": "t1", "clips": [{"id": "c1", "asset_id": "a", "duration": 2}]}]
    )
    before = {key: workspace[key] for key in editor.EDITABLE_KEYS}
    workspace = editor.patch_workspace(workspace, None, None, audio_ops=[{"op": "move_clip", "id": "c1", "start": 3}])
    assert workspace["audio_tracks"][0]["clips"][0]["start"] == 3
    workspace = editor.replace_state(workspace, before)
    assert workspace["audio_tracks"][0]["clips"][0]["start"] == 0
    # Snapshots from older clients (no audio_tracks key) keep the current tracks.
    legacy = {key: value for key, value in before.items() if key != "audio_tracks"}
    assert editor.replace_state(workspace, legacy)["audio_tracks"][0]["id"] == "t1"


def test_full_editor_render_mixes_music_with_the_voice(job_storage) -> None:  # noqa: F811
    job, config = job_storage
    root = storage.STORAGE_ROOT
    for wav in sorted((root / "audio" / f"{job}.chunks").glob("*.wav")):
        _tone(wav, 1.0, freq=880, volume=0.5)  # real voice takes
    asset = music_library.import_file(_tone(root / "bed.wav", 6.0, freq=220), original_filename="bed.wav")
    workspace = editor.build_workspace(job, config)
    workspace = editor.patch_workspace(
        workspace,
        None,
        None,
        audio={"original_enabled": False},  # no source audio in the synthetic video
        audio_tracks=[
            {"clips": [
                {"asset_id": asset["id"], "start": 0.0, "duration": 3.0, "fade_in": 0.5},
                {"asset_id": asset["id"], "start": 3.0, "in": 1.0, "duration": 3.0, "volume": 60, "fade_out": 1.0},
            ]}
        ],
    )
    output = editor.render_workspace(workspace, config, render_quality="fast", burn_subtitles=False)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True,
    )
    assert "audio" in probe.stdout
    # Music is present where no voice plays (between takes, 1.5–1.9 s).
    assert _rms_db(output, 1.55, 1.85) > -40
    # The limiter keeps the sum from clipping.
    peak = subprocess.run(
        ["ffmpeg", "-i", str(output), "-af", "volumedetect", "-vn", "-f", "null", "-"], capture_output=True, text=True
    )
    assert float(re.search(r"max_volume: (-?[\d.]+) dB", peak.stderr).group(1)) <= 0.0
