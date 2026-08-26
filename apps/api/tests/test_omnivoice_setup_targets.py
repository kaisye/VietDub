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
    _managed_python_archive_name,
    _managed_python_path,
    _managed_python_url,
    _select_torch_target,
)
from app.services.runtime_hardware import _parse_compute_capability


def _select_with(capabilities):
    gpus = [{"compute_capability": value} for value in capabilities]
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


def test_unknown_capability_keeps_the_modern_wheel():
    # Drivers too old to report compute_cap must not drag every card down to
    # cu126; _verify catches a genuine mismatch before the venv is marked ready.
    assert _select_with([None]) == "cu128"


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
