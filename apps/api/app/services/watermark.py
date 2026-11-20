"""A logo stamped on every rendered video, set once in Settings.

The look (image, corner, size, opacity) lives in storage/watermark.json; a custom
logo is copied to storage/watermark/. Without a custom logo the bundled VietNote
mark is used. A job can still opt out through output.watermark = false.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .storage import ensure_storage

DEFAULT_LOGO = Path(__file__).resolve().parent.parent / "assets" / "defaults" / "vietnote-watermark.png"
POSITIONS = ("top_left", "top_right", "bottom_left", "bottom_right")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "position": "top_right",
    # Logo width as a fraction of the frame's short side × 1.6 (≈ 13% of a 16:9 width).
    "size": 0.14,
    "opacity": 0.85,
    # Gap to the frame edges, as a fraction of the short side.
    "margin": 0.035,
    # File name under storage/watermark/, or None for the bundled VietNote logo.
    "image": None,
}


def _settings_path() -> Path:
    return ensure_storage() / "watermark.json"


def _folder() -> Path:
    folder = ensure_storage() / "watermark"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return round(max(low, min(high, float(value))), 3)
    except (TypeError, ValueError):
        return fallback


def normalize(settings: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULTS, **{key: value for key, value in (settings or {}).items() if key in DEFAULTS}}
    merged["enabled"] = bool(merged["enabled"])
    merged["position"] = merged["position"] if merged["position"] in POSITIONS else DEFAULTS["position"]
    merged["size"] = _clamp(merged["size"], 0.04, 0.5, DEFAULTS["size"])
    merged["opacity"] = _clamp(merged["opacity"], 0.1, 1.0, DEFAULTS["opacity"])
    merged["margin"] = _clamp(merged["margin"], 0.0, 0.2, DEFAULTS["margin"])
    image = merged["image"]
    merged["image"] = Path(image).name if isinstance(image, str) and image.strip() else None
    return merged


def load_settings() -> dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    settings = normalize(data if isinstance(data, dict) else {})
    if settings["image"] and not (_folder() / settings["image"]).exists():
        settings["image"] = None
    return settings


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    merged = normalize({**current, **settings})
    if merged["image"] and not (_folder() / merged["image"]).exists():
        merged["image"] = None
    if current["image"] and current["image"] != merged["image"]:
        (_folder() / current["image"]).unlink(missing_ok=True)
    _settings_path().write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    return merged


def store_image(filename: str, data: bytes) -> dict[str, Any]:
    """Save an uploaded logo and make it the watermark image."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("Logo phải là ảnh PNG, JPG hoặc WEBP (nên dùng PNG nền trong suốt).")
    if not data:
        raise ValueError("File logo trống.")
    name = f"logo-{uuid.uuid4().hex[:10]}{suffix}"
    (_folder() / name).write_bytes(data)
    return save_settings({"image": name})


def image_path(settings: dict[str, Any] | None = None) -> Path:
    settings = settings or load_settings()
    if settings.get("image"):
        custom = _folder() / settings["image"]
        if custom.exists():
            return custom
    return DEFAULT_LOGO


def active(enabled_for_job: bool = True) -> dict[str, Any] | None:
    """The settings to stamp, or None when the watermark is off (globally or for this job)."""
    if not enabled_for_job:
        return None
    settings = load_settings()
    if not settings["enabled"] or not image_path(settings).exists():
        return None
    return settings


def _filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\\'")


def overlay_graph(chain: str, settings: dict[str, Any], size: tuple[int, int]) -> str:
    """A -vf filtergraph: the existing chain, then the logo over it in its corner."""
    width, height = size
    short = min(width, height)
    logo_width = max(16, round(settings["size"] * short * 1.6))
    logo_width -= logo_width % 2
    margin = round(settings["margin"] * short)
    x = f"{margin}" if settings["position"].endswith("left") else f"W-w-{margin}"
    y = f"{margin}" if settings["position"].startswith("top") else f"H-h-{margin}"
    logo = (
        f"movie='{_filter_path(image_path(settings))}',"
        f"scale={logo_width}:-1:flags=lanczos,format=rgba,"
        f"colorchannelmixer=aa={settings['opacity']}[wm]"
    )
    base = f"[in]{chain}[base]" if chain else "[in]null[base]"
    return f"{base};{logo};[base][wm]overlay={x}:{y}:format=auto[out]"
