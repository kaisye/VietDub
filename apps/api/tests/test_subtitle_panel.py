from __future__ import annotations

from app.services import renderer


def _ass(tmp_path, **style) -> str:
    srt = tmp_path / "cue.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nXin chào các bạn\n", encoding="utf-8")
    path = renderer._srt_to_ass(srt, renderer.normalize_subtitle_style(style), (1024, 576), 2)
    return path.read_text(encoding="utf-8-sig")


def test_classic_box_is_unchanged(tmp_path) -> None:
    ass = _ass(tmp_path, box_opacity=0.6, outline=2)

    assert ",4,2.0,0," in ass  # libass whole-event box, Outline = padding.
    assert "\\p1" not in ass


def test_panel_draws_rounded_box_behind_stroked_text(tmp_path) -> None:
    ass = _ass(tmp_path, box_style="panel", box_opacity=0.6, box_radius=0.5, outline=1.5, text_color="#FFFFFF")
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]

    assert ",1,1.5,0," in ass  # Text keeps a real outline.
    assert len(dialogues) == 2
    box, text = dialogues
    assert box.startswith("Dialogue: 0,") and "\\p1}m " in box and " b " in box
    assert "\\1a&H66&" in box  # 60 % opacity.
    assert text.startswith("Dialogue: 1,") and text.endswith("Xin chào các bạn")


def test_panel_box_is_centred_on_position(tmp_path) -> None:
    ass = _ass(tmp_path, box_style="panel", box_opacity=1, box_radius=0, position_y=0.5)
    box = next(line for line in ass.splitlines() if "\\p1" in line)
    points = [int(value) for value in box.split("}m ")[1].split("{")[0].replace("l ", "").split()]
    xs, ys = points[0::2], points[1::2]

    assert abs((min(xs) + max(xs)) / 2 - 512) <= 1
    assert abs((min(ys) + max(ys)) / 2 - 288) <= 1


def test_original_volume_regions_build_a_timed_gain() -> None:
    filter_ = renderer._background_audio_filter(
        1, {"original_volume": 20, "original_volume_regions": [{"start": 1, "end": 2.5, "volume": 300}]}
    )

    assert "volume='0.200+2.800*between(t,1.000,2.500)':eval=frame" in filter_
    assert "volume=0.200" in renderer._background_audio_filter(1, {"original_volume": 20})


def test_region_fades_ramp_the_gain() -> None:
    filter_ = renderer._background_audio_filter(
        1,
        {
            "original_volume": 20,
            "original_volume_regions": [{"start": 1, "end": 5, "volume": 300, "fade_in": 1, "fade_out": 2}],
        },
    )

    assert "+2.800*between(t,1.000,5.000)*clip(min((t-1.000)/1.000,(5.000-t)/2.000),0,1)" in filter_


def test_audio_map_follows_dub_and_source_switches() -> None:
    both = renderer._audio_map_args(1, 2, {"dubbed_volume": 100})
    assert both[-2:] == ["-map", "[aout]"] and "amix" in both[1]

    # Dub off: only the source bed, with its volume.
    source = renderer._audio_map_args(None, 1, {"original_volume": 150})
    assert source[-2:] == ["-map", "[aout]"] and source[1].startswith("[1:a]volume=")

    # Source off: the voice alone, still at the dub volume.
    voice = renderer._audio_map_args(1, None, {"dubbed_volume": 50, "source_audio": False})
    assert voice[1].startswith("[1:a]volume=0.500")

    assert renderer._audio_map_args(None, None, {"source_audio": False}) == ["-an"]
    assert renderer._audio_map_args(None, None, None) == ["-map", "0:a:0?"]
    assert renderer._audio_map_args(1, None, None) == ["-map", "1:a:0"]
