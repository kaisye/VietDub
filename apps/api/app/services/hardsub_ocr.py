"""Read burned-in (hard) subtitles from video frames into a source SRT.

Many downloaded videos (especially zh/vi content) ship their captions burned
into the picture rather than as a selectable subtitle stream, so neither the
sidecar/embedded path nor speech-to-text recovers the real text. This service
samples the subtitle region, OCRs the frames and reconstructs an SRT that the
rest of the pipeline consumes like any other source transcript.

Engine: RapidOCR (ONNXRuntime) — runs the PP-OCR detection/recognition models
on CPU with the models bundled in the wheel, so there is no paddlepaddle
dependency and no network download at runtime. Heavy imports (rapidocr, cv2,
numpy) are deferred so importing this module — and the segmentation/SRT logic —
stays dependency-light and unit-testable.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .storage import ensure_storage
from .subtitle import _format_srt_time, _normalize_text, _probe_duration

logger = logging.getLogger(__name__)


@dataclass
class Caption:
    text: str
    start: float
    end: float


def extract_hardsub_subtitle(
    video_path: Path,
    target_language: str = "EN",
    source_language: str | None = None,
    region: dict | None = None,
) -> Path | None:
    """OCR the burned-in subtitle region and return an SRT path, or None.

    ``region`` is the user-drawn box from the desktop Source step (center-based
    x/y/width/height fractions). When omitted, the crop falls back to a
    full-width bottom band (AETHER_OCR_REGION_Y/H) for backward compatibility.

    Returns None when no readable caption was found so the caller can surface a
    clear error (the OCR strategy is explicit, so we do not silently fall back).
    """
    interval = _env_float("AETHER_OCR_SAMPLE_INTERVAL", 0.5, 0.1, 5.0)
    crop = _crop_filter(*_resolve_crop_box(region))
    scale_width = max(320, _env_int("AETHER_OCR_SCALE_WIDTH", 1920))
    if scale_width % 2:
        scale_width += 1
    min_confidence = _env_float("AETHER_OCR_MIN_CONFIDENCE", 0.5, 0.0, 1.0)
    diff_threshold = _env_float("AETHER_OCR_DIFF_THRESHOLD", 8.0, 0.0, 255.0)
    similarity = _env_float("AETHER_OCR_MERGE_SIMILARITY", 0.9, 0.0, 1.0)
    min_caption = _env_float("AETHER_OCR_MIN_CAPTION_SECONDS", 0.3, 0.05, 5.0)
    max_seconds = _env_float("AETHER_OCR_MAX_SECONDS", 0.0, 0.0, 1e9)

    duration = _probe_duration(video_path)
    engine = _load_ocr_engine(source_language)

    observations: list[tuple[float, str]] = []
    with tempfile.TemporaryDirectory(prefix="aether_ocr_") as directory:
        frames = _extract_frames(
            video_path,
            Path(directory),
            interval=interval,
            crop=crop,
            scale_width=scale_width,
            max_seconds=max_seconds,
        )
        if not frames:
            logger.warning("Hard subtitle OCR found no frames to sample for %s.", video_path.name)
            return None

        last_signature = None
        ocr_calls = 0
        for image_path, timestamp in frames:
            signature = _frame_signature(image_path)
            if last_signature is not None and _signature_distance(signature, last_signature) < diff_threshold:
                # Subtitle region unchanged from the last OCR'd frame — the text
                # carries forward, so skip the (expensive) OCR call entirely.
                continue
            last_signature = signature
            ocr_calls += 1
            text = _combine_ocr_lines(_ocr_image(engine, image_path), min_confidence)
            observations.append((timestamp, text))

    captions = _build_captions(
        observations,
        max(duration, observations[-1][0] + min_caption) if observations else duration,
        min_caption_seconds=min_caption,
        similarity_threshold=similarity,
    )
    logger.info(
        "Hard subtitle OCR sampled %d frames, ran OCR on %d keyframes, produced %d caption(s) for %s.",
        len(frames),
        ocr_calls,
        len(captions),
        video_path.name,
    )
    if not captions:
        return None

    srt_text = _captions_to_srt(captions)
    path = ensure_storage() / "subtitles" / f"{video_path.stem}.{target_language.lower()}.ocr.srt"
    path.write_text(srt_text, encoding="utf-8")
    return path


# ── Deterministic segmentation / SRT (no heavy deps — unit tested directly) ───

def _build_captions(
    observations: list[tuple[float, str]],
    video_end: float,
    *,
    min_caption_seconds: float,
    similarity_threshold: float,
) -> list[Caption]:
    """Turn ordered keyframe (timestamp, text) observations into captions.

    Each observation's text holds until the next keyframe, so a caption spans
    ``[ts_i, ts_{i+1})``. Empty observations create gaps (subtitle disappeared),
    and consecutive similar observations are merged into one caption.
    """
    spans: list[list] = []
    count = len(observations)
    for index, (timestamp, text) in enumerate(observations):
        end = observations[index + 1][0] if index + 1 < count else max(video_end, timestamp + min_caption_seconds)
        if not text:
            continue
        if end <= timestamp:
            end = timestamp + min_caption_seconds
        spans.append([timestamp, end, text])

    merged: list[list] = []
    for start, end, text in spans:
        if merged:
            previous = merged[-1]
            duration = end - start
            is_similar = _similar(previous[2], text, similarity_threshold)
            # Short cues (<1 s) that are moderately similar to the previous are
            # almost certainly rolling-reveal OCR frames of the same on-screen
            # text — merge them using the longer (more complete) version.
            is_rolling_variant = duration < 1.0 and _similar(previous[2], text, 0.60)
            if (is_similar or is_rolling_variant) and start <= previous[1] + 0.05:
                previous[1] = max(previous[1], end)
                if len(text) > len(previous[2]):
                    previous[2] = text
                continue
        merged.append([start, end, text])

    return [
        Caption(text=text, start=start, end=end)
        for start, end, text in merged
        if end - start >= min_caption_seconds
    ]


def _captions_to_srt(captions: list[Caption]) -> str:
    blocks = [
        f"{index}\n{_format_srt_time(caption.start)} --> {_format_srt_time(caption.end)}\n{caption.text}"
        for index, caption in enumerate(captions, start=1)
    ]
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def _similar(left: str, right: str, threshold: float) -> bool:
    left_key = _comparison_key(left)
    right_key = _comparison_key(right)
    if not left_key or not right_key:
        return left_key == right_key
    if left_key == right_key:
        return True
    return difflib.SequenceMatcher(None, left_key, right_key).ratio() >= threshold


def _comparison_key(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _combine_ocr_lines(result: list, min_confidence: float) -> str:
    """Join OCR boxes into one caption, ordered top-to-bottom then left-to-right."""
    items: list[tuple[float, float, str]] = []
    for entry in result or []:
        try:
            box, text, score = entry[0], entry[1], entry[2]
        except (TypeError, IndexError, ValueError):
            continue
        if score is not None and float(score) < min_confidence:
            continue
        normalized = _normalize_text(str(text))
        if not normalized:
            continue
        points = list(box) if box else []
        center_y = sum(float(point[1]) for point in points) / len(points) if points else 0.0
        center_x = sum(float(point[0]) for point in points) / len(points) if points else 0.0
        items.append((center_y, center_x, normalized))
    # Bucket rows (~10px) so a wrapped 2-line caption keeps reading order.
    items.sort(key=lambda item: (round(item[0] / 10.0), item[1]))
    joined = _normalize_text(" ".join(text for _, _, text in items))
    return _fix_fused_words(joined)


def _fix_fused_words(text: str) -> str:
    """Recover word boundaries lost when PP-OCR recognition merges adjacent Latin words.

    When the detection model outputs a single box spanning multiple words, the
    recognition model sees no gap and emits them fused (e.g. "spacefight" instead
    of "space fight"). wordninja splits them using a frequency-based dictionary.

    Only applied to pure-Latin runs longer than 14 chars — CJK / Vietnamese text
    already has no spaces or uses diacritics that fall outside [a-zA-Z], so this
    path is safely skipped for those scripts.
    """
    try:
        import wordninja
    except ImportError:
        return text

    tokens = re.split(r"(\s+)", text)
    result = []
    for token in tokens:
        if not token or token.isspace():
            result.append(token)
            continue
        if len(token) > 14 and re.fullmatch(r"[a-zA-Z]+", token):
            parts = wordninja.split(token)
            # Filter single-char noise fragments; keep split only if we retain
            # most of the original characters and get at least 2 real words.
            long_parts = [p for p in parts if len(p) >= 2]
            coverage = sum(len(p) for p in long_parts) / len(token)
            if len(long_parts) >= 2 and coverage >= 0.75:
                result.append(" ".join(long_parts))
                continue
        result.append(token)
    return "".join(result)


# ── Frame extraction & change detection (deferred cv2/numpy imports) ──────────

def _resolve_crop_box(region: dict | None) -> tuple[float, float, float, float]:
    """Return ``(left, top, width, height)`` frame fractions for the OCR crop.

    ``region`` is center-based (``x``/``y`` are the box centre), matching the
    desktop picker and the ``hard_sub_blur_*`` convention; it is converted to a
    top-left origin here. With no region, fall back to a full-width bottom band.
    """
    if region:
        width = _clamp(float(region.get("width", 0.72)), 0.05, 1.0)
        height = _clamp(float(region.get("height", 0.18)), 0.03, 1.0)
        left = _clamp(float(region.get("x", 0.5)) - width / 2.0, 0.0, 1.0 - width)
        top = _clamp(float(region.get("y", 0.85)) - height / 2.0, 0.0, 1.0 - height)
        return left, top, width, height

    top = _env_float("AETHER_OCR_REGION_Y", 0.70, 0.0, 0.95)
    height = _env_float("AETHER_OCR_REGION_H", 0.30, 0.05, 1.0)
    if top + height > 1.0:
        height = round(1.0 - top, 4)
    return 0.0, top, 1.0, height


def _crop_filter(left: float, top: float, width: float, height: float) -> str:
    return f"crop=in_w*{width:.4f}:in_h*{height:.4f}:in_w*{left:.4f}:in_h*{top:.4f}"


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _extract_frames(
    video_path: Path,
    output_dir: Path,
    *,
    interval: float,
    crop: str,
    scale_width: int,
    max_seconds: float,
) -> list[tuple[Path, float]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fps = 1.0 / interval
    video_filter = f"fps={fps:.6f},{crop},scale={scale_width}:-2"
    pattern = output_dir / "frame_%06d.jpg"
    command = ["ffmpeg", "-y"]
    if max_seconds and max_seconds > 0:
        command += ["-t", f"{max_seconds:.3f}"]
    command += ["-i", str(video_path), "-vf", video_filter, "-q:v", "3", str(pattern)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=_ocr_ffmpeg_timeout())
    if result.returncode != 0:
        raise RuntimeError(f"Unable to extract OCR frames: {result.stderr[-500:] or video_path.name}")
    frames = sorted(output_dir.glob("frame_*.jpg"))
    # The fps filter emits one frame per `interval` seconds starting near t=0.
    return [(path, index * interval) for index, path in enumerate(frames)]


def _frame_signature(image_path: Path):
    import cv2

    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    return cv2.resize(image, (160, 32)).astype("float32")


def _signature_distance(left, right) -> float:
    import numpy as np

    if left is None or right is None:
        return 255.0
    return float(np.mean(np.abs(left - right)))


# ── OCR engine (deferred rapidocr import) ─────────────────────────────────────

def _load_ocr_engine(source_language: str | None):
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:  # pragma: no cover - exercised via friendly message
        raise RuntimeError(
            "Hard subtitle OCR needs the 'rapidocr-onnxruntime' package, which is not installed. "
            "Install it with `pip install -r apps/api/requirements.txt`, then retry the job."
        ) from exc

    kwargs: dict[str, str] = {}
    for env_name, kwarg in (
        ("AETHER_OCR_DET_MODEL", "det_model_path"),
        ("AETHER_OCR_REC_MODEL", "rec_model_path"),
        ("AETHER_OCR_CLS_MODEL", "cls_model_path"),
    ):
        value = os.getenv(env_name, "").strip()
        if value:
            kwargs[kwarg] = value
    if source_language:
        logger.info("Loading RapidOCR for source language '%s'.", source_language)
    return RapidOCR(**kwargs)


def _ocr_image(engine, image_path: Path) -> list:
    result, _elapsed = engine(str(image_path))
    return result or []


# ── Env helpers ───────────────────────────────────────────────────────────────

def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _ocr_ffmpeg_timeout() -> int:
    value = _env_int("AETHER_OCR_FFMPEG_TIMEOUT_SECONDS", 1800)
    return value if value > 0 else 1800
