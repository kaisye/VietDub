from __future__ import annotations

import sys
import types

from app.services import hardsub_ocr


def _capture_rapidocr_kwargs(monkeypatch) -> dict:
    captured: dict = {}

    class FakeRapidOCR:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules, "rapidocr_onnxruntime", types.SimpleNamespace(RapidOCR=FakeRapidOCR)
    )
    return captured


def test_ocr_engine_caps_detection_upscaling_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AETHER_OCR_DET_LIMIT_SIDE_LEN", raising=False)
    captured = _capture_rapidocr_kwargs(monkeypatch)

    hardsub_ocr._load_ocr_engine(None)

    assert captured["det_limit_type"] == "min"
    assert captured["det_limit_side_len"] == 480


def test_ocr_engine_detection_size_is_env_overridable(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_OCR_DET_LIMIT_SIDE_LEN", "736")
    captured = _capture_rapidocr_kwargs(monkeypatch)

    hardsub_ocr._load_ocr_engine(None)

    assert captured["det_limit_side_len"] == 736
