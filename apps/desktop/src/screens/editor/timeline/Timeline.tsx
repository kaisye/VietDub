import clsx from "clsx";
import {
  AudioLines,
  Droplets,
  Eye,
  EyeOff,
  Film,
  Link2,
  Lock,
  Magnet,
  Maximize,
  LetterText,
  Plus,
  Scissors,
  Trash2,
  Type,
  Unlock,
  Mic,
  Volume2,
  VolumeX,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import type { EditorThumbnails } from "../../../api";
import { useT } from "../../../i18n";
import type {
  EditorAudioTrack,
  EditorRegion,
  EditorSegment,
  EditorSegmentPatch,
  EditorTextOverlay,
  EditorWorkspace,
  MusicAsset,
} from "../../../types";
import OriginalTrack, { type Waveform } from "../../EditorOriginalTrack";
import type { ReviewMarker } from "../useAutoReview";
import type { SelectMode } from "../useEditor";
import { clamp, formatTime, ZOOM_MAX, ZOOM_MIN } from "../utils";
import { ClipLanes, SUB_LANE_H, VOICE_LANE_H } from "./ClipLanes";
import { AudioTrackHeaders, AudioTrackLanes, type DroppedAsset } from "../music/AudioTracks";
import type { DuckingCache } from "../music/model";
import { VideoLane, VIDEO_LANE_H } from "./VideoLane";
import { TextLane, textLaneHeight } from "../text/TextLane";

export type TrackState = {
  subHidden: boolean;
  subLocked: boolean;
  voiceLocked: boolean;
  originalLocked: boolean;
  /** Preview-only solo (never saved, never affects the render). */
  solo: "dub" | "original" | null;
};

export type TimelineApi = { fit: () => void; zoomBy: (factor: number) => void };

/** The text-box lane above the cues (see text/TextLane.tsx). */
export type TimelineText = {
  items: EditorTextOverlay[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onChange: (items: EditorTextOverlay[], kind: string) => void;
  onAdd: (at: number) => void;
};

/** Music / sound-effect lanes under the built-in tracks (see music/AudioTracks.tsx). */
export type TimelineMusic = {
  tracks: EditorAudioTrack[];
  assets: Map<string, MusicAsset>;
  selectedIds: Set<string>;
  /** Preview-only solo (never saved). */
  solo: Set<string>;
  audible: (track: EditorAudioTrack) => boolean;
  ducking: DuckingCache;
  onSolo: (id: string) => void;
  onPatchTrack: (id: string, patch: Partial<EditorAudioTrack>) => void;
  onDeleteTrack: (id: string) => void;
  onAddTrack: () => void;
  onSelect: (ids: string[], mode?: SelectMode) => void;
  onChange: (tracks: EditorAudioTrack[], kind: string) => void;
  onDropAsset: (trackId: string | null, asset: DroppedAsset, at: number) => void;
};

const HEADER_W = 184;
const RULER_H = 28;
const ORIGINAL_LANE_H = 48;
const BLUR_LANE_H = 26;
const MAJOR_STEPS: [number, number][] = [
  [0.5, 5], [1, 5], [2, 4], [5, 5], [10, 5], [15, 3], [30, 3], [60, 4], [120, 4], [300, 5], [600, 5], [900, 3], [1800, 3],
];

export function Timeline({
  workspace,
  waveform,
  thumbnails,
  time,
  playing,
  zoom,
  onZoom,
  apiRef,
  selectedIds,
  primaryId,
  selectedRegionId,
  tracks,
  onTracks,
  snapping,
  onSnapping,
  linked,
  onLinked,
  busy,
  getPlayhead,
  onSeek,
  onScrub,
  onSelect,
  onSelectIds,
  onClearSelection,
  onSelectRegion,
  onRegionsChange,
  onMove,
  onRetimeVoice,
  onAddCue,
  onOpenCue,
  onSplit,
  onDelete,
  onToggleDub,
  onToggleOriginal,
  onToggleBlur,
  onOpenBlur,
  markers,
  focusRange,
  onMarker,
  music,
  text,
}: {
  workspace: EditorWorkspace;
  waveform: Waveform | null;
  thumbnails: EditorThumbnails | null;
  time: number;
  playing: boolean;
  zoom: number;
  onZoom: (zoom: number) => void;
  apiRef: MutableRefObject<TimelineApi | null>;
  selectedIds: Set<string>;
  primaryId: string | null;
  selectedRegionId: string | null;
  tracks: TrackState;
  onTracks: (patch: Partial<TrackState>) => void;
  snapping: boolean;
  onSnapping: (value: boolean) => void;
  linked: boolean;
  onLinked: (value: boolean) => void;
  busy: boolean;
  getPlayhead: () => number;
  onSeek: (t: number) => void;
  /** Scrubbing started (true) / ended (false): the screen pauses playback meanwhile. */
  onScrub: (active: boolean) => void;
  onSelect: (seg: EditorSegment, options: { jump?: boolean; mode?: SelectMode }) => void;
  onSelectIds: (ids: string[]) => void;
  onClearSelection: () => void;
  onSelectRegion: (id: string | null) => void;
  onRegionsChange: (regions: EditorRegion[], selectId?: string) => void;
  onMove: (updates: EditorSegmentPatch[]) => void;
  onRetimeVoice: (id: string, patch: { audio_start?: number; audio_length?: number }) => void;
  onAddCue: (t: number) => void;
  onOpenCue: (seg: EditorSegment) => void;
  onSplit: () => void;
  onDelete: () => void;
  onToggleDub: () => void;
  onToggleOriginal: () => void;
  onToggleBlur: () => void;
  onOpenBlur: () => void;
  /** Auto review problem regions, drawn on the ruler (click jumps to the cue). */
  markers?: ReviewMarker[];
  /** Highlighted range (the proposal under the pointer in the review panel). */
  focusRange?: { start: number; end: number } | null;
  onMarker?: (cueId: string, time: number) => void;
  music: TimelineMusic;
  text: TimelineText;
}) {
  const { t } = useT();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const duration = workspace.duration;
  const width = Math.max(1, duration * zoom);
  const [viewport, setViewport] = useState({ left: 0, width: 0 });
  const [guide, setGuide] = useState<number | null>(null);
  const dubMuted = !(workspace.audio?.dubbed_enabled ?? true);
  const originalMuted = !(workspace.audio?.original_enabled ?? true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() =>
        setViewport({ left: el.scrollLeft, width: Math.max(0, el.clientWidth - HEADER_W) }),
      );
    };
    update();
    el.addEventListener("scroll", update);
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      el.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, []);

  // ---- zoom ------------------------------------------------------------------
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const timeRef = useRef(time);
  timeRef.current = time;
  // Where the zoom should stay put: a time and its offset from the lanes' left edge.
  const zoomAnchor = useRef<{ time: number; offset: number } | null>(null);
  const minZoom = useCallback(() => {
    const el = scrollRef.current;
    const fit = el ? (el.clientWidth - HEADER_W - 24) / Math.max(1, duration) : ZOOM_MIN;
    return Math.max(0.02, Math.min(ZOOM_MIN, fit));
  }, [duration]);
  const zoomAt = useCallback(
    (next: number, offset?: number) => {
      const el = scrollRef.current;
      if (!el) return;
      const clamped = clamp(next, minZoom(), ZOOM_MAX);
      if (Math.abs(clamped - zoomRef.current) < 0.0001) return;
      const lanesWidth = el.clientWidth - HEADER_W;
      // Default anchor: the playhead if visible, else the middle of the view.
      const playX = timeRef.current * zoomRef.current - el.scrollLeft;
      const at = offset ?? (playX >= 0 && playX <= lanesWidth ? playX : lanesWidth / 2);
      // Several events can land before React re-renders; keep the first anchor's time.
      zoomAnchor.current = { time: zoomAnchor.current?.time ?? (el.scrollLeft + at) / zoomRef.current, offset: at };
      zoomRef.current = clamped;
      onZoom(clamped);
    },
    [onZoom, minZoom],
  );
  const fit = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const next = clamp((el.clientWidth - HEADER_W - 24) / Math.max(1, duration), 0.02, ZOOM_MAX);
    zoomAnchor.current = { time: 0, offset: 0 };
    zoomRef.current = next;
    onZoom(next);
  }, [duration, onZoom]);
  apiRef.current = { fit, zoomBy: (factor: number) => zoomAt(zoomRef.current * factor) };

  // Trackpad pinch / ⌘-wheel zooms around the pointer. WebKit (Tauri on macOS) sends
  // gesture* events with a scale; Chromium sends wheel events with ctrlKey set.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const offsetOf = (clientX: number) => Math.max(0, clientX - el.getBoundingClientRect().left - HEADER_W);
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        zoomAt(zoomRef.current * Math.exp(-event.deltaY * 0.01), offsetOf(event.clientX));
        return;
      }
      // A mouse wheel scrolls the timeline sideways when there is nothing to scroll vertically.
      if (Math.abs(event.deltaY) > Math.abs(event.deltaX) && el.scrollHeight <= el.clientHeight + 1) {
        event.preventDefault();
        el.scrollLeft += event.deltaY;
      }
    };
    let gestureBase = zoomRef.current;
    type GestureEvent = Event & { scale: number; clientX: number };
    const onGestureStart = (event: Event) => {
      event.preventDefault();
      gestureBase = zoomRef.current;
    };
    const onGestureChange = (event: Event) => {
      event.preventDefault();
      const gesture = event as GestureEvent;
      zoomAt(gestureBase * gesture.scale, offsetOf(gesture.clientX));
    };
    const block = (event: Event) => event.preventDefault();
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("gesturestart", onGestureStart);
    el.addEventListener("gesturechange", onGestureChange);
    el.addEventListener("gestureend", block);
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("gesturestart", onGestureStart);
      el.removeEventListener("gesturechange", onGestureChange);
      el.removeEventListener("gestureend", block);
    };
  }, [zoomAt]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const anchor = zoomAnchor.current;
    if (anchor) {
      el.scrollLeft = Math.max(0, anchor.time * zoom - anchor.offset);
      zoomAnchor.current = null;
    }
    setViewport({ left: el.scrollLeft, width: Math.max(0, el.clientWidth - HEADER_W) });
  }, [zoom]);

  // ---- follow the playhead / selection ----------------------------------------
  const visibleMarkers = useMemo(() => {
    if (!markers?.length) return [];
    const from = (viewport.left - 50) / zoom;
    const to = (viewport.left + viewport.width + 50) / zoom;
    const order = { info: 0, warning: 1, error: 2 } as const;
    return markers.filter((marker) => marker.end >= from && marker.start <= to).sort((a, b) => order[a.severity] - order[b.severity]);
  }, [markers, viewport, zoom]);
  const playheadX = time * zoom;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !playing) return;
    const lanesWidth = el.clientWidth - HEADER_W;
    if (playheadX < el.scrollLeft || playheadX > el.scrollLeft + lanesWidth - 40) {
      el.scrollLeft = Math.max(0, playheadX - 80); // Page-flip like an NLE.
    }
  }, [playheadX, playing]);

  const primary = workspace.segments.find((seg) => seg.id === primaryId);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !primary) return;
    const lanesWidth = el.clientWidth - HEADER_W;
    const x = primary.start * zoomRef.current;
    if (x < el.scrollLeft || x > el.scrollLeft + lanesWidth - 60) el.scrollLeft = Math.max(0, x - lanesWidth / 3);
  }, [primaryId]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- ruler / scrubbing ---------------------------------------------------------
  const [major, minorDiv] = useMemo(() => {
    for (const [step, div] of MAJOR_STEPS) if (step * zoom >= 84) return [step, div];
    return MAJOR_STEPS[MAJOR_STEPS.length - 1];
  }, [zoom]);
  const ticks = useMemo(() => {
    const minor = major / minorDiv;
    const first = Math.max(0, Math.floor(viewport.left / zoom / major) * major);
    const last = Math.min(duration, (viewport.left + viewport.width) / zoom + major);
    const items: { t: number; major: boolean }[] = [];
    for (let i = 0; first + i * minor <= last; i++) {
      const value = first + i * minor;
      items.push({ t: value, major: i % minorDiv === 0 });
    }
    return items;
  }, [viewport.left, viewport.width, zoom, major, minorDiv, duration]);

  const scrub = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const lane = event.currentTarget.getBoundingClientRect();
    const at = (clientX: number) => clamp((clientX - lane.left) / zoomRef.current, 0, duration);
    onScrub(true);
    onSeek(at(event.clientX));
    let frame = 0;
    const move = (pointer: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => onSeek(at(pointer.clientX)));
    };
    const up = () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      onScrub(false);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const seekLane = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    onSeek(clamp((event.clientX - rect.left) / zoom, 0, duration));
  };

  const clipLabels = useMemo(() => ({ voiceSpeed: t.ed_speed, regenerated: t.ed_regenerated }), [t]);
  // Text boxes stick to cue edges as well as to each other and the playhead.
  const textSnapTimes = useMemo(() => workspace.segments.flatMap((seg) => [seg.start, seg.end]), [workspace.segments]);
  const selectionCount = selectedIds.size;
  const sliderValue = Math.log(zoom);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--navy-900)]">
      {/* Timeline toolbar */}
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-[var(--border)] bg-[var(--surface)] px-2">
        <ToolButton label={`${t.ed_split} (S / ⌘B)`} onClick={onSplit} disabled={busy}>
          <Scissors size={15} />
        </ToolButton>
        <ToolButton label={`${t.ed_delete} (Delete)`} onClick={onDelete} disabled={busy || (!selectionCount && !selectedRegionId && !music.selectedIds.size)}>
          <Trash2 size={15} />
        </ToolButton>
        <ToolButton label={t.ed_add_cue_hint} onClick={() => onAddCue(getPlayhead())} disabled={busy || tracks.subLocked}>
          <Plus size={15} />
        </ToolButton>
        <span className="mx-1.5 h-5 w-px bg-[var(--border)]" />
        <ToolButton label={`${t.ed_snap} (N)`} active={snapping} onClick={() => onSnapping(!snapping)}>
          <Magnet size={15} />
        </ToolButton>
        <ToolButton label={t.ed_link} active={linked} onClick={() => onLinked(!linked)}>
          <Link2 size={15} />
        </ToolButton>
        {selectionCount > 1 || music.selectedIds.size > 1 ? (
          <span className="ml-2 rounded-full bg-[var(--primary-soft)] px-2 py-0.5 text-[11.5px] font-semibold text-[var(--primary-hover)]">
            {selectionCount || music.selectedIds.size} {t.ed_selected}
          </span>
        ) : null}
        <span className="flex-1" />
        <span className="mr-2 hidden font-mono text-[11px] text-[var(--text-tertiary)] lg:inline">
          {formatTime(viewport.left / zoom, false)} – {formatTime((viewport.left + viewport.width) / zoom, false)}
        </span>
        <ToolButton label={`${t.ed_zoom_out} (−)`} onClick={() => zoomAt(zoom / 1.5)}>
          <ZoomOut size={15} />
        </ToolButton>
        <input
          type="range"
          aria-label={t.ed_zoom}
          min={Math.log(0.02)}
          max={Math.log(ZOOM_MAX)}
          step={0.01}
          value={sliderValue}
          onChange={(e) => zoomAt(Math.exp(Number(e.target.value)))}
          className="w-28 accent-[var(--primary)]"
        />
        <ToolButton label={`${t.ed_zoom_in} (+)`} onClick={() => zoomAt(zoom * 1.5)}>
          <ZoomIn size={15} />
        </ToolButton>
        <ToolButton label={`${t.ed_zoom_fit} (\\)`} onClick={fit}>
          <Maximize size={14} />
        </ToolButton>
      </div>

      {/* Tracks: one scroller; the header column and ruler are sticky. */}
      <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-auto overscroll-contain">
        <div className="relative flex" style={{ width: HEADER_W + width + 40, minHeight: "100%" }}>
          <div
            className="sticky left-0 z-20 shrink-0 border-r border-white/10 bg-[var(--navy-900)]"
            style={{ width: HEADER_W }}
          >
            <div
              className="sticky top-0 z-10 flex items-center border-b border-white/10 bg-[var(--navy-900)] px-3 font-mono text-[10.5px] text-white/50"
              style={{ height: RULER_H }}
            >
              {formatTime(time)}
            </div>
            <TrackHeader height={VIDEO_LANE_H} icon={<Film size={14} />} label={t.ed_track_video} />
            <TrackHeader height={textLaneHeight(text.items)} icon={<LetterText size={14} />} label={t.tx_track}>
              <HeaderToggle label={t.tx_add_hint} active={false} onClick={() => text.onAdd(getPlayhead())}>
                <Plus size={13} />
              </HeaderToggle>
            </TrackHeader>
            <TrackHeader height={SUB_LANE_H} icon={<Type size={14} />} label={t.ed_track_subtitles}>
              <HeaderToggle
                label={tracks.subHidden ? t.ed_show_preview : t.ed_hide_preview}
                active={tracks.subHidden}
                onClick={() => onTracks({ subHidden: !tracks.subHidden })}
              >
                {tracks.subHidden ? <EyeOff size={13} /> : <Eye size={13} />}
              </HeaderToggle>
              <LockToggle locked={tracks.subLocked} onClick={() => onTracks({ subLocked: !tracks.subLocked })} />
            </TrackHeader>
            <TrackHeader height={VOICE_LANE_H} icon={<Mic size={14} />} label={t.ed_track_dub}>
              <HeaderToggle label={dubMuted ? t.ed_unmute_track : t.ed_mute_track} active={dubMuted} onClick={onToggleDub}>
                {dubMuted ? <VolumeX size={13} /> : <Volume2 size={13} />}
              </HeaderToggle>
              <HeaderToggle
                label={t.ed_solo}
                active={tracks.solo === "dub"}
                onClick={() => onTracks({ solo: tracks.solo === "dub" ? null : "dub" })}
              >
                <span className="text-[10.5px] font-bold">S</span>
              </HeaderToggle>
              <LockToggle locked={tracks.voiceLocked} onClick={() => onTracks({ voiceLocked: !tracks.voiceLocked })} />
            </TrackHeader>
            <TrackHeader height={ORIGINAL_LANE_H} icon={<AudioLines size={14} />} label={t.ed_track_original}>
              <HeaderToggle
                label={originalMuted ? t.ed_unmute_track : t.ed_mute_track}
                active={originalMuted}
                onClick={onToggleOriginal}
              >
                {originalMuted ? <VolumeX size={13} /> : <Volume2 size={13} />}
              </HeaderToggle>
              <HeaderToggle
                label={t.ed_solo}
                active={tracks.solo === "original"}
                onClick={() => onTracks({ solo: tracks.solo === "original" ? null : "original" })}
              >
                <span className="text-[10.5px] font-bold">S</span>
              </HeaderToggle>
              <LockToggle
                locked={tracks.originalLocked}
                onClick={() => onTracks({ originalLocked: !tracks.originalLocked })}
              />
            </TrackHeader>
            <TrackHeader height={BLUR_LANE_H} icon={<Droplets size={13} />} label={t.ed_track_blur} compact>
              <HeaderToggle
                label={workspace.blur.enabled ? t.ed_blur_off : t.ed_blur_on}
                active={!workspace.blur.enabled}
                onClick={onToggleBlur}
              >
                {workspace.blur.enabled ? <Eye size={13} /> : <EyeOff size={13} />}
              </HeaderToggle>
            </TrackHeader>
            <AudioTrackHeaders
              tracks={music.tracks}
              solo={music.solo}
              onSolo={music.onSolo}
              onPatch={music.onPatchTrack}
              onDelete={music.onDeleteTrack}
              onAdd={music.onAddTrack}
            />
          </div>

          <div className="relative shrink-0" style={{ width: width + 40 }}>
            {/* Ruler */}
            <div
              className="sticky top-0 z-10 cursor-pointer border-b border-white/10 bg-[var(--navy-900)]"
              style={{ height: RULER_H, width: width + 40 }}
              onPointerDown={scrub}
            >
              {ticks.map((tick) => (
                <span
                  key={tick.t}
                  className={clsx(
                    "pointer-events-none absolute bottom-0 w-px",
                    tick.major ? "h-2.5 bg-white/40" : "h-1.5 bg-white/20",
                  )}
                  style={{ left: tick.t * zoom }}
                >
                  {tick.major ? (
                    <span className="absolute bottom-3 left-1 whitespace-nowrap font-mono text-[10px] text-white/55">
                      {major < 1 ? `${formatTime(tick.t)}` : formatTime(tick.t, false)}
                    </span>
                  ) : null}
                </span>
              ))}
              {/* Auto review markers: errors on top of warnings, only what is on screen. */}
              {visibleMarkers.map((marker) => (
                <button
                  key={`${marker.cueId}-${marker.type}`}
                  type="button"
                  aria-label={t.ar_markers}
                  title={`${t.ar_markers} · ${formatTime(marker.start)}`}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={() => onMarker?.(marker.cueId, marker.start)}
                  className={clsx(
                    "absolute top-0 h-[5px] rounded-b-[2px] hover:h-[8px]",
                    marker.severity === "error" ? "z-[2] bg-[var(--danger)]" : marker.severity === "warning" ? "z-[1] bg-[var(--warning)]" : "bg-white/35",
                  )}
                  style={{ left: marker.start * zoom, width: Math.max(4, (marker.end - marker.start) * zoom) }}
                />
              ))}
              {/* Playhead head */}
              <span
                className="pointer-events-none absolute bottom-0 z-[1] -ml-[6px] h-3 w-3 rotate-45 rounded-[2px] bg-[var(--primary)] shadow-[0_0_0_1.5px_white]"
                style={{ left: playheadX, bottom: -6 }}
              />
            </div>

            <VideoLane
              thumbnails={thumbnails}
              duration={duration}
              zoom={zoom}
              viewport={viewport}
              aspect={(workspace.width || 16) / (workspace.height || 9)}
              title={workspace.title || workspace.job_id}
              onPointerDown={seekLane}
            />
            <TextLane
              items={text.items}
              duration={duration}
              zoom={zoom}
              selectedId={text.selectedId}
              snapping={snapping}
              snapTimes={textSnapTimes}
              getPlayhead={getPlayhead}
              onGuide={setGuide}
              onSelect={text.onSelect}
              onSeek={onSeek}
              onChange={text.onChange}
              onAdd={text.onAdd}
            />
            <ClipLanes
              segments={workspace.segments}
              duration={duration}
              zoom={zoom}
              viewport={viewport}
              selectedIds={selectedIds}
              snapping={snapping}
              linked={linked}
              subLocked={tracks.subLocked}
              voiceLocked={tracks.voiceLocked}
              dubMuted={dubMuted || tracks.solo === "original"}
              getPlayhead={getPlayhead}
              onGuide={setGuide}
              onSelect={onSelect}
              onSelectIds={onSelectIds}
              onClearSelection={onClearSelection}
              onSeek={onSeek}
              onMove={onMove}
              onRetimeVoice={onRetimeVoice}
              onAddCue={onAddCue}
              onOpenCue={onOpenCue}
              labels={clipLabels}
            />
            <div className={clsx((originalMuted || tracks.solo === "dub") && "opacity-45")}>
              <OriginalTrack
                duration={duration}
                zoom={zoom}
                viewport={viewport}
                waveform={waveform}
                regions={workspace.original_regions}
                baseVolume={workspace.audio.original_volume}
                selectedRegionId={selectedRegionId}
                onSelectRegion={onSelectRegion}
                onRegionsChange={onRegionsChange}
                onSeek={onSeek}
                locked={tracks.originalLocked}
              />
            </div>
            <div className="relative" style={{ height: BLUR_LANE_H }} onPointerDown={seekLane}>
              {workspace.blur.enabled ? (
                <button
                  type="button"
                  className="absolute inset-y-[5px] left-0 rounded-[4px] border border-white/15 bg-white/10 px-2 text-left text-[10px] text-white/70 hover:bg-white/15"
                  style={{ width }}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={onOpenBlur}
                >
                  {t.ed_track_blur_clip}
                </button>
              ) : null}
            </div>
            <AudioTrackLanes
              tracks={music.tracks}
              segments={workspace.segments}
              assets={music.assets}
              duration={duration}
              zoom={zoom}
              viewport={viewport}
              selectedIds={music.selectedIds}
              snapping={snapping}
              audible={music.audible}
              ducking={music.ducking}
              getPlayhead={getPlayhead}
              onGuide={setGuide}
              onSelect={music.onSelect}
              onClearSelection={onClearSelection}
              onSeek={onSeek}
              onChange={music.onChange}
              onDropAsset={music.onDropAsset}
            />

            {focusRange ? (
              <div
                className="pointer-events-none absolute bottom-0 z-[5] rounded-[3px] border border-[var(--warning)] bg-[color-mix(in_srgb,var(--warning)_14%,transparent)]"
                style={{ left: focusRange.start * zoom, width: Math.max(4, (focusRange.end - focusRange.start) * zoom), top: RULER_H }}
              />
            ) : null}
            {/* Snap guide + playhead line over every lane */}
            {guide !== null ? (
              <div
                className="pointer-events-none absolute bottom-0 top-0 z-[6] w-px bg-[var(--accent-cyan)] shadow-[0_0_6px_var(--accent-cyan)]"
                style={{ left: guide * zoom }}
              />
            ) : null}
            <div
              className="pointer-events-none absolute bottom-0 top-0 z-[7] -ml-px w-0.5 bg-[var(--primary)]"
              style={{ left: playheadX, top: RULER_H - 2 }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function ToolButton({
  label,
  onClick,
  disabled,
  active,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        "flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)] transition-colors disabled:opacity-40",
        active
          ? "bg-[var(--primary-soft)] text-[var(--primary)]"
          : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]",
      )}
    >
      {children}
    </button>
  );
}

function TrackHeader({
  height,
  icon,
  label,
  children,
  compact,
}: {
  height: number;
  icon: ReactNode;
  label: string;
  children?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className="flex items-center gap-1.5 border-b border-white/[0.06] pl-3 pr-1.5 text-white/80"
      style={{ height }}
    >
      <span className="shrink-0 text-white/55">{icon}</span>
      <span className={clsx("min-w-0 flex-1 truncate font-medium", compact ? "text-[11px]" : "text-[11.5px]")}>{label}</span>
      <span className="flex shrink-0 items-center gap-0.5">{children}</span>
    </div>
  );
}

function HeaderToggle({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={clsx(
        "flex h-6 w-6 items-center justify-center rounded-[5px] transition-colors",
        active ? "bg-[var(--primary)] text-white" : "text-white/55 hover:bg-white/10 hover:text-white",
      )}
    >
      {children}
    </button>
  );
}

function LockToggle({ locked, onClick }: { locked: boolean; onClick: () => void }) {
  const { t } = useT();
  return (
    <HeaderToggle label={locked ? t.ed_unlock_track : t.ed_lock_track} active={locked} onClick={onClick}>
      {locked ? <Lock size={12} /> : <Unlock size={12} />}
    </HeaderToggle>
  );
}
