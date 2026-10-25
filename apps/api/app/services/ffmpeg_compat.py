from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def complex_filter_file_option(executable: str = "ffmpeg") -> str:
    """Return the filter-file option supported by the selected FFmpeg.

    FFmpeg 9 removed ``-filter_complex_script`` in favor of the generic
    file-backed option syntax. VietDub still bundles FFmpeg 6 on some targets,
    so select the spelling from the executable's advertised capabilities.
    """
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-h", "full"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        # Prefer the current syntax when capability detection is unavailable.
        return "-/filter_complex"

    help_text = f"{result.stdout}\n{result.stderr}"
    if "-filter_complex_script" in help_text:
        return "-filter_complex_script"
    return "-/filter_complex"


def complex_filter_file_args(filter_file: Path, executable: str = "ffmpeg") -> list[str]:
    return [complex_filter_file_option(executable), str(filter_file)]
