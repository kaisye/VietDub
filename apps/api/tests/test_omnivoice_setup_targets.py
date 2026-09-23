"""Tests for OmniVoice local-setup hardware targeting.

PyTorch's cu128 wheels ship SASS for sm_75 and up only; the cu126 wheels still
cover sm_50..sm_90. Picking the wrong index is silent — torch installs fine and
``torch.cuda.is_available()`` stays True — so the mistake only shows up as
"no kernel image is available for execution on the device" at the first
synthesis, after a ~2.5 GB download. These tests pin the mapping.
"""

from unittest.mock import patch

import pytest

from app.services.omnivoice_local_setup import (
    MANAGED_PYTHON_TARGETS,
    _compute_capability,
    _driver_version,
    _managed_python_archive_name,
    _managed_python_path,
    _managed_python_url,
    _install_torch,
    _select_torch_target,
    _torch_install_command,
)
from app.services.runtime_hardware import _parse_compute_capability


def _select_with(capabilities, driver="580.00"):
    gpus = [
        {"compute_capability": value, "driver_version": driver}
        for value in capabilities
    ]
    with patch("app.services.runtime_hardware._detect_nvidia_gpus", return_value=gpus):
        with patch("platform.system", return_value="Windows"):
            return _select_torch_target()


@pytest.mark.parametrize(
    "capabilities, expected",
    [
        (["12.0"], "cu128"),   # Blackwell
        (["8.9"], "cu128"),    # Ada
        (["7.5"], "cu128"),    # Turing — the cu128 floor
        (["7.0"], "cu126"),    # Volta — just below it
        (["6.1"], "cu126"),    # Pascal, GTX 10xx
        (["5.0"], "cu126"),    # Maxwell, GTX 950M
        (["3.5"], "cpu"),      # Kepler — below every current wheel
        (["5.0", "8.6"], "cu128"),  # mixed rig: target the card torch will pick
    ],
)
def test_torch_index_follows_compute_capability(capabilities, expected):
    assert _select_with(capabilities) == expected


def test_unknown_capability_takes_the_wide_wheel():
    # Only older driver branches fail to report compute_cap, and those sit on
    # cards that may well be pre-Turing. cu126 spans sm_50..sm_90, so it is the
    # safe pick for a card we could not measure; cu128 would exclude everything
    # below sm_75 and buy nothing.
    assert _select_with([None]) == "cu126"


@pytest.mark.parametrize(
    "driver, expected",
    [
        ("580.00", "cu128"),  # current branch
        ("527.41", "cu128"),  # exactly the CUDA 12 floor on Windows
        ("527.40", "cu118"),  # one build below it
        ("452.39", "cu118"),  # exactly the CUDA 11.8 floor
        ("452.06", "cpu"),    # below every CUDA wheel we ship
    ],
)
def test_old_drivers_never_get_a_cuda_12_wheel(driver, expected):
    # A CUDA 12 runtime cannot initialise on a pre-r525 driver no matter what
    # the card supports: torch installs, then torch.cuda.is_available() is
    # False -- after a ~2.5 GB download that told the user nothing.
    assert _select_with(["8.9"], driver=driver) == expected


def test_driver_version_parsing():
    assert _driver_version({"driver_version": "527.41"}) == (527, 41)
    # Linux reports three components; the third must not break the compare.
    assert _driver_version({"driver_version": "525.60.13"}) == (525, 60)
    assert _driver_version({"driver_version": "570"}) == (570, 0)
    assert _driver_version({"driver_version": "Metal"}) is None
    assert _driver_version({}) is None


def test_no_gpu_falls_back_to_cpu():
    assert _select_with([]) == "cpu"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5.0", "5.0"),
        (" 8.9 ", "8.9"),
        ("", None),
        ("N/A", None),
        ("[Not Supported]", None),
        ("garbage", None),
    ],
)
def test_compute_capability_parsing(raw, expected):
    assert _parse_compute_capability(raw) == expected


def test_compute_capability_tuple_conversion():
    assert _compute_capability({"compute_capability": "5.0"}) == (5, 0)
    assert _compute_capability({"compute_capability": None}) is None
    assert _compute_capability({}) is None


def test_windows_has_a_managed_python_build():
    # Without this a Windows box with no Python hits a dead end: the setup used
    # to tell the user to go install Python by hand and stop there.
    target = MANAGED_PYTHON_TARGETS["windows-x86_64"]
    assert target.relative_exe == ("python", "python.exe")
    assert _managed_python_path("windows-x86_64").name == "python.exe"
    assert _managed_python_archive_name(target).endswith(
        "-x86_64-pc-windows-msvc-install_only.tar.gz"
    )
    assert _managed_python_url(target).startswith("https://github.com/astral-sh/")


def test_macos_managed_python_layout_is_unchanged():
    target = MANAGED_PYTHON_TARGETS["macos-arm64"]
    assert target.relative_exe == ("python", "bin", "python3")
    assert _managed_python_path("macos-arm64").parent.name == "bin"


def test_cuda_install_failure_falls_back_to_cpu(monkeypatch, tmp_path):
    calls = []

    def fake_run(command):
        calls.append(command)
        if "cu118" in command[-1]:
            raise RuntimeError("CUDA wheel download failed")

    monkeypatch.setattr("app.services.omnivoice_local_setup._run", fake_run)
    monkeypatch.setattr("app.services.omnivoice_local_setup._log", lambda _text: None)
    monkeypatch.setattr("app.services.omnivoice_local_setup._set_state", lambda *_args: None)

    selected = _install_torch(tmp_path / "python.exe", "cu118")

    assert selected == "cpu"
    assert calls[0][-1].endswith("/cu118")
    assert calls[1][-1].endswith("/cpu")
    assert "--force-reinstall" in calls[1]


def test_torch_install_command_uses_plain_url_and_resilient_download_flags(tmp_path):
    command = _torch_install_command(tmp_path / "python.exe", "cu118")

    assert command[-1] == "https://download.pytorch.org/whl/cu118"
    assert "[" not in command[-1]
    assert command[command.index("--retries") + 1] == "8"
    assert command[command.index("--timeout") + 1] == "120"
