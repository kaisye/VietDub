import json
import os

from app.services import runtime_settings


def test_saved_api_key_can_be_cleared(monkeypatch, tmp_path):
    settings_path = tmp_path / "runtime-settings.json"
    settings_path.write_text(
        json.dumps({"nvidia_api_key": "bad-nvidia-key", "groq_api_key": "good-groq-key"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AETHER_RUNTIME_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("NVIDIA_API_KEY", "bad-nvidia-key")
    monkeypatch.setenv("GROQ_API_KEY", "good-groq-key")

    updated = runtime_settings.update_runtime_settings({"nvidia_api_key": ""})

    assert updated.nvidia_api_key == ""
    assert updated.groq_api_key == "good-groq-key"
    assert "NVIDIA_API_KEY" not in os.environ
    assert os.environ["GROQ_API_KEY"] == "good-groq-key"
