import clsx from "clsx";
import { Lock, Music2, Plus, Trash2, Unlock, Volume2, VolumeX, Waves } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent as ReactDragEvent, PointerEvent as ReactPointerEvent } from "react";
import { useT } from "../../../i18n";
import type { EditorAudioClip, EditorAudioTrack, EditorSegment, MusicAsset } from "../../../types";
import { snapEdges, snapPoints, SNAP_PX } from "../timeline/snap";
import type { SelectMode } from "../useEditor";
import { clamp } from "../utils";
import { usePeaks } from "./library";
import {
  ASSET_MIME,
  clipEnd,
  clipEnvelope,
  filePosition,
  fits,
  maxClipLength,
  MIN_CLIP,
  round3,
  type DuckingCache,
} from "./model";

export const AUDIO_LANE_H = 48;
export const ADD_ROW_H = 30;

export type DroppedAsset = Pick<MusicAsset, "id" | "title" | "duration" | "kind">;

/** Reads the library asset carried by a drag from the Music tab. */
export function readDroppedAsset(event: ReactDragEvent): DroppedAsset | null {
  try {
    const raw = event.dataTransfer.getData(ASSET_MIME);
    return raw ? (JSON.parse(raw) as DroppedAsset) : null;
  } catch {
    return null;
  }
}

const accepts = (event: ReactDragEvent) => event.dataTransfer.types.includes(ASSET_MIME);

// ---- headers ----------------------------------------------------------------------------

export function AudioTrackHeaders({
  tracks,
  solo,
  onSolo,
  onPatch,
  onDelete,
  onAdd,
}: {
  tracks: EditorAudioTrack[];
  solo: Set<string>;
  onSolo: (id: string) => void;
  onPatch: (id: string, patch: Partial<EditorAudioTrack>) => void;
  onDelete: (id: string) => void;
  onAdd: () => void;
}) {
  const { t } = useT();
  return (
    <>
      {tracks.map((track) => (
        <TrackHeader key={track.id} track={track} soloed={solo.has(track.id)} onSolo={onSolo} onPatch={onPatch} onDelete={onDelete} />
      ))}
      <div className="flex items-center border-b border-white/[0.06] px-2" style={{ height: ADD_ROW_H }}>
        <button
          type="button"
          onClick={onAdd}
          className="flex h-6 w-full items-center gap-1.5 rounded-[5px] px-1.5 text-[11.5px] font-medium text-white/60 transition-colors hover:bg-white/10 hover:text-white"
        >
          <Plus size={13} />
          {t.mu_add_track}
        </button>
      </div>
    </>
  );
}

function TrackHeader({
  track,
  soloed,
  onSolo,
  onPatch,
  onDelete,
}: {
  track: EditorAudioTrack;
  soloed: boolean;
  onSolo: (id: string) => void;
  onPatch: (id: string, patch: Partial<EditorAudioTrack>) => void;
  onDelete: (id: string) => void;
}) {
  const { t } = useT();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(track.name);
  const [volume, setVolume] = useState(track.volume);
  useEffect(() => setName(track.name), [track.name]);
  useEffect(() => setVolume(track.volume), [track.volume]);
  const commitName = () => {
    setEditing(false);
    const value = name.trim();
    if (value && value !== track.name) onPatch(track.id, { name: value });
    else setName(track.name);
  };
  const commitVolume = () => {
    if (volume !== track.volume) onPatch(track.id, { volume });
  };
  return (
    <div
      className="flex flex-col justify-center gap-0.5 border-b border-white/[0.06] pl-3 pr-2 text-white/80"
      style={{ height: AUDIO_LANE_H }}
      data-testid="audio-track-header"
    >
      <div className="flex items-center gap-1.5">
        <span className="shrink-0 text-white/55">{track.kind === "sfx" ? <Waves size={13} /> : <Music2 size={13} />}</span>
        {editing ? (
          <input
            autoFocus
            value={name}
            aria-label={t.mu_track_name}
            onChange={(event) => setName(event.target.value)}
            onBlur={commitName}
            onKeyDown={(event) => {
              if (event.key === "Enter") commitName();
              if (event.key === "Escape") {
                setName(track.name);
                setEditing(false);
              }
            }}
            className="h-5 min-w-0 flex-1 rounded-[4px] border border-white/25 bg-white/10 px-1 text-[11.5px] text-white outline-none"
          />
        ) : (
          <button
            type="button"
            title={t.mu_rename_hint}
            onDoubleClick={() => setEditing(true)}
            className="min-w-0 flex-1 truncate text-left text-[11.5px] font-medium"
          >
            {track.name}
          </button>
        )}
      </div>
      <div className="flex items-center gap-0.5 pl-[15px]">
        <MiniToggle label={track.muted ? t.ed_unmute_track : t.ed_mute_track} active={track.muted} onClick={() => onPatch(track.id, { muted: !track.muted })}>
          {track.muted ? <VolumeX size={12} /> : <Volume2 size={12} />}
        </MiniToggle>
        <MiniToggle label={t.ed_solo} active={soloed} onClick={() => onSolo(track.id)}>
          <span className="text-[10px] font-bold">S</span>
        </MiniToggle>
        <MiniToggle label={track.locked ? t.ed_unlock_track : t.ed_lock_track} active={track.locked} onClick={() => onPatch(track.id, { locked: !track.locked })}>
          {track.locked ? <Lock size={11} /> : <Unlock size={11} />}
        </MiniToggle>
        <MiniToggle label={t.mu_delete_track} onClick={() => onDelete(track.id)}>
          <Trash2 size={11} />
        </MiniToggle>
        <input
          type="range"
          min={0}
          max={200}
          step={1}
          value={volume}
          title={`${t.mu_track_volume}: ${Math.round(volume)}%`}
          aria-label={t.mu_track_volume}
          onChange={(event) => setVolume(Number(event.target.value))}
          onPointerUp={commitVolume}
          onKeyUp={commitVolume}
          onBlur={commitVolume}
          className="ml-1 h-1 min-w-0 flex-1 accent-[var(--accent-violet)]"
        />
      </div>
    </div>
  );
}

function MiniToggle({ label, active, onClick, children }: { label: string; active?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={clsx(
        "flex h-[22px] w-[22px] items-center justify-center rounded-[5px] transition-colors",
        active ? "bg-[var(--primary)] text-white" : "text-white/55 hover:bg-white/10 hover:text-white",
      )}
    >
      {children}
    </button>
  );
}

// ---- lanes -----------------------------------------------------------------------------------

type LiveClip = { trackId: string; start: number; in: number; duration: number; fade_in: number; fade_out: number };

export type AudioLanesProps = {
  tracks: EditorAudioTrack[];
  segments: EditorSegment[];
  assets: Map<string, MusicAsset>;
  duration: number;
  zoom: number;
  viewport: { left: number; width: number };
  selectedIds: Set<string>;
  snapping: boolean;
  audible: (track: EditorAudioTrack) => boolean;
  ducking: DuckingCache;
  getPlayhead: () => number;
  onGuide: (t: number | null) => void;
  onSelect: (ids: string[], mode?: SelectMode) => void;
  onClearSelection: () => void;
  onSeek: (t: number) => void;
  /** Commit a new track list (one undo step). */
  onChange: (tracks: EditorAudioTrack[], kind: string) => void;
  /** A library asset dropped on a lane (trackId) or on the "new track" row (null). */
  onDropAsset: (trackId: string | null, asset: DroppedAsset, at: number) => void;
};

/** Music / sound-effect lanes: waveform clips, move (also across lanes), trim, fades, snapping, drops. */
export const AudioTrackLanes = memo(function AudioTrackLanes(props: AudioLanesProps) {
  const { t } = useT();
  const {
    tracks,
    segments,
    assets,
    duration,
    zoom,
    viewport,
    selectedIds,
    snapping,
    audible,
    ducking,
    getPlayhead,
    onGuide,
    onSelect,
    onClearSelection,
    onSeek,
    onChange,
    onDropAsset,
  } = props;
  const [live, setLive] = useState<Map<string, LiveClip> | null>(null);
  const [dropRow, setDropRow] = useState<string | null>(null);

  const snapper = (exclude: Set<string>) => {
    if (!snapping) return () => null;
    const points = snapPoints(segments, new Set(), getPlayhead(), duration);
    for (const track of tracks) for (const clip of track.clips) if (!exclude.has(clip.id)) points.push(clip.start, clipEnd(clip));
    points.sort((a, b) => a - b);
    const threshold = SNAP_PX / zoom;
    return (edges: number[]) => snapEdges(edges, points, threshold);
  };

  const drag = (event: ReactPointerEvent, move: (dt: number, pointer: PointerEvent) => void, end: (moved: boolean) => void) => {
    const startX = event.clientX;
    let moved = false;
    const onMove = (pointer: PointerEvent) => {
      const dx = pointer.clientX - startX;
      if (!moved && Math.abs(dx) < 3 && Math.abs(pointer.clientY - event.clientY) < 6) return;
      moved = true;
      move(dx / zoom, pointer);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      onGuide(null);
      end(moved);
      setLive(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const trackOf = (clipId: string) => tracks.find((track) => track.clips.some((clip) => clip.id === clipId))!;

  const beginClip = (track: EditorAudioTrack, clip: EditorAudioClip, mode: "move" | "start" | "end" | "fade_in" | "fade_out") =>
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.shiftKey || event.metaKey || event.ctrlKey) {
        onSelect([clip.id], "toggle");
        return;
      }
      const wasSelected = selectedIds.has(clip.id);
      if (!wasSelected) onSelect([clip.id]);
      if (track.locked) return;
      const group =
        mode === "move" && wasSelected && selectedIds.size > 1
          ? tracks.flatMap((item) => (item.locked ? [] : item.clips.filter((c) => selectedIds.has(c.id)).map((c) => ({ track: item, clip: c }))))
          : [{ track, clip }];
      const movingIds = new Set(group.map((item) => item.clip.id));
      const snap = snapper(movingIds);
      const asset = assets.get(clip.asset_id);
      const neighbours = track.clips.filter((other) => other.id !== clip.id);
      const prevEnd = Math.max(0, ...neighbours.filter((other) => clipEnd(other) <= clip.start + 1e-6).map(clipEnd));
      const nextStart = Math.min(duration, ...neighbours.filter((other) => other.start >= clipEnd(clip) - 1e-6).map((other) => other.start));
      let result = new Map<string, LiveClip>();
      const base = (item: EditorAudioClip, trackId: string): LiveClip => ({
        trackId,
        start: item.start,
        in: item.in,
        duration: item.duration,
        fade_in: item.fade_in,
        fade_out: item.fade_out,
      });

      drag(
        event,
        (dt, pointer) => {
          let guide: number | null = null;
          result = new Map();
          if (mode === "move") {
            const lower = Math.max(...group.map((item) => -item.clip.start));
            const upper = Math.min(...group.map((item) => duration - clipEnd(item.clip)));
            let shift = clamp(dt, lower, upper);
            const hit = snap(group.flatMap((item) => [item.clip.start + shift, clipEnd(item.clip) + shift]));
            if (hit && shift + hit.delta >= lower && shift + hit.delta <= upper) {
              shift += hit.delta;
              guide = hit.at;
            }
            // A single clip can be dragged onto another lane.
            let targetTrack = track.id;
            if (group.length === 1) {
              const lane = document.elementFromPoint(pointer.clientX, pointer.clientY)?.closest<HTMLElement>("[data-audio-track]");
              const id = lane?.dataset.audioTrack;
              if (id && tracks.some((item) => item.id === id && !item.locked)) targetTrack = id;
            }
            for (const item of group) {
              result.set(item.clip.id, { ...base(item.clip, group.length === 1 ? targetTrack : item.track.id), start: item.clip.start + shift });
            }
          } else if (mode === "start") {
            // Left edge: the file's in-point moves with it, the clip's end stays.
            const minDelta = Math.max(-clip.in, prevEnd - clip.start);
            const maxDelta = clip.duration - MIN_CLIP;
            let delta = clamp(dt, minDelta, maxDelta);
            const hit = snap([clip.start + delta]);
            if (hit && delta + hit.delta >= minDelta && delta + hit.delta <= maxDelta) {
              delta += hit.delta;
              guide = hit.at;
            }
            const length = clip.duration - delta;
            result.set(clip.id, {
              ...base(clip, track.id),
              start: clip.start + delta,
              in: clip.in + delta,
              duration: length,
              fade_in: Math.min(clip.fade_in, length),
              fade_out: Math.min(clip.fade_out, length - Math.min(clip.fade_in, length)),
            });
          } else if (mode === "end") {
            const limit = Math.min(maxClipLength(clip, asset, duration), nextStart - clip.start);
            let length = clamp(clip.duration + dt, MIN_CLIP, Math.max(MIN_CLIP, limit));
            const hit = snap([clip.start + length]);
            if (hit && length + hit.delta >= MIN_CLIP && length + hit.delta <= limit) {
              length += hit.delta;
              guide = hit.at;
            }
            result.set(clip.id, {
              ...base(clip, track.id),
              duration: length,
              fade_in: Math.min(clip.fade_in, length),
              fade_out: Math.min(clip.fade_out, length - Math.min(clip.fade_in, length)),
            });
          } else if (mode === "fade_in") {
            result.set(clip.id, { ...base(clip, track.id), fade_in: clamp(clip.fade_in + dt, 0, clip.duration - clip.fade_out) });
          } else {
            result.set(clip.id, { ...base(clip, track.id), fade_out: clamp(clip.fade_out - dt, 0, clip.duration - clip.fade_in) });
          }
          setLive(result);
          onGuide(guide);
        },
        (moved) => {
          if (!moved) {
            if (wasSelected && selectedIds.size > 1 && mode === "move") onSelect([clip.id]);
            return;
          }
          if (!result.size) return;
          commit(result, mode === "move" ? "move-clip" : mode.startsWith("fade") ? "fade-clip" : "trim-clip");
        },
      );
    };

  /** Applies live geometry; a clip that would overlap on its target lane goes to a new lane. */
  const commit = (changes: Map<string, LiveClip>, kind: string) => {
    let next = tracks.map((track) => ({ ...track, clips: track.clips.filter((clip) => !changes.has(clip.id)) }));
    const homeless: EditorAudioClip[] = [];
    for (const track of tracks) {
      for (const clip of track.clips) {
        const change = changes.get(clip.id);
        if (!change) continue;
        const moved: EditorAudioClip = {
          ...clip,
          start: round3(change.start),
          in: round3(change.in),
          duration: round3(change.duration),
          fade_in: round3(change.fade_in),
          fade_out: round3(change.fade_out),
        };
        const target = next.find((item) => item.id === change.trackId)!;
        if (fits(target, moved.start, moved.duration)) target.clips = [...target.clips, moved].sort((a, b) => a.start - b.start);
        else homeless.push(moved);
      }
    }
    if (homeless.length) {
      if (changes.size > 1) return; // A group move that would overlap is cancelled.
      const source = trackOf(homeless[0].id);
      next = [
        ...next,
        { ...source, id: `atr-${Math.random().toString(36).slice(2, 10)}`, name: `${source.name} (2)`, muted: false, locked: false, clips: homeless },
      ];
    }
    onChange(next, kind);
  };

  // Empty lane: click seeks and clears; drag draws a marquee selection over clips.
  const [marquee, setMarquee] = useState<{ trackId: string; from: number; to: number } | null>(null);
  const beginLane = (track: EditorAudioTrack) => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || event.target !== event.currentTarget) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const anchor = clamp((event.clientX - rect.left) / zoom, 0, duration);
    const additive = event.shiftKey || event.metaKey || event.ctrlKey;
    let range: { trackId: string; from: number; to: number } | null = null;
    drag(
      event,
      (dt) => {
        const other = clamp(anchor + dt, 0, duration);
        range = { trackId: track.id, from: Math.min(anchor, other), to: Math.max(anchor, other) };
        setMarquee(range);
      },
      (moved) => {
        setMarquee(null);
        if (!moved || !range) {
          onSeek(anchor);
          if (!additive) onClearSelection();
          return;
        }
        const { from, to } = range;
        const hits = track.clips.filter((clip) => clipEnd(clip) > from && clip.start < to).map((clip) => clip.id);
        onSelect(additive ? [...new Set([...selectedIds, ...hits])] : hits);
      },
    );
  };

  const dropAt = (event: ReactDragEvent<HTMLDivElement>, trackId: string | null) => {
    const asset = readDroppedAsset(event);
    setDropRow(null);
    if (!asset) return;
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    let at = clamp((event.clientX - rect.left) / zoom, 0, duration);
    const hit = snapper(new Set())([at]);
    if (hit) at += hit.delta;
    onDropAsset(trackId, asset, at);
  };

  const from = viewport.left / zoom - 2;
  const to = (viewport.left + viewport.width) / zoom + 2;

  return (
    <>
      {tracks.map((track) => {
        const dim = !audible(track);
        return (
          <div
            key={track.id}
            data-audio-track={track.id}
            className={clsx(
              "relative border-b border-white/[0.06]",
              dropRow === track.id && "bg-[color-mix(in_srgb,var(--accent-violet)_18%,transparent)]",
            )}
            style={{ height: AUDIO_LANE_H }}
            onPointerDown={beginLane(track)}
            onDragOver={(event) => {
              if (!accepts(event) || track.locked) return;
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
              setDropRow(track.id);
            }}
            onDragLeave={() => setDropRow((current) => (current === track.id ? null : current))}
            onDrop={(event) => (track.locked ? undefined : dropAt(event, track.id))}
          >
            {track.clips.map((clip) => {
              const geometry = live?.get(clip.id);
              if (geometry && geometry.trackId !== track.id) return null;
              return (
                <ClipView
                  key={clip.id}
                  clip={geometry ? { ...clip, ...geometry } : clip}
                  track={track}
                  asset={assets.get(clip.asset_id)}
                  zoom={zoom}
                  from={from}
                  to={to}
                  selected={selectedIds.has(clip.id)}
                  dragging={!!geometry}
                  dim={dim}
                  ducking={ducking}
                  missingLabel={t.mu_missing_asset}
                  onPointer={(mode) => beginClip(track, clip, mode)}
                />
              );
            })}
            {/* Clips dragged in from another lane */}
            {live
              ? [...live.entries()]
                  .filter(([id, geometry]) => geometry.trackId === track.id && !track.clips.some((clip) => clip.id === id))
                  .map(([id, geometry]) => {
                    const owner = trackOf(id);
                    const clip = owner.clips.find((item) => item.id === id)!;
                    return (
                      <ClipView
                        key={id}
                        clip={{ ...clip, ...geometry }}
                        track={owner}
                        asset={assets.get(clip.asset_id)}
                        zoom={zoom}
                        from={from}
                        to={to}
                        selected
                        dragging
                        dim={dim}
                        ducking={ducking}
                        missingLabel={t.mu_missing_asset}
                        onPointer={() => () => undefined}
                      />
                    );
                  })
              : null}
            {marquee?.trackId === track.id ? (
              <div
                className="pointer-events-none absolute inset-y-[2px] z-[4] rounded-[3px] border border-white/70 bg-white/10"
                style={{ left: marquee.from * zoom, width: Math.max(1, (marquee.to - marquee.from) * zoom) }}
              />
            ) : null}
          </div>
        );
      })}
      <div
        className={clsx(
          "relative flex items-center border-b border-white/[0.06] text-[11px] text-white/35",
          dropRow === "__new" && "bg-[color-mix(in_srgb,var(--accent-violet)_18%,transparent)] text-white/80",
        )}
        style={{ height: ADD_ROW_H }}
        onDragOver={(event) => {
          if (!accepts(event)) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
          setDropRow("__new");
        }}
        onDragLeave={() => setDropRow((current) => (current === "__new" ? null : current))}
        onDrop={(event) => dropAt(event, null)}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          const rect = event.currentTarget.getBoundingClientRect();
          onSeek(clamp((event.clientX - rect.left) / zoom, 0, duration));
        }}
      >
        <span className="pointer-events-none sticky left-3 px-3">{t.mu_drop_new_track}</span>
      </div>
    </>
  );
});

// ---- one clip ------------------------------------------------------------------------------

function ClipView({
  clip,
  track,
  asset,
  zoom,
  from,
  to,
  selected,
  dragging,
  dim,
  ducking,
  missingLabel,
  onPointer,
}: {
  clip: EditorAudioClip;
  track: EditorAudioTrack;
  asset: MusicAsset | undefined;
  zoom: number;
  from: number;
  to: number;
  selected: boolean;
  dragging: boolean;
  dim: boolean;
  ducking: DuckingCache;
  missingLabel: string;
  onPointer: (mode: "move" | "start" | "end" | "fade_in" | "fade_out") => (event: ReactPointerEvent<HTMLDivElement>) => void;
}) {
  const end = clipEnd(clip);
  if (end < from || clip.start > to) return null;
  const width = Math.max(2, clip.duration * zoom);
  const missing = !asset || asset.missing;
  const sfx = track.kind === "sfx";
  const label = asset?.title || clip.title || clip.asset_id;
  return (
    <div
      data-clip-id={clip.id}
      className={clsx(
        "group absolute bottom-[4px] top-[4px] select-none overflow-hidden rounded-[5px] border text-[10.5px] leading-none text-white",
        track.locked ? "cursor-default" : "cursor-grab active:cursor-grabbing",
        missing
          ? "border-[color-mix(in_srgb,var(--danger)_70%,black)] bg-[color-mix(in_srgb,var(--danger)_55%,var(--navy-900))]"
          : sfx
            ? "border-[color-mix(in_srgb,var(--accent-pink)_60%,black)] bg-[color-mix(in_srgb,var(--accent-pink)_42%,var(--navy-900))]"
            : "border-[color-mix(in_srgb,var(--accent-violet)_60%,black)] bg-[color-mix(in_srgb,var(--accent-violet)_45%,var(--navy-900))]",
        (dim || track.muted) && "opacity-40",
        selected && "z-[2] ring-2 ring-white brightness-110",
        dragging && "z-[3] opacity-90 shadow-lg",
      )}
      style={{ left: clip.start * zoom, width, touchAction: "none" }}
      title={`${label} · ${clip.duration.toFixed(2)}s${missing ? ` · ${missingLabel}` : ""}`}
      onPointerDown={onPointer("move")}
    >
      <ClipWave clip={clip} asset={asset} zoom={zoom} from={from} to={to} ducking={ducking} />
      {width > 40 ? (
        <span className="pointer-events-none absolute left-1.5 top-1 flex max-w-[calc(100%-12px)] items-center gap-1 truncate text-[10px] font-medium text-white/90 [text-shadow:0_1px_2px_rgba(0,0,0,.6)]">
          {clip.loop ? <span className="rounded-[3px] bg-white/20 px-1 text-[9px]">∞</span> : null}
          <span className="truncate">{label}</span>
        </span>
      ) : null}
      <FadeOverlay clip={clip} zoom={zoom} />
      {width > 18 && !track.locked ? (
        <>
          <div className="absolute inset-y-0 left-0 z-[1] w-[6px] cursor-ew-resize group-hover:bg-white/35" onPointerDown={onPointer("start")} />
          <div className="absolute inset-y-0 right-0 z-[1] w-[6px] cursor-ew-resize group-hover:bg-white/35" onPointerDown={onPointer("end")} />
          <div
            className="absolute top-0 z-[2] h-2.5 w-2.5 -translate-x-1/2 cursor-ew-resize rounded-full border border-white bg-[var(--accent-violet)] opacity-0 group-hover:opacity-100"
            style={{ left: Math.max(5, clip.fade_in * zoom) }}
            onPointerDown={onPointer("fade_in")}
          />
          <div
            className="absolute top-0 z-[2] h-2.5 w-2.5 translate-x-1/2 cursor-ew-resize rounded-full border border-white bg-[var(--accent-violet)] opacity-0 group-hover:opacity-100"
            style={{ right: Math.max(5, clip.fade_out * zoom) }}
            onPointerDown={onPointer("fade_out")}
          />
        </>
      ) : null}
    </div>
  );
}

function FadeOverlay({ clip, zoom }: { clip: EditorAudioClip; zoom: number }) {
  const width = Math.max(2, clip.duration * zoom);
  const fadeIn = clip.fade_in * zoom;
  const fadeOut = clip.fade_out * zoom;
  if (!fadeIn && !fadeOut) return null;
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${width} 100`} preserveAspectRatio="none">
      {fadeIn ? <polygon points={`0,0 ${fadeIn},0 0,100`} fill="rgba(11,23,48,0.45)" /> : null}
      {fadeOut ? <polygon points={`${width - fadeOut},0 ${width},0 ${width},100`} fill="rgba(11,23,48,0.45)" /> : null}
    </svg>
  );
}

/** Waveform of the visible part of a clip + the gain line (fades × ducking). */
function ClipWave({
  clip,
  asset,
  zoom,
  from,
  to,
  ducking,
}: {
  clip: EditorAudioClip;
  asset: MusicAsset | undefined;
  zoom: number;
  from: number;
  to: number;
  ducking: DuckingCache;
}) {
  const peaks = usePeaks(clip.asset_id);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const visibleFrom = Math.max(clip.start, from);
  const visibleTo = Math.min(clipEnd(clip), to);
  const left = (visibleFrom - clip.start) * zoom;
  const cssWidth = Math.max(1, Math.round((visibleTo - visibleFrom) * zoom));
  const assetDuration = asset?.duration ?? 0;
  const key = useMemo(
    () => [clip.start, clip.in, clip.duration, clip.fade_in, clip.fade_out, clip.loop, JSON.stringify(clip.ducking)].join("|"),
    [clip],
  );
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = window.devicePixelRatio || 1;
    const cssHeight = canvas.clientHeight || 38;
    canvas.width = Math.round(cssWidth * ratio);
    canvas.height = Math.round(cssHeight * ratio);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);
    const middle = cssHeight / 2 + 4;
    const amplitude = cssHeight / 2 - 5;
    if (peaks?.peaks.length) {
      ctx.fillStyle = "rgba(255,255,255,0.55)";
      for (let x = 0; x < cssWidth; x++) {
        const t = visibleFrom + x / zoom;
        const pos = filePosition(clip, t, assetDuration);
        if (pos === null) continue;
        const span = Math.max(1, Math.ceil(peaks.rate / zoom));
        const first = Math.floor(pos * peaks.rate);
        let peak = 0;
        for (let i = first; i < first + span && i < peaks.peaks.length; i++) peak = Math.max(peak, peaks.peaks[i]);
        const half = Math.max(0.5, (peak / 255) * amplitude * Math.max(0.15, clipEnvelope(clip, t, ducking)));
        ctx.fillRect(x, middle - half, 1, half * 2);
      }
    }
    // Gain line: top = full clip volume.
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= cssWidth; x += 2) {
      const gain = clipEnvelope(clip, visibleFrom + x / zoom, ducking);
      const y = 13 + (1 - gain) * (cssHeight - 16);
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }, [peaks, key, visibleFrom, cssWidth, zoom, assetDuration, ducking]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <canvas
      ref={canvasRef}
      className="pointer-events-none absolute inset-y-0"
      style={{ left, width: cssWidth, height: "100%" }}
    />
  );
}
