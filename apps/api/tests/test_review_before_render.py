from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import worker
from app.models import MediaJobStatus
from app.services.auto_review import runner
from app.services.production_configuration import ProductionConfiguration


def _proposal(pid: str, kind: str, risk: str, **extra) -> dict:
    return {"id": pid, "kind": kind, "risk": risk, "status": "pending", **extra}


def test_pre_render_fixes_are_low_risk_or_merges_never_rewrites() -> None:
    proposals = [
        _proposal("p1", "borrow_silence", "low"),
        _proposal("p2", "merge", "medium", needs_tts=True),
        _proposal("p3", "rewrite", "medium", needs_tts=True, needs_llm=True),
        _proposal("p4", "generate_voice", "medium", needs_tts=True),
        _proposal("p5", "extend_subtitle", "low", depends_on=["p3"]),
        {**_proposal("p6", "speed", "low"), "status": "applied"},
    ]
    assert [p["id"] for p in proposals if runner._pre_render_fixable(p, proposals)] == ["p1", "p2"]


class _FakeDB:
    def commit(self) -> None:
        pass


@pytest.fixture
def voiced_pipeline(monkeypatch, tmp_path: Path):
    calls: dict[str, object] = {"render_video": 0, "render_workspace": None, "post_review": 0}
    audio = tmp_path / "job.aligned.mp3"
    audio.write_bytes(b"")

    def fake_render_video(*args, **kwargs):
        calls["render_video"] = int(calls["render_video"]) + 1
        return tmp_path / "plain.mp4"

    def fake_render_workspace(workspace, configuration, **kwargs):
        calls["render_workspace"] = workspace
        return tmp_path / "reviewed.mp4"

    monkeypatch.setattr(worker, "_set_step", lambda *args: None)
    monkeypatch.setattr(worker, "_persist_job_log", lambda job: None)
    monkeypatch.setattr(worker, "_raise_if_cancelled", lambda job_id: None)
    monkeypatch.setattr(worker, "clear_progress", lambda job_id: None)
    monkeypatch.setattr(worker, "tts_provider_override", lambda provider: contextlib.nullcontext())
    monkeypatch.setattr(worker, "generate_tts", lambda *args, **kwargs: audio)
    monkeypatch.setattr(worker, "_log_tts_chunk_manifest", lambda job, path: None)
    monkeypatch.setattr(
        worker, "prepare_background_audio", lambda *args, **kwargs: SimpleNamespace(path=None, message="bed")
    )
    monkeypatch.setattr(worker, "render_video", fake_render_video)
    monkeypatch.setattr(worker.editor, "render_workspace", fake_render_workspace)
    monkeypatch.setattr(
        worker, "_start_auto_review", lambda *args: calls.__setitem__("post_review", int(calls["post_review"]) + 1)
    )
    return calls, tmp_path


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        id="job-review", logs="", status=MediaJobStatus.translating, progress=0, current_step="",
        output_url=None, error_message=None, completed_at=None, updated_at=None, burn_subtitles=True,
        voice="zerotts_maichi", target_language="VI", render_quality="balanced", render_mode="auto",
        speaker_diarization_enabled=False, speaker_voice_map=None, source_title="", source_language="en",
    )


def _configuration() -> ProductionConfiguration:
    configuration = ProductionConfiguration()
    configuration.voice.voice_id = "zerotts_maichi"
    configuration.subtitle.style_id = worker.NO_SUBTITLE_STYLE_ID
    return configuration


def test_reviewed_workspace_is_what_gets_rendered(voiced_pipeline, monkeypatch) -> None:
    calls, tmp_path = voiced_pipeline
    workspace = {"job_id": "job-review", "segments": []}
    monkeypatch.setattr(worker, "_review_before_render", lambda db, job, configuration: workspace)
    job = _job()
    worker._continue_after_translation(_FakeDB(), job, tmp_path / "raw.mp4", tmp_path / "job.vi.render.srt", None, _configuration())
    assert calls["render_workspace"] is workspace
    assert calls["render_video"] == 0
    assert calls["post_review"] == 0  # Already reviewed; no second review after the render.
    assert job.output_url == "/storage/rendered-outputs/reviewed.mp4"


def test_without_a_review_the_pipeline_renders_as_before(voiced_pipeline, monkeypatch) -> None:
    calls, tmp_path = voiced_pipeline
    monkeypatch.setattr(worker, "_review_before_render", lambda db, job, configuration: None)
    job = _job()
    worker._continue_after_translation(_FakeDB(), job, tmp_path / "raw.mp4", tmp_path / "job.vi.render.srt", None, _configuration())
    assert calls["render_video"] == 1
    assert calls["render_workspace"] is None
    assert calls["post_review"] == 1


def test_review_failure_never_fails_the_job(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(worker, "_set_step", lambda *args: None)
    monkeypatch.setattr(worker, "_persist_job_log", lambda job: None)
    monkeypatch.setattr(worker.editor, "load_workspace", lambda job_id: None)
    monkeypatch.setattr(worker.editor, "build_workspace", boom)
    job = _job()
    assert worker._review_before_render(_FakeDB(), job, _configuration()) is None
    assert "Auto review before render skipped: planner exploded" in job.logs


def test_existing_editor_edits_are_never_overwritten(monkeypatch) -> None:
    monkeypatch.setattr(worker.editor, "load_workspace", lambda job_id: {"job_id": job_id})
    monkeypatch.setattr(worker.editor, "build_workspace", lambda *args: pytest.fail("rebuilt the workspace"))
    assert worker._review_before_render(_FakeDB(), _job(), _configuration()) is None


def test_review_off_skips_it(monkeypatch) -> None:
    configuration = _configuration()
    configuration.workflow.auto_review = "off"
    monkeypatch.setattr(worker.editor, "load_workspace", lambda job_id: pytest.fail("looked for a workspace"))
    assert worker._review_before_render(_FakeDB(), _job(), configuration) is None
