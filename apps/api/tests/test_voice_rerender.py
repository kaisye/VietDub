from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.models import MediaJobStatus
from app.services.production_configuration import ProductionConfiguration
from app.services.voice_options import VoiceOption
from app.worker import _apply_voice_to_job, rerender_job_with_voice


def _job(**overrides):
    values = {
        "id": "job-123",
        "status": MediaJobStatus.ready,
        "voice": "old-voice",
        "voice_mode": "",
        "voice_instruction": "",
        "voice_reference_audio_url": "",
        "voice_reference_text": "",
        "speaker_voice_map": "{}",
        "configuration_snapshot_json": None,
        "runtime_snapshot_json": None,
        "output_url": "/storage/rendered-outputs/old.mp4",
        "error_message": None,
        "logs": "",
        "started_at": None,
        "completed_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_apply_voice_updates_snapshot_and_all_speaker_assignments():
    configuration = ProductionConfiguration().model_dump(mode="json")
    configuration["speakers"]["enabled"] = True
    configuration["speakers"]["voice_map"] = {
        "SPEAKER_00": {"voice_id": "old-voice", "rate": 0},
        "SPEAKER_01": {"voice_id": "other-old-voice", "rate": 5},
    }
    job = _job(
        configuration_snapshot_json=json.dumps(configuration),
        speaker_voice_map=json.dumps(configuration["speakers"]["voice_map"]),
    )
    voice = VoiceOption(
        id="zerotts_test",
        name="Test Voice",
        locale="vi-VN",
        language="Vietnamese",
        type="ZeroTTS",
        engine="zerotts",
    )

    _apply_voice_to_job(job, voice)

    snapshot = json.loads(job.configuration_snapshot_json)
    assert job.voice == voice.id
    assert snapshot["voice"]["voice_id"] == voice.id
    assert snapshot["voice"]["provider"] == "zerotts"
    assert {item["voice_id"] for item in snapshot["speakers"]["voice_map"].values()} == {voice.id}
    assert {item["voice_id"] for item in json.loads(job.speaker_voice_map).values()} == {voice.id}


def test_rerender_starts_from_translated_checkpoint_with_new_output(monkeypatch):
    raw_video = Path("/tmp/job-123.mp4")
    translated = Path("/tmp/job-123.vi.render.srt")
    started = {}
    monkeypatch.setattr(
        "app.worker._retry_checkpoint",
        lambda _job_id: {
            "kind": "translated",
            "raw_video_path": raw_video,
            "subtitle_path": translated,
        },
    )
    monkeypatch.setattr(
        "app.worker._start_translation_resume",
        lambda job_id, raw, subtitle, output_filename=None: started.update(
            job_id=job_id,
            raw=raw,
            subtitle=subtitle,
            output_filename=output_filename,
        ),
    )

    class FakeDb:
        def commit(self):
            pass

        def refresh(self, _job):
            pass

    job = _job()
    voice = VoiceOption("new-voice", "New Voice", "vi-VN", "Vietnamese", "Narration", engine="edge")

    rerender_job_with_voice(FakeDb(), job, voice)

    assert job.status == MediaJobStatus.tts_generating
    assert job.progress == 70
    assert job.voice == voice.id
    assert job.output_url is None
    assert started["raw"] == raw_video
    assert started["subtitle"] == translated
    assert started["output_filename"].startswith("job-123-voice-")
    assert started["output_filename"].endswith(".mp4")
