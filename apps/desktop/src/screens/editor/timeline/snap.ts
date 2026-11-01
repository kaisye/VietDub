import type { EditorSegment } from "../../../types";

/** Pixels within which a dragged edge sticks to a snap point. */
export const SNAP_PX = 8;

/** Sorted snap points: timeline ends, the playhead and every clip edge except the dragged ones. */
export function snapPoints(segments: EditorSegment[], exclude: Set<string>, playhead: number, duration: number): number[] {
  const points = [0, duration, playhead];
  for (const seg of segments) {
    if (exclude.has(seg.id)) continue;
    points.push(seg.start, seg.end);
    if (seg.audio_url) points.push(seg.audio_start, seg.audio_start + seg.audio_duration);
  }
  return points.sort((a, b) => a - b);
}

function nearest(points: number[], value: number): number {
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid] < value) lo = mid + 1;
    else hi = mid;
  }
  const after = points[lo];
  const before = lo > 0 ? points[lo - 1] : after;
  return Math.abs(before - value) <= Math.abs(after - value) ? before : after;
}

/**
 * Smallest correction that puts one of `edges` on a snap point, if any is within
 * `threshold` seconds. `at` is the snapped time (drawn as a guide line).
 */
export function snapEdges(edges: number[], points: number[], threshold: number): { delta: number; at: number } | null {
  if (!points.length) return null;
  let best: { delta: number; at: number } | null = null;
  for (const edge of edges) {
    const point = nearest(points, edge);
    const delta = point - edge;
    if (Math.abs(delta) <= threshold && (!best || Math.abs(delta) < Math.abs(best.delta))) best = { delta, at: point };
  }
  return best;
}
