import subprocess

import pytest

from app.services import renderer, watermark


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(watermark, "ensure_storage", lambda: tmp_path)
    return tmp_path


def test_off_by_default_and_uses_the_bundled_vietnote_logo(storage):
    assert watermark.load_settings()["enabled"] is False
    assert watermark.active() is None
    assert watermark.image_path() == watermark.DEFAULT_LOGO
    assert watermark.DEFAULT_LOGO.exists()


def test_settings_are_clamped_and_a_job_can_opt_out(storage):
    saved = watermark.save_settings({"enabled": True, "position": "nowhere", "size": 9, "opacity": 0})
    assert saved["position"] == "top_right" and saved["size"] == 0.5 and saved["opacity"] == 0.1
    assert watermark.active() is not None
    assert watermark.active(enabled_for_job=False) is None


def test_uploaded_logo_replaces_and_reset_removes_it(storage):
    with pytest.raises(ValueError):
        watermark.store_image("logo.gif", b"x")
    first = watermark.store_image("Logo.PNG", b"png")
    assert watermark.image_path().name == first["image"]
    second = watermark.store_image("b.png", b"png2")
    assert not (storage / "watermark" / first["image"]).exists()
    watermark.save_settings({"image": None})
    assert not (storage / "watermark" / second["image"]).exists()
    assert watermark.image_path() == watermark.DEFAULT_LOGO


def test_overlay_wraps_the_whole_chain_and_sits_in_the_corner():
    settings = watermark.normalize({"enabled": True, "position": "bottom_left", "size": 0.1, "margin": 0.05})
    graph = watermark.overlay_graph("split[a][b];[b]crop=10:10:0:0[c];[a][c]overlay=0:0,ass='x.ass'", settings, (1920, 1080))
    assert graph.startswith("[in]split[a][b];")
    assert "ass='x.ass'[base];movie='" in graph
    assert "scale=172:-1" in graph  # 0.1 × 1080 × 1.6, even
    assert graph.endswith("[base][wm]overlay=54:H-h-54:format=auto[out]")
    assert watermark.overlay_graph("", settings, (640, 360)).startswith("[in]null[base];")


def test_ffmpeg_accepts_the_graph(tmp_path, storage):
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=1", str(video)], check=True
    )
    settings = watermark.normalize({"enabled": True})
    graph = renderer._video_filter(video, None, None, "source", watermark_settings=settings)
    out = tmp_path / "frame.png"
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", graph, "-frames:v", "1", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert out.stat().st_size > 0
