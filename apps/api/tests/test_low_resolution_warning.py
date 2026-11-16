"""Worker warns when a URL download comes back below 720p (YouTube's signed-out 360p cap)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app import worker
from app.models import MediaJob


def _video(path: Path, size: str) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={size}:d=1", str(path)], check=True)
    return path


def test_low_resolution_download_logs_the_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(worker, "_persist_job_log", lambda job: None)
    small = _video(tmp_path / "small.mp4", "640x360")
    sharp = _video(tmp_path / "sharp.mp4", "1280x720")

    job = MediaJob(id="j1", video_url="https://www.youtube.com/watch?v=x", logs="")
    worker._warn_if_low_resolution(job, small)
    assert f"{worker.LOW_RESOLUTION_MARKER} 640x360" in job.logs

    for url, video in (("https://www.youtube.com/watch?v=x", sharp), ("/storage/uploads/a.mp4", small)):
        job = MediaJob(id="j2", video_url=url, logs="")
        worker._warn_if_low_resolution(job, video)
        assert worker.LOW_RESOLUTION_MARKER not in (job.logs or "")


def test_subtitles_burn_on_an_upscaled_canvas_for_small_sources(tmp_path) -> None:
    from app.services import renderer

    assert renderer._subtitle_canvas((640, 360)) == (1920, 1080)
    assert renderer._subtitle_canvas((202, 360)) == (1080, 1924)  # Portrait: the width is the short side.
    assert renderer._subtitle_canvas((1920, 1080)) == (1920, 1080)

    small = _video(tmp_path / "small.mp4", "640x360")
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
    graph = renderer._video_filter(small, srt, None, "source")
    assert graph.startswith("scale=1920:1080:flags=lanczos,") and "ass=" in graph
    assert "scale=" not in renderer._video_filter(small, None, None, "source")
