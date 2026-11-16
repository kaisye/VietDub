import clsx from "clsx";
import { useMemo, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { EditorTextOverlay } from "../../../types";
import { SNAP_PX, snapEdges } from "../timeline/snap";
import { clamp } from "../utils";
import { TEXT_MIN_LENGTH } from "./model";

const ROW_H = 26;

/** Boxes that overlap in time go on separate rows (greedy, by start). */
function layoutRows(items: EditorTextOverlay[]): Map<string, number> {
  const rowEnds: number[] = [];
  const rows = new Map<string, number>();
  for (const item of [...items].sort((a, b) => a.start - b.start)) {
    let row = rowEnds.findIndex((end) => end <= item.start + 1e-6);
    if (row < 0) row = rowEnds.push(0) - 1;
    rowEnds[row] = item.end;
    rows.set(item.id, row);
  }
  return rows;
}

export function textLaneHeight(items: EditorTextOverlay[]): number {
  const rows = layoutRows(items);
  return Math.max(1, ...[...rows.values()].map((row) => row + 1)) * ROW_H + 4;
}

type Mode = "move" | "start" | "end";

/** Timeline lane of text boxes: click selects, drag moves, edges trim, double-click empty space adds one. */
export function TextLane({
  items,
  duration,
  zoom,
  selectedId,
  snapping,
  snapTimes,
  getPlayhead,
  onGuide,
  onSelect,
  onSeek,
  onChange,
  onAdd,
}: {
  items: EditorTextOverlay[];
  duration: number;
  zoom: number;
  selectedId: string | null;
  snapping: boolean;
  /** Other edges to stick to (cues); the playhead and the other boxes are added here. */
  snapTimes: number[];
  getPlayhead: () => number;
  onGuide: (t: number | null) => void;
  onSelect: (id: string) => void;
  onSeek: (t: number) => void;
  onChange: (items: EditorTextOverlay[], kind: string) => void;
  onAdd: (at: number) => void;
}) {
  const [live, setLive] = useState<{ id: string; start: number; end: number } | null>(null);
  const rows = useMemo(() => layoutRows(items), [items]);
  const height = textLaneHeight(items);

  const begin = (item: EditorTextOverlay, mode: Mode) => (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(item.id);
    const points = [0, duration, getPlayhead(), ...snapTimes];
    for (const other of items) if (other.id !== item.id) points.push(other.start, other.end);
    points.sort((a, b) => a - b);
    const threshold = SNAP_PX / zoom;
    const snap = (edges: number[]) => (snapping ? snapEdges(edges, points, threshold) : null);
    const originX = event.clientX;
    const length = item.end - item.start;
    let latest = { start: item.start, end: item.end };
    let moved = false;

    const onMove = (move: PointerEvent) => {
      const dt = (move.clientX - originX) / zoom;
      if (!moved && Math.abs(move.clientX - originX) < 3) return;
      moved = true;
      let guide: number | null = null;
      if (mode === "move") {
        let start = clamp(item.start + dt, 0, duration - length);
        const hit = snap([start, start + length]);
        if (hit && start + hit.delta >= 0 && start + hit.delta + length <= duration) {
          start += hit.delta;
          guide = hit.at;
        }
        latest = { start, end: start + length };
      } else if (mode === "start") {
        let start = clamp(item.start + dt, 0, item.end - TEXT_MIN_LENGTH);
        const hit = snap([start]);
        if (hit && start + hit.delta >= 0 && start + hit.delta <= item.end - TEXT_MIN_LENGTH) {
          start += hit.delta;
          guide = hit.at;
        }
        latest = { start, end: item.end };
      } else {
        let end = clamp(item.end + dt, item.start + TEXT_MIN_LENGTH, duration);
        const hit = snap([end]);
        if (hit && end + hit.delta >= item.start + TEXT_MIN_LENGTH && end + hit.delta <= duration) {
          end += hit.delta;
          guide = hit.at;
        }
        latest = { start: item.start, end };
      }
      onGuide(guide);
      setLive({ id: item.id, ...latest });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      onGuide(null);
      setLive(null);
      if (!moved) return;
      const round = (value: number) => Math.round(value * 1000) / 1000;
      onChange(
        items.map((other) => (other.id === item.id ? { ...other, start: round(latest.start), end: round(latest.end) } : other)),
        mode === "move" ? "text-move" : "text-trim",
      );
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const timeAt = (event: React.MouseEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return clamp((event.clientX - rect.left) / zoom, 0, duration);
  };

  return (
    <div
      className="relative border-b border-white/[0.06]"
      style={{ height }}
      onPointerDown={(event) => {
        if (event.button === 0 && event.target === event.currentTarget) onSeek(timeAt(event));
      }}
      onDoubleClick={(event) => {
        if (event.target === event.currentTarget) onAdd(timeAt(event));
      }}
    >
      {items.map((item) => {
        const span = live?.id === item.id ? live : item;
        const selected = item.id === selectedId;
        return (
          <div
            key={item.id}
            title={item.text}
            onPointerDown={begin(item, "move")}
            className={clsx(
              "group absolute flex cursor-grab items-center overflow-hidden rounded-[4px] border px-2 text-[10.5px] font-medium active:cursor-grabbing",
              selected
                ? "z-[2] border-white bg-[#f59e0b] text-[#1f1300] shadow-[0_0_0_1px_white]"
                : "border-[#f59e0b]/60 bg-[#f59e0b]/25 text-[#fde4b4] hover:bg-[#f59e0b]/35",
            )}
            style={{
              left: span.start * zoom,
              width: Math.max(6, (span.end - span.start) * zoom),
              top: 2 + (rows.get(item.id) ?? 0) * ROW_H,
              height: ROW_H - 4,
            }}
          >
            <span className="pointer-events-none truncate">T · {item.text.replace(/\n/g, " ")}</span>
            <span
              className="absolute inset-y-0 left-0 w-1.5 cursor-ew-resize opacity-0 group-hover:opacity-100 bg-white/40"
              onPointerDown={begin(item, "start")}
            />
            <span
              className="absolute inset-y-0 right-0 w-1.5 cursor-ew-resize opacity-0 group-hover:opacity-100 bg-white/40"
              onPointerDown={begin(item, "end")}
            />
          </div>
        );
      })}
    </div>
  );
}
