"""Auto review end to end through the API: review, propose, apply (with a fake LLM + TTS), revert."""
from __future__ import annotations

import json
import subprocess
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import MediaJob, MediaJobStatus
from app.services import editor, storage
from app.services.auto_review import runner, vi_text

JOB = "job-review"
CUES = [
    # (start, end, text, natural take length, placed length)
    (0.0, 2.6, "Ông cho rằng Trung Quốc có nền tảng sản xuất vô cùng vững chắc và đáng tin.", 4.0, 2.6),
    (6.0, 7.5, "Câu tiếp theo.", 1.0, 1.0),
    (9.0, 9.4, "Ồ, thật à?", 1.0, 1.0),
    (12.0, 13.4, "Đây là một câu nói rất dài cần phải rút gọn lại cho vừa khung thời gian.", 4.2, 2.6),
    (13.4, 14.4, "Câu chen giữa.", 0.9, 0.9),
    (16.0, 16.2, "...", 0.3, 0.3),
]


def _wav(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * int(8000 * seconds))


def _time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path)
    root = storage.ensure_storage()
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:d=20", "-c:v", "libx264", str(root / "raw-videos" / f"{JOB}.mp4")],
        capture_output=True,
        check=True,
    )
    blocks = [f"{n}\n{_time(s)} --> {_time(e)}\n{text}" for n, (s, e, text, _, _) in enumerate(CUES, 1)]
    (root / "subtitles" / f"{JOB}.vi.render.display.srt").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    chunk_dir = root / "audio" / f"{JOB}.chunks"
    chunk_dir.mkdir()
    chunks = []
    for index, (start, _end, text, natural, placed) in enumerate(CUES, 1):
        audio = chunk_dir / f"{index:04d}_zerotts_raw.wav"
        _wav(audio, natural)
        chunks.append({"chunk": index, "start": start, "end": start + placed, "audio": str(audio), "text": text})
    (root / "audio" / f"{JOB}.aligned.chunks.json").write_text(json.dumps({"chunks": chunks}), encoding="utf-8")

    # Fake LLM: drop the filler. Fake TTS: a take of 0.22 s per syllable.
    def completer(system, user):
        items = json.loads(user.split("\n", 1)[1])["items"]
        return json.dumps({"lines": [{"id": item["id"], "text": item["current"].replace(" rất dài cần phải", "").replace(" lại", "")} for item in items]})

    monkeypatch.setattr(runner, "completers_factory", lambda: [("fake", completer)])

    def fake_regenerate(workspace, segment_id, **_settings):
        segment = next(item for item in workspace["segments"] if item["id"] == segment_id)
        take = editor.workspace_dir(workspace["job_id"]) / "audio"
        take.mkdir(exist_ok=True)
        path = take / f"{segment_id}-{int(time.time() * 1e6)}.wav"
        natural = round(vi_text.syllable_count(segment["text"]) * 0.22, 3)
        _wav(path, natural)
        segment.update(
            previous_audio_url=segment.get("audio_url"),
            previous_audio_duration=segment.get("audio_duration"),
            audio_url=editor._storage_url(path),
            audio_source_url=editor._storage_url(path),
            audio_source_duration=natural,
            audio_duration=natural,
            audio_text=segment["text"],
        )
        segment.pop("voice_slot", None)
        return editor.save_workspace(workspace)

    monkeypatch.setattr(editor, "regenerate_segment_audio", fake_regenerate)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with Session() as db:
        db.add(MediaJob(id=JOB, video_url="https://example.com/v", status=MediaJobStatus.ready, voice="zerotts_maichi", target_language="vi"))
        db.add(MediaJob(id="job-busy", video_url="https://example.com/w", status=MediaJobStatus.queued))
        db.commit()

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    from app.main import app

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _wait(http: TestClient, predicate, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        review = http.get(f"/jobs/{JOB}/auto-review").json()
        if predicate(review):
            return review
        time.sleep(0.05)
    raise AssertionError(f"timed out: {review.get('status')} {review.get('applying')}")


def test_review_propose_apply_and_revert(client) -> None:
    http = client
    assert http.get(f"/jobs/{JOB}/auto-review").json()["status"] == "idle"
    assert http.post("/jobs/job-busy/auto-review", json={"mode": "propose"}).status_code == 409

    started = http.post(f"/jobs/{JOB}/auto-review", json={"mode": "propose"})
    assert started.status_code == 200
    review = _wait(http, lambda r: r["status"] in {"done", "failed"})
    assert review["status"] == "done", review.get("error")
    before = review["summary_before"]
    assert before["by_type"]["voice_fast"] >= 2 and before["by_type"]["sub_empty"] == 1
    kinds = {proposal["kind"] for proposal in review["proposals"]}
    assert {"borrow_silence", "extend_subtitle", "delete", "rewrite"} <= kinds
    assert review["summary_predicted"]["score"] > before["score"]
    assert review["llm"]["provider"] == "fake"
    rewrite = next(proposal for proposal in review["proposals"] if proposal["kind"] == "rewrite")
    assert rewrite["ops"][0]["text"] == "Đây là một câu nói rút gọn cho vừa khung thời gian."
    assert rewrite["before"][0]["source_url"].endswith("0004_zerotts_raw.wav")  # For "Nghe thử".

    status = http.get(f"/jobs/{JOB}/auto-review/status").json()
    assert status["summary"]["pending"] == len(review["proposals"])
    badges = http.get("/jobs/auto-review/summaries", params={"ids": f"{JOB},nope"}).json()
    assert badges[JOB]["issues"] == before["issues"] and badges["nope"] == {"status": "none"}

    # Nothing changed yet: proposals wait for approval.
    workspace = http.get(f"/jobs/{JOB}/editor").json()
    assert workspace["segments"][0]["audio_duration"] == 2.6

    rejected_id = next(proposal["id"] for proposal in review["proposals"] if proposal["kind"] == "delete")
    assert http.post(f"/jobs/{JOB}/auto-review/reject", json={"ids": [rejected_id]}).status_code == 200

    applied = http.post(f"/jobs/{JOB}/auto-review/apply", json={"scope": "all"})
    assert applied.status_code == 200
    review = _wait(http, lambda r: (r.get("applying") or {}).get("status") in {"done", "failed"})
    assert review["applying"]["status"] == "done"
    statuses = {proposal["kind"]: proposal["status"] for proposal in review["proposals"]}
    assert statuses["borrow_silence"] == "applied" and statuses["rewrite"] == "applied"
    assert statuses["delete"] == "rejected"
    batch = review["batches"][-1]
    assert batch["score_after"] > batch["score_before"] and batch["cues_worse"] == 0

    workspace = http.get(f"/jobs/{JOB}/editor").json()
    first = workspace["segments"][0]
    assert first["audio_duration"] == pytest.approx(4.0, abs=0.01)  # Natural speed: borrowed the silence.
    assert first["end"] >= 4.0 - 0.01
    texts = [segment["text"] for segment in workspace["segments"]]
    assert "Đây là một câu nói rút gọn cho vừa khung thời gian." in texts
    assert "..." in texts  # Rejected.
    voices = sorted((s["audio_start"], s["audio_start"] + s["audio_duration"]) for s in workspace["segments"] if s.get("audio_url"))
    assert all(a_end <= b_start + 1e-6 for (_, a_end), (b_start, _) in zip(voices, voices[1:]))

    assert http.post(f"/jobs/{JOB}/auto-review/apply", json={"scope": "all"}).status_code == 409  # Nothing pending.

    reverted = http.post(f"/jobs/{JOB}/auto-review/batches/{batch['id']}/revert")
    assert reverted.status_code == 200
    body = reverted.json()
    assert body["workspace"]["segments"][0]["audio_duration"] == 2.6
    assert any(segment["text"].startswith("Đây là một câu nói rất dài") for segment in body["workspace"]["segments"])
    assert all(p["status"] in {"pending", "rejected"} for p in body["review"]["proposals"])
    assert http.post(f"/jobs/{JOB}/auto-review/batches/{batch['id']}/revert").status_code == 409


def test_safe_mode_applies_only_low_risk_fixes(client) -> None:
    http = client
    http.post(f"/jobs/{JOB}/auto-review", json={"mode": "safe"})
    review = _wait(http, lambda r: r["status"] in {"done", "failed"})
    assert review["status"] == "done", review.get("error")
    applied = [proposal for proposal in review["proposals"] if proposal["status"] == "applied"]
    assert applied and all(proposal["risk"] == "low" and not proposal["needs_tts"] for proposal in applied)
    assert any(proposal["kind"] == "rewrite" and proposal["status"] == "pending" for proposal in review["proposals"])
    assert review["batches"][-1]["auto"] is True


def test_report_mode_and_stale_proposals(client) -> None:
    http = client
    http.post(f"/jobs/{JOB}/auto-review", json={"mode": "report"})
    review = _wait(http, lambda r: r["status"] in {"done", "failed"})
    assert review["proposals"] == [] and review["issues"]

    http.post(f"/jobs/{JOB}/auto-review", json={"mode": "propose"})
    review = _wait(http, lambda r: r["status"] in {"done", "failed"})
    target = next(proposal for proposal in review["proposals"] if proposal["kind"] == "borrow_silence")
    # The user edits the cue in the editor meanwhile: the proposal must not clobber it.
    http.patch(f"/jobs/{JOB}/editor", json={"segments": [{"id": target["cue_ids"][0], "start": 0.1, "end": 2.6}]})
    http.post(f"/jobs/{JOB}/auto-review/apply", json={"scope": "selected", "ids": [target["id"]]})
    review = _wait(http, lambda r: (r.get("applying") or {}).get("status") in {"done", "failed"})
    assert next(p for p in review["proposals"] if p["id"] == target["id"])["status"] == "stale"


def test_pipeline_hook_never_fails_the_job(monkeypatch) -> None:
    from app import worker

    calls = []

    def boom(*args, **kwargs):
        calls.append(kwargs.get("trigger"))
        raise RuntimeError("no workspace")

    monkeypatch.setattr(runner, "start_review", boom)
    monkeypatch.setattr(worker, "editor_voice_settings", lambda job: {})
    monkeypatch.setattr(worker, "_persist_job_log", lambda job: None)

    class FakeDb:
        committed = 0

        def commit(self):
            self.committed += 1

        def rollback(self):
            pass

    job = MediaJob(id="j", video_url="x", status=MediaJobStatus.ready, logs="")
    db = FakeDb()
    worker._start_auto_review(db, job, None)
    assert calls == ["pipeline"] and "Auto review could not start" in job.logs and db.committed == 1
