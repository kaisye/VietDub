from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import worker
from app.models import MediaJobStatus
from app.services.production_configuration import ProductionConfiguration


class _FakeDB:
    def commit(self) -> None:
        pass


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        id="job-render-later",
        logs="",
        status=MediaJobStatus.translating,
        progress=0,
        current_step="",
        output_url=None,
        error_message=None,
        completed_at=None,
        updated_at=None,
        burn_subtitles=True,
        voice="none",
        target_language="VI",
        render_quality="balanced",
        render_mode="auto",
    )


def _configuration(auto_render: bool) -> ProductionConfiguration:
    configuration = ProductionConfiguration()
    configuration.voice.voice_id = worker.NO_VOICE_ID
    configuration.subtitle.style_id = worker.NO_SUBTITLE_STYLE_ID
    configuration.workflow.auto_render = auto_render
    return configuration


@pytest.fixture
def pipeline(monkeypatch, tmp_path: Path):
    calls: dict[str, int] = {"render": 0, "review": 0}

    def fake_render(*args, **kwargs):
        calls["render"] += 1
        output = tmp_path / "out.mp4"
        output.write_bytes(b"")
        return output

    monkeypatch.setattr(worker, "_set_step", lambda db, job, status, progress, step: None)
    monkeypatch.setattr(worker, "_persist_job_log", lambda job: None)
    monkeypatch.setattr(worker, "_raise_if_cancelled", lambda job_id: None)
    monkeypatch.setattr(worker, "clear_progress", lambda job_id: None)
    monkeypatch.setattr(
        worker,
        "prepare_background_audio",
        lambda *args, **kwargs: SimpleNamespace(path=None, message="bed"),
    )
    monkeypatch.setattr(worker, "render_video", fake_render)
    monkeypatch.setattr(
        worker, "_start_auto_review", lambda db, job, configuration: calls.__setitem__("review", calls["review"] + 1)
    )
    return calls, tmp_path


def test_render_after_editing_stops_before_render(pipeline) -> None:
    calls, tmp_path = pipeline
    job = _job()
    worker._continue_after_translation(
        _FakeDB(), job, tmp_path / "raw.mp4", tmp_path / "vi.srt", None, _configuration(False)
    )
    assert calls["render"] == 0
    assert job.status == MediaJobStatus.ready
    assert job.output_url is None
    assert job.progress == 100
    assert "Skipped rendering" in job.logs
    # Auto review only needs the editor workspace, so it still runs.
    assert calls["review"] == 1


def test_default_configuration_still_renders(pipeline) -> None:
    calls, tmp_path = pipeline
    job = _job()
    worker._continue_after_translation(
        _FakeDB(), job, tmp_path / "raw.mp4", tmp_path / "vi.srt", None, _configuration(True)
    )
    assert calls["render"] == 1
    assert job.output_url == "/storage/rendered-outputs/out.mp4"
