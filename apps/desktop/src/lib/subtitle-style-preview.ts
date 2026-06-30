import type { CSSProperties } from "react";

import type { SubtitleStyle } from "../types";

export const DEFAULT_SUBTITLE_STYLE: SubtitleStyle = {
  font_name: "Arial",
  font_size: 20,
  text_color: "#000000",
  box_color: "#FFFFFF",
  box_opacity: 1,
  box_blur: 0,
  outline_color: "#000000",
  outline: 0,
  shadow: 0,
  alignment: "bottom_center",
  margin_v: 34,
  position_x: 0.5,
  position_y: 0.92,
  bold: false,
};

export function subtitlePreviewStyle(
  style: Partial<SubtitleStyle> | undefined,
  position?: "bottom" | "middle" | "top",
  maxLines: number = 2,
): CSSProperties {
  const normalized = { ...DEFAULT_SUBTITLE_STYLE, ...(style ?? {}) };
  const positionY = position
    ? position === "top"
      ? 0.16
      : position === "middle"
        ? 0.5
        : 0.88
    : clamp(normalized.position_y, 0.05, 0.95);

  // Match the backend font-size formula: font_px = configured * video_width / 512
  // (512 = ref_width 1920 / ref_scale 3.75 — see renderer.py _srt_to_ass)
  // As a fraction of container width: configured / 512 * 100 = configured / 5.12 cqw
  // This ensures the preview matches the actual render for any aspect ratio,
  // including portrait 9:16 where cqh-based sizing was ~2.3× too large.
  const fontSizeCqw = clamp(normalized.font_size, 8, 72) / 5.12;

  // Horizontal anchor must mirror the renderer (_srt_to_ass uses \an4/\an5/\an6):
  // the subtitle is left-anchored for position_x <= 0.33 (text grows right),
  // right-anchored for >= 0.67 (text grows left), and centered in between. This
  // keeps the box fully on-screen at the edges instead of always centering on
  // the point, which let it run off (and collapse) past the frame.
  const positionX = clamp(normalized.position_x, 0.05, 0.95);
  const anchorX = positionX <= 0.33 ? "0%" : positionX >= 0.67 ? "-100%" : "-50%";

  return {
    left: `${positionX * 100}%`,
    top: `${positionY * 100}%`,
    transform: `translate(${anchorX}, -50%)`,
    // max-content keeps the box at its natural width regardless of horizontal
    // position. With auto width the absolutely-positioned box's available width
    // is `frameWidth - left`, so dragging it right shrank the wrap width and
    // collapsed the text into a tiny cropped box.
    width: "max-content",
    maxWidth: "82%",
    color: normalized.text_color,
    backgroundColor: hexToRgba(normalized.box_color, normalized.box_opacity),
    backdropFilter: normalized.box_blur > 0 ? `blur(${normalized.box_blur}px)` : undefined,
    WebkitBackdropFilter: normalized.box_blur > 0 ? `blur(${normalized.box_blur}px)` : undefined,
    fontFamily: normalized.font_name,
    fontSize: `clamp(8px, ${fontSizeCqw}cqw, 96px)`,
    fontWeight: normalized.bold ? 800 : 600,
    lineHeight: 1.35,
    textShadow: normalized.shadow > 0
      ? `${normalized.shadow}px ${normalized.shadow}px 0 ${normalized.outline_color}`
      : undefined,
    // paint-order:stroke draws the outline BEHIND the fill, so a thicker outline
    // grows outward instead of eating into the glyphs. This matches the
    // ffmpeg/libass render, where the text fill always sits on top of the
    // outline. Width is doubled because only the outer half of a centered stroke
    // stays visible once the fill covers the inner half.
    paintOrder: "stroke",
    WebkitTextStroke: normalized.outline > 0
      ? `${normalized.outline / 9.6}cqw ${normalized.outline_color}`
      : undefined,
    // Limit visible lines to match the max_lines render setting.
    display: "-webkit-box",
    WebkitBoxOrient: "vertical",
    WebkitLineClamp: maxLines,
    overflow: "hidden",
  };
}

function clamp(value: number, min: number, max: number) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(min, Math.min(max, number)) : (min + max) / 2;
}

function hexToRgba(hex: string, opacity: number) {
  const normalized = /^#[0-9a-fA-F]{6}$/.test(hex) ? hex : "#FFFFFF";
  const red = parseInt(normalized.slice(1, 3), 16);
  const green = parseInt(normalized.slice(3, 5), 16);
  const blue = parseInt(normalized.slice(5, 7), 16);
  return `rgba(${red}, ${green}, ${blue}, ${clamp(opacity, 0, 1)})`;
}
