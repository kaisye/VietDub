# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Video Dubbing FastAPI backend.

Build from the repo root (so the `app` package is importable):

    python -m PyInstaller apps/api/videodubbing-api.spec --noconfirm

Produces a single `dist/videodubbing-api(.exe)` (one-file build) so it can be
wired into Tauri's `bundle.externalBin` as a single sidecar binary.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, get_module_file_attribute

datas = []
binaries = []
hiddenimports = []

# Our own package and its services (some are imported lazily inside functions).
hiddenimports += collect_submodules("app")

# Ship only runtime code/config with known provenance. Personal reference audio,
# transcripts, instructions and generated voice previews are intentionally not
# part of the distributable application.
# NOTE: data source paths are resolved relative to this .spec file's directory
# (apps/api/), NOT the build cwd. Keep them relative to apps/api/.
datas += [
    ("omnivoice_local_service.py", "."),
    ("app/assets/omnivoice_service.py", "app/assets"),
    ("app/assets/omnivoice-requirements.txt", "app/assets"),
    # The user's distributable voice setup: the default OmniVoice reference clip
    # (Voice_Ref.WAV + scripts/instruction) served
    # by /voice-options/{id}/preview. Bundled so the packaged app ships with the
    # configured default voice.
    ("app/assets/defaults", "app/assets/defaults"),
]

# uvicorn[standard] pulls these in only at runtime.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "websockets",
    "websockets.legacy",
    "httptools",
    "watchfiles",
]

# Third-party packages imported lazily (edge_tts, gradio_client, yt_dlp, riva,
# plus the hard-subtitle OCR stack). collect_all grabs submodules, data files
# and bundled binaries/protos. onnxruntime ships native .dll/.so providers and
# cv2 (opencv) ships native libs — both are imported lazily inside
# hardsub_ocr.py and must be collected explicitly or the frozen OCR path fails.
for pkg in (
    "edge_tts",
    "gradio_client",
    "yt_dlp",
    "riva",
    "dotenv",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "cv2",
    "wordninja",
    # ZeroTTS and its dynamic/native runtime dependencies. Model weights stay out
    # of the binary and are downloaded into app storage on first use.
    "zerotts",
    "tokenizers",
    "huggingface_hub",
    "soundfile",
    "scipy",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception as exc:  # package may be absent in a slim build
        print(f"[spec] skipping optional package {pkg!r}: {exc}")

# wordninja 2.0 installs as ``wordninja.py`` plus a sibling data directory.
# Because it is a module rather than a regular package, collect_all() does not
# reliably discover its frequency dictionary. The module opens this exact path
# during import, so missing it makes the whole OCR job fail in a one-file build.
wordninja_module = Path(get_module_file_attribute("wordninja"))
wordninja_words = wordninja_module.parent / "wordninja" / "wordninja_words.txt.gz"
if not wordninja_words.is_file():
    raise RuntimeError(f"Required wordninja dictionary was not found: {wordninja_words}")
datas.append((str(wordninja_words), "wordninja"))
hiddenimports.append("wordninja")


block_cipher = None

a = Analysis(
    ["run_server.py"],
    pathex=["apps/api"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy/optional ML libs are run out-of-process (OmniVoice local/Colab),
        # never imported by the API server itself.
        "torch",
        "torchaudio",
        "demucs",
        "pytest",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# One-file build: everything is packed into a single self-extracting executable.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="videodubbing-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
