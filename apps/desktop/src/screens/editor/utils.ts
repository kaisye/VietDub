import type { EditorEditableState } from "../../api";
import type { EditorIssue, EditorSegment, EditorWorkspace } from "../../types";

/** `L("tiếng Việt", "English")` — the editor's inline translation helper (driven by the i18n language). */
export type Translate = (vi: string, en: string) => string;

export const ISSUE_LABEL: Record<EditorIssue, [string, string]> = {
  overrun: ["Audio tràn", "Audio overrun"],
  stale_audio: ["Audio cũ", "Stale audio"],
  no_audio: ["Thiếu audio", "No audio"],
  untranslated: ["Chưa dịch", "Untranslated"],
  empty: ["Câu rỗng", "Empty"],
};

/** Preview frame step for ←/→ (the source frame rate is not known; 30 fps is the common case). */
export const FRAME = 1 / 30;
export const MIN_CUE = 0.2;
export const ZOOM_MIN = 1;
export const ZOOM_MAX = 400;

export const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
export const round3 = (value: number) => Math.round(value * 1000) / 1000;

export function formatTime(seconds: number, precise = true): string {
  const safe = Math.max(0, seconds);
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = safe % 60;
  const sec = precise ? s.toFixed(2).padStart(5, "0") : String(Math.floor(s)).padStart(2, "0");
  return `${h > 0 ? `${h}:` : ""}${String(m).padStart(2, "0")}:${sec}`;
}

/** NLE-style timecode HH:MM:SS:FF at 30 fps. */
export function formatTimecode(seconds: number): string {
  const safe = Math.max(0, seconds);
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = Math.floor(safe % 60);
  const f = Math.floor((safe - Math.floor(safe)) * 30 + 1e-6);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}:${pad(f)}`;
}

export function segmentAt(segments: EditorSegment[], time: number, key: "subtitle" | "audio"): EditorSegment | null {
  // Segments are sorted by start; binary-search the last one starting before `time`.
  let lo = 0;
  let hi = segments.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const start = key === "audio" ? segments[mid].audio_start : segments[mid].start;
    if (start <= time) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  for (let i = found; i >= Math.max(0, found - 3); i--) {
    const seg = segments[i];
    if (key === "audio") {
      if (seg.audio_url && !seg.muted && time < seg.audio_start + seg.audio_duration) return seg;
    } else if (time < seg.end) {
      return seg;
    }
  }
  return null;
}

export function editableState(workspace: EditorWorkspace): EditorEditableState {
  return {
    segments: workspace.segments,
    blur: workspace.blur,
    audio: workspace.audio,
    style: workspace.style,
    max_lines: workspace.max_lines,
    original_regions: workspace.original_regions,
    audio_tracks: workspace.audio_tracks ?? [],
    text_overlays: workspace.text_overlays ?? [],
  };
}

export const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Per-viewer UI preferences (panel sizes, toggles). Never required for correctness. */
export function loadPref<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (raw) return { ...fallback, ...JSON.parse(raw) } as T;
  } catch {
    /* defaults */
  }
  return fallback;
}

export function savePref(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* per-viewer convenience only */
  }
}
