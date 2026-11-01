import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { EditorRegion } from "../types";

export type Waveform = { rate: number; peaks: number[] };

const MIN_REGION = 0.1;

/** 0–1 weight of the region's volume at time t (1 = full region volume), like the renderer's clip(min(...)). */
export function regionEnvelope(region: EditorRegion, t: number): number {
  if (t < region.start || t > region.end) return 0;
  let weight = 1;
  if (region.fade_in > 0) weight = Math.min(weight, (t - region.start) / region.fade_in);
  if (region.fade_out > 0) weight = Math.min(weight, (region.end - t) / region.fade_out);
  return Math.max(0, Math.min(1, weight));
}

/** Effective original-audio volume (percent) at time t. */
export function originalVolumeAt(regions: EditorRegion[], base: number, t: number): number {
  const region = regions.find((item) => t >= item.start && t <= item.end);
  return region ? base + (region.volume - base) * regionEnvelope(region, t) : base;
}

/** Original-audio lane: waveform plus volume regions you create by dragging and trim by their edges. */
export default function OriginalTrack({
  duration,
  zoom,
  viewport,
  waveform,
  regions,
  baseVolume,
  selectedRegionId,
  onSelectRegion,
  onRegionsChange,
  onSeek,
  locked = false,
}: {
  duration: number;
  zoom: number;
  viewport: { left: number; width: number };
  waveform: Waveform | null;
  regions: EditorRegion[];
  baseVolume: number;
  selectedRegionId: string | null;
  onSelectRegion: (id: string | null) => void;
  onRegionsChange: (regions: EditorRegion[], selectId?: string) => void;
  onSeek: (t: number) => void;
  /** Timeline track lock: regions can't be created or edited, clicks only seek. */
  locked?: boolean;
}) {
  const laneRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Live geometry while dragging; committed on pointer up.
  const [draft, setDraft] = useState<EditorRegion | null>(null);

  const shown = draft ? [...regions.filter((region) => region.id !== draft.id), draft] : regions;

  // Only the visible window is drawn: the full timeline can be far wider than a canvas allows.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = window.devicePixelRatio || 1;
    const cssWidth = Math.max(1, viewport.width);
    const cssHeight = canvas.clientHeight || 44;
    canvas.width = Math.round(cssWidth * ratio);
    canvas.height = Math.round(cssHeight * ratio);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);
    if (!waveform || !waveform.peaks.length) return;
    const { rate, peaks } = waveform;
    const middle = cssHeight / 2;
    let regionIndex = 0;
    const sorted = [...shown].sort((a, b) => a.start - b.start);
    for (let x = 0; x < cssWidth; x++) {
      const t0 = (viewport.left + x) / zoom;
      const t1 = (viewport.left + x + 1) / zoom;
      const from = Math.floor(t0 * rate);
      const to = Math.max(from + 1, Math.floor(t1 * rate));
      let peak = 0;
      for (let i = from; i < to && i < peaks.length; i++) peak = Math.max(peak, peaks[i]);
      if (!peak) continue;
      while (regionIndex < sorted.length && sorted[regionIndex].end <= t0) regionIndex++;
      const region = sorted[regionIndex] && sorted[regionIndex].start <= t0 ? sorted[regionIndex] : null;
      // Height follows the effective volume so boosted/cut regions and fades are visible at a glance.
      const volume = region ? baseVolume + (region.volume - baseVolume) * regionEnvelope(region, t0) : baseVolume;
      const scale = Math.min(1.6, Math.sqrt(volume / 100));
      const half = Math.min(middle, (peak / 255) * middle * Math.max(0.08, scale));
      ctx.fillStyle = region ? "rgba(147, 197, 253, 0.95)" : "rgba(203, 213, 225, 0.5)";
      ctx.fillRect(x, middle - half, 1, half * 2);
    }
  }, [waveform, viewport.left, viewport.width, zoom, shown, baseVolume]);

  const timeAt = (clientX: number) => {
    const rect = laneRef.current?.getBoundingClientRect();
    return rect ? Math.min(duration, Math.max(0, (clientX - rect.left) / zoom)) : 0;
  };

  const bounds = (id: string | null) => {
    // Neighbouring regions limit how far a region may grow.
    const others = regions.filter((region) => region.id !== id);
    return (t: number) => ({
      min: Math.max(0, ...others.filter((region) => region.end <= t).map((region) => region.end)),
      max: Math.min(duration, ...others.filter((region) => region.start >= t).map((region) => region.start)),
    });
  };

  const track = (
    event: ReactPointerEvent,
    onMove: (t: number) => void,
    onUp: (moved: boolean) => void,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    let moved = false;
    const move = (pointer: PointerEvent) => {
      if (Math.abs(pointer.clientX - startX) > 3) moved = true;
      if (moved) onMove(timeAt(pointer.clientX));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      onUp(moved);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const commit = (region: EditorRegion | null) => {
    setDraft(null);
    if (!region || region.end - region.start < MIN_REGION) return;
    onRegionsChange(
      [...regions.filter((item) => item.id !== region.id), region].sort((a, b) => a.start - b.start),
      region.id,
    );
  };

  // Drag on empty lane = new region; a plain click seeks.
  const onLaneDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    if (locked) {
      onSelectRegion(null);
      onSeek(timeAt(event.clientX));
      return;
    }
    const anchor = timeAt(event.clientX);
    const limit = bounds(null)(anchor);
    const id = `reg-${Math.random().toString(36).slice(2, 10)}`;
    let latest: EditorRegion | null = null;
    track(
      event,
      (t) => {
        const clamped = Math.min(limit.max, Math.max(limit.min, t));
        latest = {
          id,
          start: Math.min(anchor, clamped),
          end: Math.max(anchor, clamped),
          volume: 200,
          fade_in: 0,
          fade_out: 0,
        };
        setDraft(latest);
      },
      (moved) => {
        if (moved) commit(latest);
        else {
          onSelectRegion(null);
          onSeek(anchor);
        }
      },
    );
  };

  const onEdgeDown = (region: EditorRegion, edge: "start" | "end") => (event: ReactPointerEvent<HTMLDivElement>) => {
    onSelectRegion(region.id);
    const limit = bounds(region.id)(edge === "start" ? region.end : region.start);
    let latest: EditorRegion = region;
    track(
      event,
      (t) => {
        latest =
          edge === "start"
            ? { ...region, start: Math.min(region.end - MIN_REGION, Math.max(limit.min, t)) }
            : { ...region, end: Math.max(region.start + MIN_REGION, Math.min(limit.max, t)) };
        // Trimming never leaves fades longer than the region.
        const length = latest.end - latest.start;
        const fadeIn = Math.min(latest.fade_in, length);
        latest = { ...latest, fade_in: fadeIn, fade_out: Math.min(latest.fade_out, length - fadeIn) };
        setDraft(latest);
      },
      (moved) => (moved ? commit(latest) : setDraft(null)),
    );
  };

  // Fade handles sit on the top corners; dragging inwards lengthens the fade.
  const onFadeDown = (region: EditorRegion, side: "in" | "out") => (event: ReactPointerEvent<HTMLDivElement>) => {
    onSelectRegion(region.id);
    let latest: EditorRegion = region;
    track(
      event,
      (t) => {
        const length = region.end - region.start;
        latest =
          side === "in"
            ? { ...region, fade_in: Math.max(0, Math.min(length - region.fade_out, t - region.start)) }
            : { ...region, fade_out: Math.max(0, Math.min(length - region.fade_in, region.end - t)) };
        setDraft(latest);
      },
      (moved) => (moved ? commit(latest) : setDraft(null)),
    );
  };

  return (
    <div className="editor-lane original" ref={laneRef} onPointerDown={onLaneDown}>
      <canvas ref={canvasRef} className="editor-wave" style={{ left: viewport.left, width: viewport.width }} />
      {shown.map((region) => (
        <div
          key={region.id}
          className={`editor-region${region.id === selectedRegionId ? " selected" : ""}`}
          style={{
            left: region.start * zoom,
            width: Math.max(4, (region.end - region.start) * zoom),
            pointerEvents: locked ? "none" : undefined,
          }}
          onPointerDown={(event) => {
            event.stopPropagation();
            onSelectRegion(region.id);
          }}
        >
          <FadeShape region={region} zoom={zoom} />
          <div className="editor-region-edge start" onPointerDown={onEdgeDown(region, "start")} />
          <span className="editor-region-label">{Math.round(region.volume)}%</span>
          <div className="editor-region-edge end" onPointerDown={onEdgeDown(region, "end")} />
          <div
            className="editor-fade-handle in"
            style={{ left: region.fade_in * zoom }}
            title="Kéo để tăng dần"
            onPointerDown={onFadeDown(region, "in")}
          />
          <div
            className="editor-fade-handle out"
            style={{ right: region.fade_out * zoom }}
            title="Kéo để giảm dần"
            onPointerDown={onFadeDown(region, "out")}
          />
        </div>
      ))}
    </div>
  );
}

/** Darkens the faded corners of a region so the ramps read like a DAW clip. */
function FadeShape({ region, zoom }: { region: EditorRegion; zoom: number }) {
  const width = Math.max(4, (region.end - region.start) * zoom);
  const fadeIn = region.fade_in * zoom;
  const fadeOut = region.fade_out * zoom;
  if (!fadeIn && !fadeOut) return null;
  return (
    <svg className="editor-fade-shape" viewBox={`0 0 ${width} 100`} preserveAspectRatio="none">
      {fadeIn ? <polygon points={`0,0 ${fadeIn},0 0,100`} /> : null}
      {fadeOut ? <polygon points={`${width - fadeOut},0 ${width},0 ${width},100`} /> : null}
      <polyline
        points={`0,100 ${fadeIn},0 ${width - fadeOut},0 ${width},100`}
        fill="none"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
