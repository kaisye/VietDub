import type { CSSProperties } from "react";
import type { EditorTextOverlay, EditorWorkspace } from "../../../types";
import { hexAlpha } from "../../EditorStyle";

export const TEXT_MIN_LENGTH = 0.2;
const DEFAULT_LENGTH = 3;

/** A new box at `at` (3 s, or up to the end of the video), centred near the top. */
export function makeTextOverlay(at: number, duration: number, text: string): EditorTextOverlay {
  const start = Math.max(0, Math.min(at, duration - TEXT_MIN_LENGTH));
  return {
    id: `txt-${Math.random().toString(36).slice(2, 10)}`,
    text,
    start: Math.round(start * 1000) / 1000,
    end: Math.round(Math.min(duration, start + DEFAULT_LENGTH) * 1000) / 1000,
    x: 0.5,
    y: 0.2,
    size: 0.07,
    color: "#FFFFFF",
    bg_color: "#000000",
    bg_opacity: 0.6,
    bold: true,
    align: "center",
    font_name: "",
  };
}

export const overlayVisible = (item: EditorTextOverlay, t: number) => t >= item.start && t < item.end;

/**
 * CSS for a box inside the preview frame, mirroring write_overlays_ass: font size is a fraction
 * of the frame height (converted to container width units), the box is a per-line background.
 */
export function overlayStyle(item: EditorTextOverlay, workspace: EditorWorkspace): { box: CSSProperties; line: CSSProperties } {
  const aspect = (workspace.height || 9) / (workspace.width || 16);
  const fontCqw = item.size * aspect * 100;
  const anchorX = item.align === "left" ? "0%" : item.align === "right" ? "-100%" : "-50%";
  const boxed = item.bg_opacity > 0;
  return {
    box: {
      left: `${item.x * 100}%`,
      top: `${item.y * 100}%`,
      transform: `translate(${anchorX}, -50%)`,
      textAlign: item.align,
      fontFamily: `"${item.font_name || workspace.style.font_name}", Arial, sans-serif`,
      fontWeight: item.bold ? 700 : 400,
      fontSize: `${fontCqw}cqw`,
      color: item.color,
    },
    line: boxed
      ? {
          background: hexAlpha(item.bg_color, item.bg_opacity),
          padding: "0.3em",
          boxDecorationBreak: "clone",
          WebkitBoxDecorationBreak: "clone",
        }
      : { textShadow: "0.05em 0.05em 0 rgba(0, 0, 0, 0.55)" },
  };
}
