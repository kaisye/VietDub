from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .audio_bed import background_volume, tts_volume
from .storage import ensure_storage

DEFAULT_SUBTITLE_STYLE: dict[str, Any] = {
    "font_name": "Arial",
    "font_size": 20,
    "text_color": "#000000",
    "box_color": "#FFFFFF",
    "box_opacity": 1.0,
    "box_blur": 0,
    "outline_color": "#000000",
    "outline": 0,
    "shadow": 0,
    "alignment": "bottom_center",
    "margin_v": 34,
    "position_x": 0.5,
    "position_y": 0.92,
    "bold": False,
    # Bilingual burn-in: the source-language line is drawn under the translated
    # line, one language per line, at bilingual_font_scale × the main font size.
    "bilingual_enabled": False,
    "bilingual_font_scale": 0.72,
    "bilingual_color": "#444444",
    "hard_sub_blur_enabled": False,
    "hard_sub_blur_x": 0.5,
    "hard_sub_blur_y": 0.86,
    "hard_sub_blur_width": 0.72,
    "hard_sub_blur_height": 0.14,
    # box = uniform; vertical / horizontal = directional blur (smears along one axis).
    "hard_sub_blur_style": "box",
    # 0–250; 50 = the auto strength (1.0×), scaling the blur radius/sigma from 0× to 5×.
    "hard_sub_blur_strength": 50,
}

HARD_SUB_BLUR_STYLES = ("box", "vertical", "horizontal")
ASS_PLAY_RES_X = 384
ASS_PLAY_RES_Y = 288
# Companion SRT written next to the render subtitle, holding the source-language
# text on the render subtitle's own timings. Bilingual burn-in reads it.
SOURCE_LANGUAGE_SUBTITLE_SUFFIX = ".source.srt"


def render_video(
    video_path: Path,
    audio_path: Path | None,
    subtitle_path: Path | None,
    background_audio_path: Path | None = None,
    render_quality: str | None = None,
    subtitle_style: dict[str, Any] | None = None,
    audio_mix: dict[str, Any] | None = None,
    aspect_ratio: str = "source",
    max_lines: int = 2,
    output_filename: str | None = None,
) -> Path:
    """Render a real MP4 output with the generated voice and subtitles."""
    root = ensure_storage()
    safe_output_name = Path(output_filename).name if output_filename else f"{video_path.stem}.mp4"
    if Path(safe_output_name).suffix.lower() != ".mp4":
        safe_output_name = f"{Path(safe_output_name).stem}.mp4"
    output = root / "rendered-outputs" / safe_output_name
    video_duration = _probe_duration(video_path)
    video_encoding_args, audio_bitrate = _render_quality_args(render_quality)
    configured_subtitle_style = subtitle_style
    if subtitle_path is not None and configured_subtitle_style is None:
        from .subtitle_styles import active_subtitle_style

        configured_subtitle_style = active_subtitle_style()

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
    ]
    next_input_index = 1
    voice_input_index: int | None = None
    background_input_index: int | None = None
    if audio_path:
        voice_input_index = next_input_index
        next_input_index += 1
        command.extend(["-i", str(audio_path)])
    if background_audio_path:
        background_input_index = next_input_index
        command.extend(["-i", str(background_audio_path)])
    audio_map = "0:a:0?"
    if voice_input_index is not None and background_input_index is not None:
        command.extend(
            [
                "-filter_complex",
                _audio_mix_filter(
                    voice_input_index=voice_input_index,
                    background_input_index=background_input_index,
                    audio_mix=audio_mix,
                ),
            ]
        )
        audio_map = "[aout]"
    elif voice_input_index is not None:
        audio_map = f"{voice_input_index}:a:0"
    elif background_input_index is not None:
        command.extend(
            [
                "-filter_complex",
                _background_audio_filter(background_input_index, audio_mix),
            ]
        )
        audio_map = "[aout]"

    command.extend(["-map", "0:v:0", "-map", audio_map])
    video_filter = _video_filter(
        video_path,
        subtitle_path,
        configured_subtitle_style,
        aspect_ratio,
        max_lines=max_lines,
    )
    if video_filter:
        command.extend(["-vf", video_filter])
    command.extend(video_encoding_args)
    command.extend(["-c:a", "aac", "-b:a", audio_bitrate, "-t", f"{video_duration:.3f}", str(output)])

    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
        return output

    if subtitle_path is None:
        raise RuntimeError(f"Render failed: {result.stderr[-1000:]}")

    return _render_with_subtitle_track(
        video_path,
        audio_path,
        subtitle_path,
        output,
        result.stderr,
        background_audio_path,
        render_quality,
        audio_mix,
        aspect_ratio,
        configured_subtitle_style,
        max_lines=max_lines,
    )


def normalize_subtitle_style(style: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_SUBTITLE_STYLE)
    if isinstance(style, dict):
        merged.update({key: value for key, value in style.items() if value is not None})

    merged["font_name"] = str(merged.get("font_name") or DEFAULT_SUBTITLE_STYLE["font_name"]).strip()[:80]
    merged["font_size"] = _clamp_int(merged.get("font_size"), 10, 72, DEFAULT_SUBTITLE_STYLE["font_size"])
    merged["text_color"] = _normalize_hex_color(merged.get("text_color"), DEFAULT_SUBTITLE_STYLE["text_color"])
    merged["box_color"] = _normalize_hex_color(merged.get("box_color"), DEFAULT_SUBTITLE_STYLE["box_color"])
    merged["outline_color"] = _normalize_hex_color(merged.get("outline_color"), DEFAULT_SUBTITLE_STYLE["outline_color"])
    merged["box_opacity"] = _clamp_float(merged.get("box_opacity"), 0.0, 1.0, DEFAULT_SUBTITLE_STYLE["box_opacity"])
    merged["box_blur"] = _clamp_int(merged.get("box_blur"), 0, 24, DEFAULT_SUBTITLE_STYLE["box_blur"])
    merged["outline"] = _clamp_float(merged.get("outline"), 0.0, 8.0, DEFAULT_SUBTITLE_STYLE["outline"])
    merged["shadow"] = _clamp_int(merged.get("shadow"), 0, 8, DEFAULT_SUBTITLE_STYLE["shadow"])
    merged["margin_v"] = _clamp_int(merged.get("margin_v"), 0, 400, DEFAULT_SUBTITLE_STYLE["margin_v"])
    merged["position_x"] = _clamp_float(merged.get("position_x"), 0.05, 0.95, DEFAULT_SUBTITLE_STYLE["position_x"])
    merged["position_y"] = _clamp_float(merged.get("position_y"), 0.05, 0.95, DEFAULT_SUBTITLE_STYLE["position_y"])
    merged["bold"] = bool(merged.get("bold"))
    merged["bilingual_enabled"] = bool(merged.get("bilingual_enabled"))
    merged["bilingual_font_scale"] = _clamp_float(
        merged.get("bilingual_font_scale"), 0.4, 1.0, DEFAULT_SUBTITLE_STYLE["bilingual_font_scale"]
    )
    merged["bilingual_color"] = _normalize_hex_color(
        merged.get("bilingual_color"), DEFAULT_SUBTITLE_STYLE["bilingual_color"]
    )
    merged["hard_sub_blur_enabled"] = bool(merged.get("hard_sub_blur_enabled"))
    merged["hard_sub_blur_x"] = _clamp_float(
        merged.get("hard_sub_blur_x"), 0.02, 0.98, DEFAULT_SUBTITLE_STYLE["hard_sub_blur_x"]
    )
    merged["hard_sub_blur_y"] = _clamp_float(
        merged.get("hard_sub_blur_y"), 0.02, 0.98, DEFAULT_SUBTITLE_STYLE["hard_sub_blur_y"]
    )
    merged["hard_sub_blur_width"] = _clamp_float(
        merged.get("hard_sub_blur_width"), 0.04, 1.0, DEFAULT_SUBTITLE_STYLE["hard_sub_blur_width"]
    )
    merged["hard_sub_blur_height"] = _clamp_float(
        merged.get("hard_sub_blur_height"), 0.03, 0.80, DEFAULT_SUBTITLE_STYLE["hard_sub_blur_height"]
    )
    blur_style = str(merged.get("hard_sub_blur_style") or "box").strip().lower()
    merged["hard_sub_blur_style"] = blur_style if blur_style in HARD_SUB_BLUR_STYLES else "box"
    merged["hard_sub_blur_strength"] = _clamp_int(
        merged.get("hard_sub_blur_strength"), 0, 250, DEFAULT_SUBTITLE_STYLE["hard_sub_blur_strength"]
    )
    merged["alignment"] = _alignment_from_position(float(merged["position_x"]), float(merged["position_y"]))
    return merged


def detect_subtitle_region(video_path: Path) -> dict[str, Any]:
    width, height = _probe_dimensions(video_path)
    duration = _probe_duration(video_path)
    sample_times = _sample_times(duration)
    boxes: list[tuple[int, int, int, int, int, int]] = []
    for sample_time in sample_times:
        frame = _read_video_frame(video_path, sample_time, target_width=360)
        if frame:
            box = _detect_bright_text_box(frame)
            if box:
                boxes.append(box)

    style = normalize_subtitle_style()
    if not boxes:
        bottom = int(height * 0.88)
        top = int(height * 0.76)
        style["margin_v"] = max(24, height - bottom)
        style["position_x"] = 0.5
        style["position_y"] = round((top + bottom) / 2 / max(1, height), 4)
        style["alignment"] = _alignment_from_position(float(style["position_x"]), float(style["position_y"]))
        return {
            "detected": False,
            "region": _normalized_region(0, top, width, bottom, width, height),
            "subtitle_style": style,
        }

    scale_boxes: list[tuple[int, int, int, int]] = []
    for left, top, right, bottom, frame_width, frame_height in boxes:
        scale_x = width / max(1, frame_width)
        scale_y = height / max(1, frame_height)
        scale_boxes.append((
            round(left * scale_x),
            round(top * scale_y),
            round(right * scale_x),
            round(bottom * scale_y),
        ))
    left = min(box[0] for box in scale_boxes)
    top = min(box[1] for box in scale_boxes)
    right = max(box[2] for box in scale_boxes)
    bottom = max(box[3] for box in scale_boxes)
    style["margin_v"] = max(8, min(220, height - bottom + 4))
    style["position_x"] = round((left + right) / 2 / max(1, width), 4)
    style["position_y"] = round((top + bottom) / 2 / max(1, height), 4)
    style["alignment"] = _alignment_from_position(float(style["position_x"]), float(style["position_y"]))
    return {
        "detected": True,
        "region": _normalized_region(left, top, right, bottom, width, height),
        "subtitle_style": style,
    }


def render_subtitle_preview(
    video_path: Path,
    subtitle_path: Path,
    subtitle_style: dict[str, Any] | None = None,
    at_seconds: float | None = None,
    max_lines: int = 2,
) -> Path:
    root = ensure_storage()
    output_dir = root / "subtitle-previews"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{video_path.stem}.{uuid.uuid4().hex[:8]}.png"
    duration = _probe_duration(video_path)
    sample_time = at_seconds if at_seconds is not None else min(max(0.5, duration * 0.25), max(0.5, duration - 0.2))
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-ss",
        f"{max(0.0, sample_time):.3f}",
        "-vf",
        _subtitle_filter(subtitle_path, subtitle_style, _probe_dimensions(video_path), max_lines=max_lines),
        "-frames:v",
        "1",
        "-compression_level",
        "3",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Unable to render subtitle style preview: {result.stderr[-1000:]}")
    return output


def _render_with_subtitle_track(
    video_path: Path,
    audio_path: Path | None,
    subtitle_path: Path,
    output: Path,
    previous_error: str,
    background_audio_path: Path | None = None,
    render_quality: str | None = None,
    audio_mix: dict[str, Any] | None = None,
    aspect_ratio: str = "source",
    subtitle_style: dict[str, Any] | None = None,
    max_lines: int = 2,
) -> Path:
    video_duration = _probe_duration(video_path)
    video_encoding_args, audio_bitrate = _render_quality_args(render_quality)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
    ]
    next_input_index = 1
    voice_input_index: int | None = None
    if audio_path:
        voice_input_index = next_input_index
        next_input_index += 1
        command.extend(["-i", str(audio_path)])
    subtitle_input_index = next_input_index
    next_input_index += 1
    command.extend(["-i", str(subtitle_path)])
    background_input_index: int | None = None
    if background_audio_path:
        background_input_index = next_input_index
        command.extend(["-i", str(background_audio_path)])
    audio_map = "0:a:0?"
    if voice_input_index is not None and background_input_index is not None:
        command.extend(
            [
                "-filter_complex",
                _audio_mix_filter(
                    voice_input_index=voice_input_index,
                    background_input_index=background_input_index,
                    audio_mix=audio_mix,
                ),
            ]
        )
        audio_map = "[aout]"
    elif voice_input_index is not None:
        audio_map = f"{voice_input_index}:a:0"
    elif background_input_index is not None:
        command.extend(
            [
                "-filter_complex",
                _background_audio_filter(background_input_index, audio_mix),
            ]
        )
        audio_map = "[aout]"
    command.extend([
        "-map",
        "0:v:0",
        "-map",
        audio_map,
        "-map",
        f"{subtitle_input_index}:0",
    ])
    video_filter = _video_filter(video_path, None, subtitle_style, aspect_ratio, max_lines=max_lines)
    if video_filter:
        command.extend(["-vf", video_filter])
    command.extend(video_encoding_args)
    command.extend(["-c:a", "aac", "-b:a", audio_bitrate, "-c:s", "mov_text", "-t", f"{video_duration:.3f}", str(output)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"Render failed. Burn-in error: {previous_error[-1000:]}; subtitle-track error: {result.stderr[-1000:]}")
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("Render completed without producing an output video.")
    return output


def _subtitle_filter(
    subtitle_path: Path,
    subtitle_style: dict[str, Any] | None = None,
    video_size: tuple[int, int] | None = None,
    max_lines: int = 2,
) -> str:
    configured = normalize_subtitle_style(subtitle_style)
    if video_size:
        # Generate a proper ASS file with PlayRes = video dimensions so that
        # margins and font size are correct for any aspect ratio (including 9:16).
        ass_path = _srt_to_ass(subtitle_path, configured, video_size, max_lines)
        normalized_ass = ass_path.resolve().as_posix().replace(":", r"\:").replace("'", r"\\'")
        return f"ass='{normalized_ass}'"
    # Fallback when video dimensions are unknown (should not normally happen).
    normalized = subtitle_path.resolve().as_posix().replace(":", r"\:").replace("'", r"\\'")
    position = _ass_position_style(configured, video_size)
    style = ",".join(
        [
            f"FontName={configured['font_name']}",
            f"FontSize={configured['font_size']}",
            f"PrimaryColour={_ass_color(configured['text_color'])}",
            f"BackColour={_ass_color(configured['box_color'], opacity=float(configured['box_opacity']))}",
            f"OutlineColour={_ass_color(configured['outline_color'])}",
            "BorderStyle=4",
            f"Outline={configured['outline']}",
            f"Shadow={configured['shadow']}",
            f"Blur={configured['box_blur']}",
            f"Alignment={position['alignment']}",
            f"MarginL={position['margin_l']}",
            f"MarginR={position['margin_r']}",
            f"MarginV={position['margin_v']}",
            f"Bold={-1 if configured['bold'] else 0}",
        ]
    )
    return f"subtitles='{normalized}':force_style='{style}'"


def prepare_display_subtitle(
    video_path: Path,
    subtitle_path: Path,
    subtitle_style: dict[str, Any] | None = None,
    max_lines: int = 2,
) -> Path:
    """Create the display cues before TTS so voice and subtitles share them."""
    configured = normalize_subtitle_style(subtitle_style)
    chars_per_line = _subtitle_chars_per_line(configured, _probe_dimensions(video_path))
    display_cues: list[tuple[float, float, str]] = []
    source_cues = _merge_orphan_display_cues(_parse_srt_for_ass(subtitle_path))
    for cue_start, cue_end, cue_text in source_cues:
        display_cues.extend(
            _wrap_cue_for_ass(cue_text, cue_start, cue_end, chars_per_line, max_lines)
        )

    if not display_cues:
        return subtitle_path

    destination = subtitle_path.with_name(f"{subtitle_path.stem}.display.srt")
    blocks = [
        "\n".join(
            [str(index), f"{_srt_time(start)} --> {_srt_time(end)}", text]
        )
        for index, (start, end, text) in enumerate(display_cues, start=1)
        if text.strip() and end > start
    ]
    destination.write_text("\n\n".join(blocks).strip() + "\n", encoding="utf-8")
    return destination


def _merge_orphan_display_cues(
    cues: list[tuple[float, float, str]],
) -> list[tuple[float, float, str]]:
    merged: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        words = text.split()
        starts_as_continuation = bool(text) and text[0].islower()
        if (
            merged
            and len(words) <= 2
            and starts_as_continuation
            and start - merged[-1][1] <= 0.2
            and not re.search(r"[.!?\u3002\uff01\uff1f]\s*$", merged[-1][2])
        ):
            previous_start, _, previous_text = merged[-1]
            merged[-1] = (previous_start, end, f"{previous_text.rstrip()} {text.lstrip()}")
            continue
        merged.append((start, end, text))
    return merged


def _srt_to_ass(
    srt_path: Path,
    configured: dict[str, Any],
    video_size: tuple[int, int],
    max_lines: int,
) -> Path:
    """Convert SRT to a properly positioned ASS file for any video aspect ratio.

    Using PlayResX/PlayResY = video dimensions means all coordinate values
    (margins, font size) are in actual video pixels, so 9:16 portrait videos
    position and size subtitles correctly without the distortion that occurs
    when the default 4:3 ASS virtual canvas is stretched to a portrait frame.
    """
    width, height = video_size
    position_x = float(configured["position_x"])
    position_y = float(configured["position_y"])
    alignment_text = _alignment_from_position(position_x, position_y)
    alignment = _ass_alignment(alignment_text)
    vertical = alignment_text.split("_")[0]

    # Normalize font size so it looks the same FRACTION OF VIDEO WIDTH as on a
    # 16:9 1080p reference where the ASS virtual canvas is 384x288 and libass
    # applies a Y-scale of 1080/288 = 3.75, giving visual_px = font_size * 3.75.
    # To match that fraction on any video width:
    #   font_fraction = (configured_font_size * 3.75) / 1920
    #   target_font_px = font_fraction * video_width
    #                  = configured_font_size * video_width * 3.75 / 1920
    #                  = configured_font_size * video_width / 512
    font_size = max(8, round(configured["font_size"] * width / 512))

    # Margins in actual video pixels (PlayRes = video dimensions).
    margin_l = round(position_x * width) if position_x < 0.45 else 0
    margin_r = round((1.0 - position_x) * width) if position_x > 0.55 else 0
    if vertical == "top":
        margin_v = round(position_y * height)
    elif vertical == "bottom":
        margin_v = round((1.0 - position_y) * height)
    else:
        margin_v = 0

    chars_per_line = _subtitle_chars_per_line(configured, video_size)

    # Parse SRT cues.
    srt_cues = _parse_srt_for_ass(srt_path)

    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: None",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,"
        " BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
        " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            f"Style: Default,{configured['font_name']},{font_size},"
            f"{_ass_color(configured['text_color'])},&H000000FF,"
            f"{_ass_color(configured['outline_color'])},"
            f"{_ass_color(configured['box_color'], float(configured['box_opacity']))},"
            f"{-1 if configured['bold'] else 0},0,0,0,100,100,0,0,"
            f"4,{configured['outline']},{configured['shadow']},"
            f"{alignment},{margin_l},{margin_r},{margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # \anN\pos(x,y): center of text block at position_y for every cue.
    # 1-line and 2-line subtitles both center at the same position_y — no jumping.
    h_an = 4 if position_x <= 0.33 else 6 if position_x >= 0.67 else 5
    pos_tag = "{" + f"\\an{h_an}\\pos({round(position_x * width)},{round(position_y * height)})" + "}"

    # Bilingual: the source-language line rides on the same Dialogue event as an
    # inline \fs/\1c override, so both languages stay in one block that libass
    # centers on position_y together. Separate events would drift apart whenever
    # the translated line wrapped to a different number of lines.
    secondary_cues: list[tuple[float, float, str]] = []
    secondary_tag = ""
    secondary_chars_per_line = chars_per_line
    if configured.get("bilingual_enabled"):
        secondary_path = source_language_subtitle_path(srt_path)
        if secondary_path.exists():
            secondary_cues = _parse_srt_for_ass(secondary_path)
    if secondary_cues:
        scale = float(configured["bilingual_font_scale"])
        secondary_font_size = max(6, round(font_size * scale))
        secondary_chars_per_line = max(15, round(chars_per_line / scale))
        secondary_tag = (
            "{" + f"\\fs{secondary_font_size}\\1c{_ass_color_tag(configured['bilingual_color'])}" + "}"
        )

    for cue_start, cue_end, cue_text in srt_cues:
        sub_cues = _wrap_cue_for_ass(cue_text, cue_start, cue_end, chars_per_line, max_lines)
        for s_start, s_end, s_text in sub_cues:
            ass_text = s_text.replace("\n", r"\N")
            if secondary_tag:
                secondary_lines = _wrap_text_lines(
                    _secondary_text_for_span(secondary_cues, s_start, s_end),
                    secondary_chars_per_line,
                    max_lines,
                )
                if secondary_lines:
                    ass_text += r"\N" + secondary_tag + r"\N".join(secondary_lines)
            ass_lines.append(
                f"Dialogue: 0,{_ass_time(s_start)},{_ass_time(s_end)},"
                f"Default,,0,0,0,,{pos_tag}{ass_text}"
            )

    ass_path = srt_path.with_suffix(".render.ass")
    ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8-sig")
    return ass_path


def source_language_subtitle_path(subtitle_path: Path) -> Path:
    """Return the source-language companion for a render or display subtitle.

    ``translate_subtitle`` writes ``<stem>.render.source.srt`` alongside
    ``<stem>.render.srt``. ``prepare_display_subtitle`` later rewrites the render
    subtitle into ``<stem>.render.display.srt``, so the ``.display`` marker is
    dropped before resolving the companion — both point at the same source text.
    """
    stem = subtitle_path.stem
    if stem.endswith(".display"):
        stem = stem[: -len(".display")]
    return subtitle_path.with_name(f"{stem}{SOURCE_LANGUAGE_SUBTITLE_SUFFIX}")


def _secondary_text_for_span(
    cues: list[tuple[float, float, str]],
    start: float,
    end: float,
) -> str:
    """Return the source-language text that belongs to one display cue.

    Display wrapping splits a translated cue into consecutive time slices, so a
    source cue can straddle several of them. A source cue that is mostly covered
    contributes its whole text; a partly covered one contributes the matching
    slice of its words, which keeps the source line moving with the translation
    instead of repeating in full under every slice.
    """
    parts: list[str] = []
    for cue_start, cue_end, cue_text in cues:
        overlap = min(end, cue_end) - max(start, cue_start)
        if overlap <= 0.01:
            continue
        duration = max(0.05, cue_end - cue_start)
        if overlap >= duration * 0.85:
            parts.append(cue_text)
            continue
        words = cue_text.split()
        if not words:
            continue
        first = round(len(words) * max(0.0, max(start, cue_start) - cue_start) / duration)
        last = round(len(words) * min(1.0, (min(end, cue_end) - cue_start) / duration))
        sliced = words[first : max(first + 1, last)]
        if sliced:
            parts.append(" ".join(sliced))
    return " ".join(parts).strip()


def _subtitle_chars_per_line(
    configured: dict[str, Any],
    video_size: tuple[int, int],
) -> int:
    width, _ = video_size
    position_x = float(configured["position_x"])
    font_size = max(8, round(configured["font_size"] * width / 512))
    margin_l = round(position_x * width) if position_x < 0.45 else 0
    margin_r = round((1.0 - position_x) * width) if position_x > 0.55 else 0
    available_width = max(100, width - margin_l - margin_r)
    return max(15, int(available_width / max(1, font_size * 0.55)))


def _parse_srt_for_ass(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if "-->" in b]
    cues: list[tuple[float, float, str]] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        t_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if t_idx is None:
            continue
        start_s, end_s = lines[t_idx].split("-->", 1)
        start = _srt_secs(start_s.strip())
        end = _srt_secs(end_s.strip().split()[0])
        cue_text = " ".join(ln for ln in lines[t_idx + 1:] if ln and not ln.isdigit()).strip()
        if cue_text and end > start:
            cues.append((start, end, cue_text))
    return cues


def _srt_secs(value: str) -> float:
    m = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", value)
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000


def _wrap_cue_for_ass(
    text: str,
    start: float,
    end: float,
    chars_per_line: int,
    max_lines: int,
) -> list[tuple[float, float, str]]:
    """Word-wrap cue text and split into consecutive sub-cues of at most max_lines each."""
    wrapped = _wrap_text_lines(text, chars_per_line, max_lines)
    if not wrapped:
        return [(start, end, text)]

    # Group into blocks of max_lines each.
    groups: list[str] = []
    for i in range(0, len(wrapped), max(1, max_lines)):
        groups.append("\n".join(wrapped[i : i + max_lines]))

    if len(groups) == 1:
        return [(start, end, groups[0])]

    # Distribute cue time proportionally across groups.
    duration = max(0.05, end - start)
    n = len(groups)
    result: list[tuple[float, float, str]] = []
    for i, group in enumerate(groups):
        g_start = round(start + duration * i / n, 3)
        g_end = round(start + duration * (i + 1) / n, 3)
        result.append((g_start, g_end, group))
    return result


def _wrap_text_lines(text: str, chars_per_line: int, max_lines: int) -> list[str]:
    """Word-wrap one line of text, preferring an even two-line split."""
    words = text.split()
    if not words:
        return []

    balanced = _balanced_ass_wrap(words, chars_per_line, max_lines)
    if balanced is not None:
        return balanced

    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= chars_per_line:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _balanced_ass_wrap(
    words: list[str],
    chars_per_line: int,
    max_lines: int,
) -> list[str] | None:
    if max_lines != 2 or len(words) <= 2:
        return None

    slack = max(2, min(8, round(chars_per_line * 0.12)))
    limit = chars_per_line + slack
    text_length = len(" ".join(words))
    # Text fits on a single line — no split needed.
    if text_length <= limit:
        return None
    if text_length > max_lines * limit:
        return None

    candidates: list[tuple[float, int, list[str]]] = []
    for split_index in range(1, len(words)):
        lines = [" ".join(words[:split_index]), " ".join(words[split_index:])]
        lengths = [len(line) for line in lines]
        if any(length > limit for length in lengths):
            continue
        if any(len(line.split()) <= 1 for line in lines):
            continue

        overflow = sum(max(0, length - chars_per_line) for length in lengths)
        balance = abs(lengths[0] - lengths[1])
        candidates.append((overflow * 10 + balance, split_index, lines))

    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[2]


def _ass_time(seconds: float) -> str:
    cs = round(max(0.0, seconds) * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _probe_dimensions(path: Path) -> tuple[int, int]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return 1920, 1080
    try:
        width, height = result.stdout.strip().split("x", 1)
        return max(1, int(width)), max(1, int(height))
    except ValueError:
        return 1920, 1080


def _sample_times(duration: float) -> list[float]:
    safe_duration = max(0.5, duration)
    candidates = [safe_duration * 0.25, safe_duration * 0.5, safe_duration * 0.75]
    return [min(max(0.2, value), max(0.2, safe_duration - 0.2)) for value in candidates]


def _read_video_frame(video_path: Path, at_seconds: float, target_width: int) -> dict[str, Any] | None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{at_seconds:.3f}",
        "-i",
        str(video_path),
        "-vf",
        f"scale={target_width}:-1",
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "ppm",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, timeout=45)
    if result.returncode != 0 or not result.stdout:
        return None
    return _parse_ppm(result.stdout)


def _parse_ppm(data: bytes) -> dict[str, Any] | None:
    if not data.startswith(b"P6"):
        return None
    parts: list[bytes] = []
    cursor = 2
    while len(parts) < 3 and cursor < len(data):
        while cursor < len(data) and data[cursor] in b" \t\r\n":
            cursor += 1
        if cursor < len(data) and data[cursor] == ord("#"):
            while cursor < len(data) and data[cursor] not in b"\r\n":
                cursor += 1
            continue
        start = cursor
        while cursor < len(data) and data[cursor] not in b" \t\r\n":
            cursor += 1
        parts.append(data[start:cursor])
    if len(parts) != 3:
        return None
    while cursor < len(data) and data[cursor] in b" \t\r\n":
        cursor += 1
    try:
        width = int(parts[0])
        height = int(parts[1])
        max_value = int(parts[2])
    except ValueError:
        return None
    pixels = data[cursor:]
    if max_value != 255 or len(pixels) < width * height * 3:
        return None
    return {"width": width, "height": height, "pixels": pixels[: width * height * 3]}


def _detect_bright_text_box(frame: dict[str, Any]) -> tuple[int, int, int, int, int, int] | None:
    width = int(frame["width"])
    height = int(frame["height"])
    pixels: bytes = frame["pixels"]
    start_y = int(height * 0.72)
    active: list[tuple[int, int]] = []
    row_counts = [0] * height
    for y in range(start_y, height):
        row_count = 0
        for x in range(width):
            offset = (y * width + x) * 3
            r, g, b = pixels[offset], pixels[offset + 1], pixels[offset + 2]
            luma = (77 * r + 150 * g + 29 * b) >> 8
            if luma >= 220:
                row_count += 1
                active.append((x, y))
        row_counts[y] = row_count

    min_row_pixels = max(4, int(width * 0.015))
    candidate_rows = [y for y in range(start_y, height) if min_row_pixels <= row_counts[y] <= width * 0.75]
    if not candidate_rows:
        return None

    top = max(start_y, min(candidate_rows) - 4)
    bottom = min(height - 1, max(candidate_rows) + 4)
    scoped = [(x, y) for x, y in active if top <= y <= bottom]
    if not scoped:
        return None
    left = max(0, min(x for x, _y in scoped) - 8)
    right = min(width - 1, max(x for x, _y in scoped) + 8)
    if bottom - top < 8 or right - left < width * 0.08:
        return None
    return left, top, right, bottom, width, height


def _normalized_region(left: int, top: int, right: int, bottom: int, width: int, height: int) -> dict[str, float]:
    return {
        "x": round(max(0.0, min(1.0, left / max(1, width))), 4),
        "y": round(max(0.0, min(1.0, top / max(1, height))), 4),
        "width": round(max(0.0, min(1.0, (right - left) / max(1, width))), 4),
        "height": round(max(0.0, min(1.0, (bottom - top) / max(1, height))), 4),
    }


def _alignment_from_position(position_x: float, position_y: float) -> str:
    vertical = "top" if position_y <= 0.33 else "middle" if position_y < 0.67 else "bottom"
    horizontal = "left" if position_x <= 0.33 else "center" if position_x < 0.67 else "right"
    return f"{vertical}_{horizontal}"


def _ass_alignment(value: str) -> int:
    return {
        "bottom_left": 1,
        "bottom_center": 2,
        "bottom_right": 3,
        "middle_left": 4,
        "middle_center": 5,
        "middle_right": 6,
        "top_left": 7,
        "top_center": 8,
        "top_right": 9,
    }.get(value, 2)


def _ass_position_style(configured: dict[str, Any], video_size: tuple[int, int] | None = None) -> dict[str, int]:
    position_x = float(configured.get("position_x", DEFAULT_SUBTITLE_STYLE["position_x"]))
    position_y = float(configured.get("position_y", DEFAULT_SUBTITLE_STYLE["position_y"]))
    alignment_text = _alignment_from_position(position_x, position_y)
    vertical, horizontal = alignment_text.split("_", 1)
    margin_l = int(position_x * ASS_PLAY_RES_X) if horizontal == "left" else 0
    margin_r = int((1.0 - position_x) * ASS_PLAY_RES_X) if horizontal == "right" else 0
    if vertical == "top":
        margin_v = int(position_y * ASS_PLAY_RES_Y)
    elif vertical == "bottom":
        margin_v = int((1.0 - position_y) * ASS_PLAY_RES_Y)
    else:
        margin_v = int(abs(position_y - 0.5) * ASS_PLAY_RES_Y)
    return {
        "alignment": _ass_alignment(alignment_text),
        "margin_l": max(0, margin_l),
        "margin_r": max(0, margin_r),
        "margin_v": max(0, margin_v),
    }


def _ass_color(hex_color: str, opacity: float = 1.0) -> str:
    value = _normalize_hex_color(hex_color, "#000000").lstrip("#")
    red = value[0:2]
    green = value[2:4]
    blue = value[4:6]
    alpha = round((1.0 - _clamp_float(opacity, 0.0, 1.0, 1.0)) * 255)
    return f"&H{alpha:02X}{blue}{green}{red}"


def _ass_color_tag(hex_color: str) -> str:
    """Return an inline ``\\1c`` colour value (``&HBBGGRR&``, no alpha channel)."""
    value = _normalize_hex_color(hex_color, "#FFFFFF").lstrip("#")
    return f"&H{value[4:6]}{value[2:4]}{value[0:2]}&"


def _normalize_hex_color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) == 7:
        try:
            int(text[1:], 16)
            return text.upper()
        except ValueError:
            pass
    return fallback


def _clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _clamp_float(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _render_quality_args(render_quality: str | None) -> tuple[list[str], str]:
    configured = (render_quality or os.getenv("AETHER_RENDER_QUALITY", "balanced")).strip().lower()
    aliases = {
        "default": "balanced",
        "good": "balanced",
        "hq": "high",
        "best": "source",
        "original": "source",
        "near_source": "source",
        "near-source": "source",
    }
    quality = aliases.get(configured, configured)
    settings = {
        "fast": ("veryfast", "24", "160k"),
        "balanced": ("veryfast", "20", "192k"),
        "high": ("medium", "18", "256k"),
        "source": ("slow", "16", "320k"),
    }
    preset, crf, audio_bitrate = settings.get(quality, settings["balanced"])
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"], audio_bitrate


def _audio_mix_filter(
    voice_input_index: int = 1,
    background_input_index: int = 2,
    audio_mix: dict[str, Any] | None = None,
) -> str:
    settings = audio_mix or {}
    voice_gain = _mix_gain(settings.get("dubbed_volume"), tts_volume())
    bed_gain = _mix_gain(settings.get("original_volume"), background_volume())
    normalize = bool(settings.get("normalize", True))
    ducking = bool(settings.get("ducking", False))
    filters = [
        f"[{voice_input_index}:a]volume={voice_gain:.3f}[voice]",
        f"[{background_input_index}:a]volume={bed_gain:.3f}[bed]",
    ]
    if ducking:
        filters.append("[voice]asplit=2[voice_mix][voice_sidechain]")
        filters.append(
            "[bed][voice_sidechain]sidechaincompress="
            "threshold=0.04:ratio=8:attack=20:release=300[ducked]"
        )
        bed_label = "ducked"
        voice_label = "voice_mix"
    else:
        bed_label = "bed"
        voice_label = "voice"
    limiter = ",alimiter=limit=0.95" if normalize else ""
    filters.append(
        f"[{bed_label}][{voice_label}]amix=inputs=2:duration=first:dropout_transition=0:normalize=0"
        f"{limiter}[aout]"
    )
    return ";".join(filters)


def _background_audio_filter(
    background_input_index: int,
    audio_mix: dict[str, Any] | None = None,
) -> str:
    settings = audio_mix or {}
    bed_gain = _mix_gain(settings.get("original_volume"), background_volume())
    normalize = bool(settings.get("normalize", True))
    limiter = ",alimiter=limit=0.95" if normalize else ""
    return f"[{background_input_index}:a]volume={bed_gain:.3f}{limiter}[aout]"


def _mix_gain(value: Any, fallback: float) -> float:
    try:
        return max(0.0, min(2.0, float(value) / 100.0))
    except (TypeError, ValueError):
        return fallback


def _video_filter(
    video_path: Path,
    subtitle_path: Path | None,
    subtitle_style: dict[str, Any] | None,
    aspect_ratio: str,
    max_lines: int = 2,
) -> str:
    ratio_filter = _aspect_ratio_filter(video_path, aspect_ratio)
    configured = normalize_subtitle_style(subtitle_style)
    target_dimensions = _target_dimensions(video_path, aspect_ratio)
    blur_filter = _hard_sub_blur_filter(configured, target_dimensions)
    subtitle_filter = ""
    if subtitle_path is not None:
        subtitle_filter = _subtitle_filter(
            subtitle_path,
            configured,
            target_dimensions,
            max_lines=max_lines,
        )
    prefix = f"{ratio_filter}," if ratio_filter else ""
    if blur_filter:
        output_chain = blur_filter
        if subtitle_filter:
            output_chain = f"{output_chain},{subtitle_filter}"
        return f"{prefix}{output_chain}"
    return ",".join(filter(None, (ratio_filter, subtitle_filter)))


def _hard_sub_blur_filter(configured: dict[str, Any], video_size: tuple[int, int]) -> str:
    if not configured.get("hard_sub_blur_enabled"):
        return ""
    width, height = video_size
    region_width = max(8, min(width, round(float(configured["hard_sub_blur_width"]) * width)))
    region_height = max(8, min(height, round(float(configured["hard_sub_blur_height"]) * height)))
    center_x = float(configured["hard_sub_blur_x"]) * width
    center_y = float(configured["hard_sub_blur_y"]) * height
    left = max(0, min(width - region_width, round(center_x - region_width / 2)))
    top = max(0, min(height - region_height, round(center_y - region_height / 2)))
    blur = _blur_op(
        str(configured.get("hard_sub_blur_style") or "box"),
        region_width,
        region_height,
        int(configured.get("hard_sub_blur_strength") or 50),
    )
    return (
        "split[hard_sub_base][hard_sub_crop];"
        f"[hard_sub_crop]crop={region_width}:{region_height}:{left}:{top},"
        f"{blur}[hard_sub_blurred];"
        f"[hard_sub_base][hard_sub_blurred]overlay={left}:{top}"
    )


def _blur_op(style: str, region_width: int, region_height: int, strength: int = 50) -> str:
    """Return the FFmpeg blur filter for the chosen blur style and strength.

    - ``box``       : uniform box blur (default, isotropic).
    - ``vertical``  : Gaussian blur on the vertical axis only (``sigma=0`` cancels the
      horizontal pass), smearing content top-to-bottom.
    - ``horizontal``: Gaussian blur on the horizontal axis only.

    ``strength`` is 0–250; 50 keeps the size-derived auto radius (1.0×) and the slider
    scales it from 0× to 5×.
    """
    style = (style or "box").strip().lower()
    mul = max(0, min(250, strength)) / 50.0
    if style == "vertical":
        sigma_v = max(2, min(200, round(region_height / 3 * mul)))
        return f"gblur=sigma=0:sigmaV={sigma_v}"
    if style == "horizontal":
        sigma_h = max(2, min(200, round(region_width / 12 * mul)))
        return f"gblur=sigma={sigma_h}:sigmaV=0"
    # boxblur rejects any radius larger than half the plane size. On 4:2:0 video
    # the chroma plane is subsampled to a quarter area, so its ceiling is
    # min(w,h)//4 — well under the luma ceiling. Emit an explicit, separately
    # clamped chroma radius so a strong blur over a short caption band can't push
    # the chroma radius past FFmpeg's limit (which aborts the entire render with
    # "Invalid chroma_param radius value").
    smallest = max(2, min(region_width, region_height))
    auto_radius = max(1, min(60, round(region_height / 8 * mul)))
    luma_radius = min(auto_radius, max(1, smallest // 2))
    chroma_radius = min(auto_radius, max(1, smallest // 4))
    return f"boxblur={luma_radius}:2:{chroma_radius}:2"


def _aspect_ratio_filter(video_path: Path, aspect_ratio: str) -> str:
    if not aspect_ratio or aspect_ratio == "source":
        return ""
    target = _target_dimensions(video_path, aspect_ratio)
    if not target:
        return ""
    width, height = target
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )


def _target_dimensions(video_path: Path, aspect_ratio: str) -> tuple[int, int]:
    source_width, source_height = _probe_dimensions(video_path)
    ratios = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}
    ratio = ratios.get(aspect_ratio)
    if not ratio:
        return source_width, source_height
    source_ratio = source_width / max(1, source_height)
    if source_ratio >= ratio:
        height = source_height
        width = round(height * ratio)
    else:
        width = source_width
        height = round(width / ratio)
    return max(2, width // 2 * 2), max(2, height // 2 * 2)


def _probe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return 3600.0
    try:
        return max(0.1, float(result.stdout.strip()))
    except ValueError:
        return 3600.0
