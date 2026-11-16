"""Editor text boxes: free text drawn on the video for a time range (titles, callouts, notes).

A box is anchored at (x, y), fractions of the output frame; ``align`` says which point of
the box sits there (left edge, centre or right edge, always vertically centred), exactly as
the editor preview draws it. ``size`` is the font size as a fraction of the frame height.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

MAX_OVERLAYS = 100
MAX_TEXT = 500
ALIGNS = ("left", "center", "right")
DEFAULTS: dict[str, Any] = {
    "text": "",
    "start": 0.0,
    "end": 3.0,
    "x": 0.5,
    "y": 0.2,
    "size": 0.07,
    "color": "#FFFFFF",
    "bg_color": "#000000",
    "bg_opacity": 0.6,
    "bold": True,
    "align": "center",
    "font_name": "",
}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _number(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:  # NaN
        return fallback
    return min(high, max(low, number))


def _color(value: Any, fallback: str) -> str:
    return str(value).upper() if isinstance(value, str) and _HEX.match(value) else fallback


def normalize_text_overlays(items: Any, duration: float) -> list[dict[str, Any]]:
    """Clamp every field; drop empty boxes; keep at most MAX_OVERLAYS, sorted by start."""
    duration = max(0.1, float(duration or 0.1))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").replace("\r\n", "\n")[:MAX_TEXT]
        if not text.strip():
            continue
        start = round(_number(raw.get("start"), 0.0, 0.0, max(0.0, duration - 0.1)), 3)
        end = round(_number(raw.get("end"), start + 3.0, start + 0.1, duration), 3)
        overlay_id = str(raw.get("id") or "")[:64] or f"txt-{uuid.uuid4().hex[:8]}"
        if overlay_id in seen:
            overlay_id = f"txt-{uuid.uuid4().hex[:8]}"
        seen.add(overlay_id)
        align = raw.get("align") if raw.get("align") in ALIGNS else DEFAULTS["align"]
        result.append(
            {
                "id": overlay_id,
                "text": text,
                "start": start,
                "end": end,
                "x": round(_number(raw.get("x"), DEFAULTS["x"], 0.0, 1.0), 4),
                "y": round(_number(raw.get("y"), DEFAULTS["y"], 0.0, 1.0), 4),
                "size": round(_number(raw.get("size"), DEFAULTS["size"], 0.02, 0.3), 4),
                "color": _color(raw.get("color"), DEFAULTS["color"]),
                "bg_color": _color(raw.get("bg_color"), DEFAULTS["bg_color"]),
                "bg_opacity": round(_number(raw.get("bg_opacity"), DEFAULTS["bg_opacity"], 0.0, 1.0), 3),
                "bold": bool(raw.get("bold", DEFAULTS["bold"])),
                "align": align,
                "font_name": str(raw.get("font_name") or "")[:80],
            }
        )
        if len(result) >= MAX_OVERLAYS:
            break
    return sorted(result, key=lambda item: item["start"])


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def _ass_bgr(hex_color: str) -> str:
    return f"&H{hex_color[5:7]}{hex_color[3:5]}{hex_color[1:3]}&".upper()


def _ass_alpha(opacity: float) -> str:
    return f"&H{round((1 - opacity) * 255):02X}&"


def _ass_text(text: str) -> str:
    # Braces open override blocks and a backslash starts a tag: neither may reach libass raw.
    clean = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    return "\\N".join(clean.split("\n"))


def write_overlays_ass(
    overlays: list[dict[str, Any]], size: tuple[int, int], path: Path, default_font: str = "Arial"
) -> Path | None:
    """Writes an ASS file drawing the boxes on a frame of ``size`` pixels; None when there are none."""
    if not overlays:
        return None
    width, height = size
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",  # Only explicit line breaks: the box keeps the lines typed in the editor.
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic,"
        " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL,"
        " MarginR, MarginV, Encoding",
        # BorderStyle 3 draws an opaque box (OutlineColour) behind each line; 1 is plain text with a shadow.
        f"Style: Box,{default_font},48,&H00FFFFFF,&H00FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,3,0,0,5,0,0,0,1",
        f"Style: Plain,{default_font},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    anchors = {"left": 4, "center": 5, "right": 6}
    for index, item in enumerate(overlays):
        font_px = max(8, round(item["size"] * height))
        boxed = item["bg_opacity"] > 0
        tags = [
            f"\\an{anchors[item['align']]}",
            f"\\pos({round(item['x'] * width)},{round(item['y'] * height)})",
            f"\\fs{font_px}",
            f"\\b{1 if item['bold'] else 0}",
            f"\\1c{_ass_bgr(item['color'])}",
        ]
        if item.get("font_name"):
            tags.append(f"\\fn{item['font_name']}")
        if boxed:
            # The box is the line's border: its width is the padding around the text.
            tags += [f"\\bord{max(2, round(font_px * 0.3))}", f"\\3c{_ass_bgr(item['bg_color'])}", f"\\3a{_ass_alpha(item['bg_opacity'])}", "\\shad0"]
        else:
            tags += ["\\bord0", f"\\shad{max(1, round(font_px * 0.05))}"]
        # Later boxes are drawn on top, like the preview's stacking order.
        lines.append(
            f"Dialogue: {10 + index},{_ass_time(item['start'])},{_ass_time(item['end'])},"
            f"{'Box' if boxed else 'Plain'},,0,0,0,,{{{''.join(tags)}}}{_ass_text(item['text'])}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
