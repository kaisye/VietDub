# Third-Party Notices

VietDub is built with open-source components. The complete dependency versions
are recorded in `package-lock.json`, `apps/api/requirements.txt` and
`apps/desktop/src-tauri/Cargo.lock`.

Notable runtime components include:

- Tauri, React and Rust ecosystem crates under their respective licenses.
- FastAPI, Uvicorn, SQLAlchemy, PyInstaller, yt-dlp and Edge TTS.
- FFmpeg binaries supplied by `ffmpeg-static` / `ffprobe-static`. The selected
  FFmpeg build includes GPL components such as libx264 and libmp3lame; distributors
  must satisfy the corresponding GPL source and notice obligations.
- RapidOCR/ONNX Runtime, downloaded Python dependencies governed by their own
  package licenses.
- Node.js, downloaded on demand from nodejs.org under the Node.js license.
- Python 3.12 standalone runtime, downloaded on demand from
  `astral-sh/python-build-standalone`, governed by the Python Software Foundation
  license and the notices included in that distribution.
- 9router, downloaded on demand from npm under the license published with the
  pinned package version.

Before public or commercial redistribution, review every dependency license and
replace this notice with the exact license texts/source offer required for the
specific release binaries.
