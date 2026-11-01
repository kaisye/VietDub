import clsx from "clsx";
import { memo, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { EditorSegment, EditorSegmentPatch } from "../../../types";
import type { SelectMode } from "../useEditor";
import { clamp, MIN_CUE, round3 } from "../utils";
import { snapEdges, snapPoints, SNAP_PX } from "./snap";

export const SUB_LANE_H = 38;
export const VOICE_LANE_H = 38;
const MIN_VOICE = 0.3;
// Same tempo limits as the server's retime_voice: slower than 0.8× or faster than 2× sounds broken.
const MIN_TEMPO = 0.8;
const MAX_TEMPO = 2;

type Live = {
  subs: Map<string, { start: number; end: number }>;
  voices: Map<string, { start: number; length: number }>;
};

type Marquee = { lane: "sub" | "voice"; from: number; to: number };

export type ClipLanesProps = {
  segments: EditorSegment[];
  duration: number;
  zoom: number;
  viewport: { left: number; width: number };
  selectedIds: Set<string>;
  snapping: boolean;
  linked: boolean;
  subLocked: boolean;
  voiceLocked: boolean;
  dubMuted: boolean;
  /** Read at drag start, so playhead ticks don't re-render the clips. */
  getPlayhead: () => number;
  onGuide: (t: number | null) => void;
  onSelect: (seg: EditorSegment, options: { jump?: boolean; mode?: SelectMode }) => void;
  onSelectIds: (ids: string[]) => void;
  onClearSelection: () => void;
  onSeek: (t: number) => void;
  onMove: (updates: EditorSegmentPatch[]) => void;
  onRetimeVoice: (id: string, patch: { audio_start?: number; audio_length?: number }) => void;
  onAddCue: (t: number) => void;
  onOpenCue: (seg: EditorSegment) => void;
  labels: { voiceSpeed: string; regenerated: string };
};

/** Subtitle + voice lanes: click / ⇧-click selection, marquee, drag to move, edge-trim, snapping. */
export const ClipLanes = memo(function ClipLanes(props: ClipLanesProps) {
  const {
    segments,
    duration,
    zoom,
    viewport,
    selectedIds,
    snapping,
    linked,
    subLocked,
    voiceLocked,
    dubMuted,
    getPlayhead,
    onGuide,
    onSelect,
    onSelectIds,
    onClearSelection,
    onSeek,
    onMove,
    onRetimeVoice,
    onAddCue,
    onOpenCue,
    labels,
  } = props;
  const [live, setLive] = useState<Live | null>(null);
  const [marquee, setMarquee] = useState<Marquee | null>(null);

  const from = viewport.left / zoom - 2;
  const to = (viewport.left + viewport.width) / zoom + 2;

  /** Runs a pointer drag; `onMove` gets the time delta, `onEnd` whether the pointer really moved. */
  const drag = (event: ReactPointerEvent, move: (dt: number) => void, end: (moved: boolean) => void) => {
    const startX = event.clientX;
    let moved = false;
    const onMovePointer = (pointer: PointerEvent) => {
      const dx = pointer.clientX - startX;
      if (!moved && Math.abs(dx) < 3) return;
      moved = true;
      move(dx / zoom);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMovePointer);
      window.removeEventListener("pointerup", onUp);
      setLive(null);
      onGuide(null);
      end(moved);
    };
    window.addEventListener("pointermove", onMovePointer);
    window.addEventListener("pointerup", onUp);
  };

  const snapper = (exclude: Set<string>) => {
    const points = snapping ? snapPoints(segments, exclude, getPlayhead(), duration) : [];
    const threshold = SNAP_PX / zoom;
    return (edges: number[]) => (snapping ? snapEdges(edges, points, threshold) : null);
  };

  // ---- subtitle clips --------------------------------------------------------
  const beginSub = (seg: EditorSegment, mode: "move" | "start" | "end") => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.shiftKey || event.metaKey || event.ctrlKey) {
      onSelect(seg, { jump: false, mode: "toggle" });
      return;
    }
    const wasSelected = selectedIds.has(seg.id);
    if (!wasSelected) onSelect(seg, { jump: false });
    if (subLocked) return;
    const group = mode === "move" && wasSelected && selectedIds.size > 1;
    const moving = group ? segments.filter((item) => selectedIds.has(item.id)) : [seg];
    const movingIds = new Set(moving.map((item) => item.id));
    // Neighbours that stay put bound the move; an existing overlap is tolerated rather than snapped.
    const bounds = (item: EditorSegment) => {
      const index = segments.indexOf(item);
      let prev: EditorSegment | undefined;
      for (let i = index - 1; i >= 0; i--) if (!movingIds.has(segments[i].id)) { prev = segments[i]; break; }
      let next: EditorSegment | undefined;
      for (let i = index + 1; i < segments.length; i++) if (!movingIds.has(segments[i].id)) { next = segments[i]; break; }
      return {
        floor: Math.min(item.start, prev ? prev.end : 0),
        ceiling: Math.max(item.end, next ? next.start : duration),
      };
    };
    let lower = -Infinity;
    let upper = Infinity;
    for (const item of moving) {
      const { floor, ceiling } = bounds(item);
      lower = Math.max(lower, floor - item.start);
      upper = Math.min(upper, ceiling - item.end);
    }
    const own = bounds(seg);
    const snap = snapper(movingIds);
    let shift = 0;
    let start = seg.start;
    let end = seg.end;

    drag(
      event,
      (dt) => {
        let guide: number | null = null;
        if (mode === "move") {
          shift = clamp(dt, lower, upper);
          const hit = snap(moving.flatMap((item) => [item.start + shift, item.end + shift]));
          if (hit && shift + hit.delta >= lower && shift + hit.delta <= upper) {
            shift += hit.delta;
            guide = hit.at;
          }
        } else if (mode === "start") {
          start = clamp(seg.start + dt, own.floor, seg.end - MIN_CUE);
          const hit = snap([start]);
          if (hit && start + hit.delta >= own.floor && start + hit.delta <= seg.end - MIN_CUE) {
            start += hit.delta;
            guide = hit.at;
          }
        } else {
          end = clamp(seg.end + dt, seg.start + MIN_CUE, own.ceiling);
          const hit = snap([end]);
          if (hit && end + hit.delta >= seg.start + MIN_CUE && end + hit.delta <= own.ceiling) {
            end += hit.delta;
            guide = hit.at;
          }
        }
        const subs = new Map<string, { start: number; end: number }>();
        const voices = new Map<string, { start: number; length: number }>();
        if (mode === "move") {
          for (const item of moving) {
            subs.set(item.id, { start: item.start + shift, end: item.end + shift });
            if (linked && item.audio_url) {
              voices.set(item.id, {
                start: clamp(item.audio_start + shift, 0, Math.max(0, duration - item.audio_duration)),
                length: item.audio_duration,
              });
            }
          }
        } else {
          subs.set(seg.id, { start, end });
        }
        setLive({ subs, voices });
        onGuide(guide);
      },
      (moved) => {
        if (!moved) {
          // A click on a clip of a multi-selection narrows the selection to it.
          if (wasSelected && selectedIds.size > 1) onSelect(seg, { jump: false });
          return;
        }
        if (mode === "move") {
          if (Math.abs(shift) < 0.0005) return;
          onMove(
            moving.map((item) => ({
              id: item.id,
              start: round3(item.start + shift),
              end: round3(item.end + shift),
              ...(linked && item.audio_url
                ? { audio_start: round3(clamp(item.audio_start + shift, 0, Math.max(0, duration - item.audio_duration))) }
                : {}),
            })),
          );
        } else {
          onMove([{ id: seg.id, start: round3(start), end: round3(end) }]);
        }
      },
    );
  };

  // ---- voice clips -----------------------------------------------------------
  // Body drag moves the take on its own; edges speed it up / slow it down.
  const beginVoice = (seg: EditorSegment, mode: "move" | "start" | "end") => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.shiftKey || event.metaKey || event.ctrlKey) {
      onSelect(seg, { jump: false, mode: "toggle" });
      return;
    }
    const wasSelected = selectedIds.has(seg.id);
    if (!wasSelected) onSelect(seg, { jump: false });
    if (voiceLocked) return;
    const end0 = seg.audio_start + seg.audio_duration;
    const natural = seg.audio_source_duration || seg.audio_duration;
    const minLength = Math.max(MIN_VOICE, natural / MAX_TEMPO);
    const maxLength = natural / MIN_TEMPO;
    const snap = snapper(new Set([seg.id]));
    let start = seg.audio_start;
    let length = seg.audio_duration;
    drag(
      event,
      (dt) => {
        let guide: number | null = null;
        if (mode === "move") {
          const limit = Math.max(0, duration - seg.audio_duration);
          start = clamp(seg.audio_start + dt, 0, limit);
          const hit = snap([start, start + seg.audio_duration]);
          if (hit && start + hit.delta >= 0 && start + hit.delta <= limit) {
            start += hit.delta;
            guide = hit.at;
          }
        } else if (mode === "start") {
          start = clamp(seg.audio_start + dt, Math.max(0, end0 - maxLength), end0 - minLength);
          const hit = snap([start]);
          if (hit && end0 - (start + hit.delta) >= minLength && end0 - (start + hit.delta) <= maxLength) {
            start += hit.delta;
            guide = hit.at;
          }
          length = end0 - start;
        } else {
          length = clamp(seg.audio_duration + dt, minLength, maxLength);
          const hit = snap([seg.audio_start + length]);
          if (hit && length + hit.delta >= minLength && length + hit.delta <= maxLength) {
            length += hit.delta;
            guide = hit.at;
          }
        }
        setLive({ subs: new Map(), voices: new Map([[seg.id, { start, length }]]) });
        onGuide(guide);
      },
      (moved) => {
        if (!moved) {
          if (wasSelected && selectedIds.size > 1) onSelect(seg, { jump: false });
          return;
        }
        onRetimeVoice(
          seg.id,
          mode === "move"
            ? { audio_start: round3(start) }
            : mode === "start"
              ? { audio_start: round3(start), audio_length: round3(length) }
              : { audio_length: round3(length) },
        );
      },
    );
  };

  // ---- empty lane: click seeks, drag draws a selection marquee ------------------
  const beginLane = (lane: "sub" | "voice") => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || event.target !== event.currentTarget) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const anchor = clamp((event.clientX - rect.left) / zoom, 0, duration);
    const additive = event.shiftKey || event.metaKey || event.ctrlKey;
    let range: Marquee | null = null;
    drag(
      event,
      (dt) => {
        const other = clamp(anchor + dt, 0, duration);
        range = { lane, from: Math.min(anchor, other), to: Math.max(anchor, other) };
        setMarquee(range);
      },
      (moved) => {
        setMarquee(null);
        if (!moved || !range) {
          onSeek(anchor);
          if (!additive) onClearSelection();
          return;
        }
        const { from: a, to: b } = range;
        const hits = segments
          .filter((seg) =>
            lane === "sub"
              ? seg.end > a && seg.start < b
              : !!seg.audio_url && seg.audio_start + seg.audio_duration > a && seg.audio_start < b,
          )
          .map((seg) => seg.id);
        onSelectIds(additive ? [...new Set([...selectedIds, ...hits])] : hits);
      },
    );
  };

  const numbers = new Map(segments.map((seg, index) => [seg.id, index + 1]));

  return (
    <>
      {/* Subtitle lane */}
      <div
        className="relative border-b border-white/[0.06]"
        style={{ height: SUB_LANE_H }}
        onPointerDown={beginLane("sub")}
        onDoubleClick={(event) => {
          if (subLocked || event.target !== event.currentTarget) return;
          const rect = event.currentTarget.getBoundingClientRect();
          onAddCue((event.clientX - rect.left) / zoom);
        }}
      >
        {segments.map((seg) => {
          const geometry = live?.subs.get(seg.id);
          const start = geometry ? geometry.start : seg.start;
          const end = geometry ? geometry.end : seg.end;
          if (end < from || start > to) return null;
          const width = Math.max(2, (end - start) * zoom);
          const isSelected = selectedIds.has(seg.id);
          const bad = seg.issues.includes("untranslated") || seg.issues.includes("empty");
          return (
            <div
              key={seg.id}
              className={clsx(
                "group absolute bottom-[5px] top-[5px] select-none overflow-hidden rounded-[5px] border text-[11px] leading-none text-white",
                subLocked ? "cursor-default" : "cursor-grab active:cursor-grabbing",
                bad
                  ? "border-[color-mix(in_srgb,var(--danger)_70%,black)] bg-[color-mix(in_srgb,var(--danger)_80%,var(--navy-900))]"
                  : "border-[color-mix(in_srgb,var(--primary)_60%,black)] bg-[color-mix(in_srgb,var(--primary)_82%,var(--navy-900))]",
                isSelected && "z-[2] ring-2 ring-white ring-offset-0 brightness-110",
                geometry && "z-[3] opacity-90 shadow-lg",
              )}
              style={{ left: start * zoom, width, touchAction: "none" }}
              title={seg.text}
              onPointerDown={beginSub(seg, "move")}
              onDoubleClick={(event) => {
                event.stopPropagation();
                onOpenCue(seg);
              }}
            >
              {width > 28 ? (
                <span className="pointer-events-none flex h-full items-center gap-1.5 truncate px-2">
                  <span className="font-mono text-[10px] text-white/60">{numbers.get(seg.id)}</span>
                  {width > 60 ? <span className="truncate">{seg.text || "—"}</span> : null}
                </span>
              ) : null}
              {seg.issues.length ? (
                <span
                  className={clsx(
                    "pointer-events-none absolute right-1 top-1 h-1.5 w-1.5 rounded-full",
                    bad ? "bg-white" : "bg-[var(--warning-border)]",
                  )}
                />
              ) : null}
              {width > 14 && !subLocked ? (
                <>
                  <div
                    className="absolute inset-y-0 left-0 w-[7px] cursor-ew-resize rounded-l-[4px] group-hover:bg-white/35"
                    onPointerDown={beginSub(seg, "start")}
                  />
                  <div
                    className="absolute inset-y-0 right-0 w-[7px] cursor-ew-resize rounded-r-[4px] group-hover:bg-white/35"
                    onPointerDown={beginSub(seg, "end")}
                  />
                </>
              ) : null}
            </div>
          );
        })}
        {marquee?.lane === "sub" ? <MarqueeBox marquee={marquee} zoom={zoom} /> : null}
      </div>

      {/* Voice (dub) lane */}
      <div
        className={clsx("relative border-b border-white/[0.06]", dubMuted && "opacity-45")}
        style={{ height: VOICE_LANE_H }}
        onPointerDown={beginLane("voice")}
      >
        {segments.map((seg) => {
          if (!seg.audio_url) return null;
          const geometry = live?.voices.get(seg.id);
          const start = geometry ? geometry.start : seg.audio_start;
          const length = geometry ? geometry.length : seg.audio_duration;
          if (start + length < from || start > to) return null;
          const width = Math.max(2, length * zoom);
          const isSelected = selectedIds.has(seg.id);
          const overrun = seg.issues.includes("overrun");
          const stale = seg.issues.includes("stale_audio");
          const regenerated = seg.audio_url.includes("/editor/");
          const tempo = seg.voice_slot && seg.audio_source_duration ? seg.audio_source_duration / seg.audio_duration : null;
          const liveTempo = geometry && geometry.length !== seg.audio_duration
            ? (seg.audio_source_duration || seg.audio_duration) / geometry.length
            : null;
          return (
            <div
              key={seg.id}
              className={clsx(
                "group absolute bottom-[5px] top-[5px] select-none overflow-hidden rounded-[5px] border text-[10.5px] leading-none text-white",
                voiceLocked ? "cursor-default" : "cursor-grab active:cursor-grabbing",
                overrun
                  ? "border-[color-mix(in_srgb,var(--danger)_70%,black)] bg-[color-mix(in_srgb,var(--danger)_75%,var(--navy-900))]"
                  : "border-[color-mix(in_srgb,var(--accent-cyan)_60%,black)] bg-[color-mix(in_srgb,var(--accent-cyan)_78%,var(--navy-900))]",
                seg.muted && "opacity-35",
                isSelected && "z-[2] ring-2 ring-white brightness-110",
                geometry && "z-[3] opacity-90 shadow-lg",
              )}
              style={{
                left: start * zoom,
                width,
                touchAction: "none",
                // A faint speech-like texture so voice clips read as audio at a glance.
                backgroundImage:
                  "repeating-linear-gradient(90deg, rgba(255,255,255,0.13) 0 2px, transparent 2px 5px)",
                backgroundSize: "100% 46%",
                backgroundPosition: "0 50%",
                backgroundRepeat: "no-repeat",
              }}
              title={`${length.toFixed(2)}s${tempo ? ` · ${labels.voiceSpeed} ×${tempo.toFixed(2)}` : ""}${regenerated ? ` · ${labels.regenerated}` : ""}`}
              onPointerDown={beginVoice(seg, "move")}
              onDoubleClick={(event) => {
                event.stopPropagation();
                onOpenCue(seg);
              }}
            >
              {stale ? <span className="pointer-events-none absolute inset-x-0 top-0 h-[3px] bg-[var(--warning-border)]" /> : null}
              {width > 44 ? (
                <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 whitespace-nowrap font-mono">
                  {liveTempo ? `${length.toFixed(2)}s · ×${liveTempo.toFixed(2)}` : tempo ? `×${tempo.toFixed(2)}` : ""}
                </span>
              ) : null}
              {width > 14 && !voiceLocked ? (
                <>
                  <div
                    className="absolute inset-y-0 left-0 w-[7px] cursor-ew-resize group-hover:bg-white/35"
                    onPointerDown={beginVoice(seg, "start")}
                  />
                  <div
                    className="absolute inset-y-0 right-0 w-[7px] cursor-ew-resize group-hover:bg-white/35"
                    onPointerDown={beginVoice(seg, "end")}
                  />
                </>
              ) : null}
            </div>
          );
        })}
        {marquee?.lane === "voice" ? <MarqueeBox marquee={marquee} zoom={zoom} /> : null}
      </div>
    </>
  );
});

function MarqueeBox({ marquee, zoom }: { marquee: Marquee; zoom: number }) {
  return (
    <div
      className="pointer-events-none absolute inset-y-[2px] z-[4] rounded-[3px] border border-white/70 bg-white/10"
      style={{ left: marquee.from * zoom, width: Math.max(1, (marquee.to - marquee.from) * zoom) }}
    />
  );
}
