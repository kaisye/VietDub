import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import omnivoice_local_runtime, tts


def test_venv_python_symlink_is_not_resolved(monkeypatch, tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv-omnivoice" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    monkeypatch.setattr(omnivoice_local_runtime, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(omnivoice_local_runtime.os, "name", "posix")
    monkeypatch.delenv("AETHER_OMNIVOICE_PYTHON", raising=False)
    checked: list[Path] = []

    def fake_validate(path: Path, runtime: str, device: str) -> str | None:
        checked.append(path)
        return None

    monkeypatch.setattr(omnivoice_local_runtime, "_validate_python", fake_validate)

    found, error = omnivoice_local_runtime._find_omnivoice_python("auto", "auto")

    # The venv path itself must be used; its symlink target has no venv packages.
    assert found == venv_python
    assert error == ""
    assert checked == [venv_python]


def _local_settings() -> SimpleNamespace:
    return SimpleNamespace(omnivoice_runtime="auto", omnivoice_api_url="http://127.0.0.1:8008")


def test_local_omnivoice_is_started_on_demand(monkeypatch) -> None:
    health = iter([False, False, True])
    started: list[bool] = []
    monkeypatch.setattr(tts, "_omnivoice_health_ready", lambda _url: next(health))
    monkeypatch.setattr(tts, "report_progress", lambda _msg: None)
    monkeypatch.setattr(tts.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        omnivoice_local_runtime,
        "start_local_omnivoice",
        lambda settings: started.append(True) or {"state": "starting"},
    )
    monkeypatch.setattr(omnivoice_local_runtime, "local_omnivoice_status", lambda settings: {"state": "loading"})

    assert tts._resolve_omnivoice_base_url(_local_settings()) == "http://127.0.0.1:8008"
    assert started == [True]


def test_local_omnivoice_setup_required_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(tts, "_omnivoice_health_ready", lambda _url: False)
    monkeypatch.setattr(tts, "report_progress", lambda _msg: None)
    monkeypatch.setattr(
        omnivoice_local_runtime,
        "start_local_omnivoice",
        lambda settings: {"state": "setup_required", "error": "no torch"},
    )

    with pytest.raises(RuntimeError, match="chưa được cài đặt"):
        tts._resolve_omnivoice_base_url(_local_settings())


def test_launcher_auto_prefers_mps_without_cuda(monkeypatch) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import omnivoice_local_service as launcher

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert launcher._resolve_device("auto", "auto")[0] == "mps"
    assert launcher._resolve_device("mps", "mps")[0] == "mps"
    assert launcher._resolve_device("cpu", "auto")[0] == "cpu"
