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
  bilingual_enabled: false,
  bilingual_font_scale: 0.72,
  bilingual_color: "#444444",
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

  // Horizontal anchor AND multi-line text alignment must mirror the renderer
  // (_srt_to_ass uses \an4/\an5/\an6): left-anchored + left-aligned for
  // position_x <= 0.33 (text grows right), right-anchored + right-aligned for
  // >= 0.67 (text grows left), and centered in between. \anN drives both the
  // anchor edge and how wrapped lines align inside the block, so the preview
  // must do the same — always centering misplaced off-center multi-line cues.
  const positionX = clamp(normalized.position_x, 0.05, 0.95);
  const anchor = positionX <= 0.33 ? "left" : positionX >= 0.67 ? "right" : "center";
  const anchorX = anchor === "left" ? "0%" : anchor === "right" ? "-100%" : "-50%";

  // Wrap width must match the renderer's available text width, which is
  // position-dependent because the ASS margins become one-sided once the
  // subtitle leaves the center band (see _subtitle_chars_per_line):
  //   position_x < 0.45 -> (1 - position_x);  > 0.55 -> position_x;  else full.
  // A fixed 82% cap made centered cues wrap earlier than the render and
  // off-center cues wrap at the wrong width, so line breaks diverged.
  const maxWidthFraction =
    positionX < 0.45 ? 1 - positionX : positionX > 0.55 ? positionX : 1;

  return {
    left: `${positionX * 100}%`,
    top: `${positionY * 100}%`,
    transform: `translate(${anchorX}, -50%)`,
    textAlign: anchor,
    // max-content keeps the box at its natural width for short cues; the
    // position-aware maxWidth caps long cues at the same width the renderer
    // wraps at. Auto width would instead collapse against the right frame edge.
    width: "max-content",
    maxWidth: `${Math.round(maxWidthFraction * 100)}%`,
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

/** Inline style for the bilingual source-language line drawn under the translation.
 *  Mirrors the renderer's `{\fs..\1c..}` override: same font, scaled size, own colour. */
export function subtitleSecondaryPreviewStyle(
  style: Partial<SubtitleStyle> | undefined,
): CSSProperties {
  const normalized = { ...DEFAULT_SUBTITLE_STYLE, ...(style ?? {}) };
  const scale = clamp(normalized.bilingual_font_scale ?? 0.72, 0.4, 1);
  return {
    fontSize: `clamp(6px, ${(clamp(normalized.font_size, 8, 72) / 5.12) * scale}cqw, 96px)`,
    color: normalized.bilingual_color ?? "#444444",
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
