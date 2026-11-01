import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { X } from "lucide-react";
import { getEditorThumbnails, type EditorThumbnails } from "../../api";
import { Button, InlineNotice, LoadingState } from "../../components/ui";
import { useT } from "../../i18n";
import { Inspector } from "./Inspector";
import { LeftPanel, type CueFilter, type LeftTab } from "./LeftPanel";
import { Preview } from "./Preview";
import { Timeline, type TimelineApi, type TrackState } from "./timeline/Timeline";
import { ShortcutsHelp, Toolbar } from "./Toolbar";
import { useAutoReview } from "./useAutoReview";
import { useAudioTracks } from "./music/useAudioTracks";
import { useEditor } from "./useEditor";
import { usePlayback } from "./usePlayback";
import { loadPref, savePref, segmentAt } from "./utils";

const LAYOUT_KEY = "vd.editor.layout";
const TRACKS_KEY = "vd.editor.tracks";

type Layout = { timelineHeight: number; zoom: number; snapping: boolean; linked: boolean; tab: LeftTab };
const DEFAULT_LAYOUT: Layout = { timelineHeight: 290, zoom: 24, snapping: true, linked: true, tab: "cues" };
const DEFAULT_TRACKS: TrackState = { subHidden: false, subLocked: false, voiceLocked: false, originalLocked: false, solo: null };

/**
 * NLE-style editor workspace: toolbar, assets panel (left), preview (center),
 * inspector (right) and a multi-track timeline (bottom).
 */
export default function EditorScreen({
  jobId,
  onBack,
  onOpenAssistant,
}: {
  jobId: string;
  onBack: () => void;
  /** Opens the AI assistant screen (its settings live there) from the AI tab. */
  onOpenAssistant?: () => void;
}) {
  const { lang, t } = useT();
  const L = useCallback((vi: string, en: string) => (lang === "vi" ? vi : en), [lang]);

  // seek lives in usePlayback, which needs the workspace from useEditor: bridge it with a ref.
  const seekRef = useRef<(time: number) => void>(() => undefined);
  const editor = useEditor(jobId, L, seekRef);
  const { workspace, segments } = editor;
  const review = useAutoReview(jobId, editor);
  const [reviewFocus, setReviewFocus] = useState<{ start: number; end: number } | null>(null);

  const [layout, setLayout] = useState<Layout>(() => loadPref(LAYOUT_KEY, DEFAULT_LAYOUT));
  const updateLayout = useCallback((patch: Partial<Layout>) => {
    setLayout((current) => {
      const next = { ...current, ...patch };
      savePref(LAYOUT_KEY, next);
      return next;
    });
  }, []);
  const [tracks, setTracks] = useState<TrackState>(() => ({ ...loadPref(TRACKS_KEY, DEFAULT_TRACKS), solo: null }));
  const updateTracks = useCallback((patch: Partial<TrackState>) => {
    setTracks((current) => {
      const next = { ...current, ...patch };
      savePref(TRACKS_KEY, { ...next, solo: null });
      return next;
    });
  }, []);

  const dubOn = workspace?.audio?.dubbed_enabled ?? true;
  const originalOn = workspace?.audio?.original_enabled ?? true;
  const music = useAudioTracks(editor, tracks.solo);
  // Any solo (built-in lane or music lane) silences every lane that is not soloed.
  const playback = usePlayback(workspace, {
    dubAudible: dubOn && (!music.anySolo || tracks.solo === "dub"),
    originalAudible: originalOn && (!music.anySolo || tracks.solo === "original"),
    musicAudible: music.audible,
    assetUrl: music.assetUrl,
  });
  seekRef.current = playback.seek;
  const { time, playing } = playback;

  const [filter, setFilter] = useState<CueFilter>("all");
  const [query, setQuery] = useState("");
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [thumbnails, setThumbnails] = useState<EditorThumbnails | null>(null);
  const timelineApi = useRef<TimelineApi | null>(null);

  useEffect(() => {
    getEditorThumbnails(jobId).then(setThumbnails).catch(() => undefined); // The filmstrip is decoration only.
  }, [jobId]);

  const clipSelected = useMemo(() => new Set(editor.clipSelection), [editor.clipSelection]);
  const subtitleNow = useMemo(() => segmentAt(segments, time, "subtitle"), [segments, time]);
  const timeRef = useRef(time);
  timeRef.current = time;
  const getPlayhead = useCallback(() => playback.videoRef.current?.currentTime ?? timeRef.current, [playback.videoRef]);

  const stepCue = useCallback(
    (direction: 1 | -1) => {
      const index = editor.selectedIndex;
      let next = segments[index + direction];
      if (index < 0) {
        const t0 = getPlayhead();
        next = direction === 1 ? segments.find((seg) => seg.start > t0 + 0.01)! : [...segments].reverse().find((seg) => seg.start < t0 - 0.01)!;
      }
      if (next) editor.select(next);
    },
    [editor, segments, getPlayhead],
  );

  // Pause while scrubbing; resume afterwards if it was playing.
  const wasPlaying = useRef(false);
  const onScrub = useCallback(
    (active: boolean) => {
      if (active) {
        wasPlaying.current = !(playback.videoRef.current?.paused ?? true);
        playback.pause();
      } else if (wasPlaying.current) {
        playback.play();
      }
    },
    [playback],
  );

  const openCue = useCallback(
    (seg: (typeof segments)[number]) => {
      editor.select(seg);
      updateLayout({ tab: "cues" });
      window.setTimeout(() => editor.textRef.current?.focus(), 0);
    },
    [editor, updateLayout],
  );

  /** From the review panel / timeline markers: select the cue, or seek when it no longer exists. */
  const jumpToCue = useCallback(
    (cueId: string, at: number) => {
      const seg = segments.find((item) => item.id === cueId);
      if (seg) editor.select(seg);
      else playback.seek(at);
    },
    [segments, editor, playback],
  );

  const deleteAny = useCallback(() => {
    const regions = workspace?.original_regions;
    if (editor.clipSelection.length) {
      editor.deleteClips();
    } else if (editor.selectedRegionId && regions) {
      editor.saveRegions(regions.filter((region) => region.id !== editor.selectedRegionId));
      editor.selectRegion(null);
    } else {
      void editor.deleteSelection();
    }
  }, [editor, workspace?.original_regions]);

  // ---- keyboard shortcuts ---------------------------------------------------------
  const keys = useRef({ editor, playback, stepCue, deleteAny, layout, updateLayout, getPlayhead });
  keys.current = { editor, playback, stepCue, deleteAny, layout, updateLayout, getPlayhead };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const k = keys.current;
      const target = event.target as HTMLElement | null;
      const typing =
        !!target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
      const mod = event.metaKey || event.ctrlKey;
      const key = event.key.toLowerCase();
      if (mod && key === "s") {
        event.preventDefault();
        void k.editor.flushAll();
        return;
      }
      if (typing) return; // Text fields keep their own keys (and ⌘Z).
      if (document.querySelector("[role=dialog]")) return; // A modal is open.
      if (mod && (key === "y" || (key === "z" && event.shiftKey))) {
        event.preventDefault();
        void k.editor.travel("redo");
      } else if (mod && key === "z") {
        event.preventDefault();
        void k.editor.travel("undo");
      } else if (mod && key === "b") {
        event.preventDefault();
        void k.editor.splitAt(k.getPlayhead());
      } else if (mod && key === "a") {
        event.preventDefault();
        k.editor.selectIds(k.editor.segments.map((seg) => seg.id));
      } else if (mod) {
        return;
      } else if (event.key === " ") {
        event.preventDefault();
        k.playback.togglePlay();
      } else if (key === "j" || key === "k" || key === "l") {
        event.preventDefault();
        k.playback.shuttle(key);
      } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const direction = event.key === "ArrowLeft" ? -1 : 1;
        if (event.shiftKey) k.playback.step(direction);
        else k.playback.stepFrames(direction);
      } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        k.stepCue(event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Home") {
        event.preventDefault();
        k.playback.seek(0);
      } else if (event.key === "End") {
        event.preventDefault();
        k.playback.seek(k.editor.workspace?.duration ?? 0);
      } else if (key === "s") {
        event.preventDefault();
        void k.editor.splitAt(k.getPlayhead());
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        k.deleteAny();
      } else if (key === "m") {
        void k.editor.toggleMute();
      } else if (key === "n") {
        k.updateLayout({ snapping: !k.layout.snapping });
      } else if (event.key === "+" || event.key === "=") {
        timelineApi.current?.zoomBy(1.5);
      } else if (event.key === "-" || event.key === "_") {
        timelineApi.current?.zoomBy(1 / 1.5);
      } else if (event.key === "\\") {
        timelineApi.current?.fit();
      } else if (event.key === "Escape") {
        k.editor.clearSelection();
      } else if (event.key === "Enter" && k.editor.selected) {
        event.preventDefault();
        k.updateLayout({ tab: "cues" });
        k.editor.textRef.current?.focus();
      } else if (event.key === "?") {
        setShortcutsOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ---- timeline height (drag the splitter) ---------------------------------------------
  const beginResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startY = event.clientY;
    const start = layout.timelineHeight;
    const move = (pointer: PointerEvent) =>
      setLayout((current) => ({ ...current, timelineHeight: Math.min(560, Math.max(180, start - (pointer.clientY - startY))) }));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setLayout((current) => {
        savePref(LAYOUT_KEY, current);
        return current;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const goBack = useCallback(async () => {
    if (await editor.flushAll()) onBack();
  }, [editor, onBack]);

  if (!workspace) {
    return (
      <div className="editor flex h-full items-center justify-center" style={{ padding: 0 }}>
        {editor.error ? (
          <div className="grid max-w-md gap-3">
            <InlineNotice tone="danger">{editor.error}</InlineNotice>
            <Button onClick={onBack}>← {L("Quay lại", "Back")}</Button>
          </div>
        ) : (
          <LoadingState label={L("Đang mở workspace…", "Opening workspace…")} />
        )}
      </div>
    );
  }

  const render = workspace.render;
  return (
    <div className="editor flex h-full min-h-0 flex-col overflow-hidden" style={{ padding: 0, gap: 0 }}>
      <Toolbar editor={editor} jobId={jobId} onBack={() => void goBack()} onShortcuts={() => setShortcutsOpen(true)} L={L} />

      {editor.error || editor.notice || (render.status === "failed" && render.error) ? (
        <div className="grid shrink-0 gap-1.5 border-b border-[var(--border)] bg-[var(--surface)] px-3 py-2">
          {editor.error ? (
            <Banner tone="danger" onClose={() => editor.setError(null)}>
              {editor.error}
            </Banner>
          ) : null}
          {editor.notice ? (
            <Banner tone="success" onClose={() => editor.setNotice(null)}>
              {editor.notice}
            </Banner>
          ) : null}
          {render.status === "failed" && render.error ? (
            <Banner tone="danger">
              {L("Render lỗi: ", "Render failed: ")}
              {render.error}
            </Banner>
          ) : null}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <LeftPanel
          tab={layout.tab}
          onTab={(tab) => updateLayout({ tab })}
          editor={editor}
          activeId={subtitleNow?.id ?? null}
          filter={filter}
          onFilter={setFilter}
          query={query}
          onQuery={setQuery}
          jobId={jobId}
          onOpenAssistant={onOpenAssistant}
          review={review}
          onReviewJump={jumpToCue}
          onReviewFocus={setReviewFocus}
          music={music}
          getPlayhead={getPlayhead}
          pausePreview={playback.pause}
          L={L}
        />
        <Preview
          workspace={workspace}
          playback={playback}
          subtitle={subtitleNow}
          showSubtitles={!tracks.subHidden}
          onMoveSubtitle={(position_y) => editor.saveStyle({ position_y })}
          onBlurChange={editor.saveBlur}
          onStepCue={stepCue}
        />
        <Inspector editor={editor} playback={playback} music={music} getPlayhead={getPlayhead} L={L} />
      </div>

      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label={t.ed_resize_timeline}
        title={t.ed_resize_timeline}
        onPointerDown={beginResize}
        className="group relative z-10 h-1.5 shrink-0 cursor-row-resize border-t border-[var(--border)] bg-[var(--surface)]"
      >
        <span className="absolute left-1/2 top-0.5 h-0.5 w-10 -translate-x-1/2 rounded-full bg-[var(--border-strong)] group-hover:bg-[var(--primary)]" />
      </div>
      <div className="shrink-0" style={{ height: layout.timelineHeight }}>
        <Timeline
          workspace={workspace}
          waveform={editor.waveform}
          thumbnails={thumbnails}
          time={time}
          playing={playing}
          zoom={layout.zoom}
          onZoom={(zoom) => updateLayout({ zoom })}
          apiRef={timelineApi}
          selectedIds={editor.selectedIds}
          primaryId={editor.selectedId}
          selectedRegionId={editor.selectedRegionId}
          tracks={tracks}
          onTracks={updateTracks}
          snapping={layout.snapping}
          onSnapping={(snapping) => updateLayout({ snapping })}
          linked={layout.linked}
          onLinked={(linked) => updateLayout({ linked })}
          busy={!!editor.busy || !!editor.batch}
          getPlayhead={getPlayhead}
          onSeek={playback.seek}
          onScrub={onScrub}
          onSelect={editor.select}
          onSelectIds={editor.selectIds}
          onClearSelection={editor.clearSelection}
          onSelectRegion={editor.selectRegion}
          onRegionsChange={editor.saveRegions}
          onMove={(updates) => void editor.moveSegments(updates)}
          onRetimeVoice={(id, patch) => void editor.retimeVoice(id, patch)}
          onAddCue={(at) => void editor.addCueAt(at)}
          onOpenCue={openCue}
          onSplit={() => void editor.splitAt(getPlayhead())}
          onDelete={deleteAny}
          onToggleDub={() => editor.saveAudio({ dubbed_enabled: !dubOn })}
          onToggleOriginal={() => editor.saveAudio({ original_enabled: !originalOn })}
          onToggleBlur={() => editor.saveBlur({ enabled: !workspace.blur.enabled })}
          onOpenBlur={() => updateLayout({ tab: "styles" })}
          markers={review.markers}
          focusRange={reviewFocus}
          onMarker={(cueId, at) => {
            jumpToCue(cueId, at);
            updateLayout({ tab: "review" });
          }}
          music={{
            tracks: music.tracks,
            assets: music.assets,
            selectedIds: clipSelected,
            solo: music.solo,
            audible: music.audible,
            ducking: music.ducking,
            onSolo: music.toggleSolo,
            onPatchTrack: music.patchTrack,
            onDeleteTrack: music.deleteTrack,
            onAddTrack: () => music.addTrack("music"),
            onSelect: editor.selectClips,
            onChange: editor.saveAudioTracks,
            onDropAsset: (trackId, asset, at) => music.addAsset(asset, at, trackId),
          }}
        />
      </div>

      <ShortcutsHelp open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </div>
  );
}

function Banner({ tone, onClose, children }: { tone: "danger" | "success"; onClose?: () => void; children: React.ReactNode }) {
  return (
    <div
      className={
        tone === "danger"
          ? "flex items-start gap-2 rounded-[var(--radius-control)] border border-[var(--danger-border)] bg-[var(--danger-soft)] px-3 py-1.5 text-[12.5px] text-[var(--danger)]"
          : "flex items-start gap-2 rounded-[var(--radius-control)] border border-[var(--success-border)] bg-[var(--success-soft)] px-3 py-1.5 text-[12.5px] text-[var(--success)]"
      }
    >
      <span className="min-w-0 flex-1 break-words">{children}</span>
      {onClose ? (
        <button type="button" aria-label="Close" onClick={onClose} className="shrink-0 opacity-70 hover:opacity-100">
          <X size={14} />
        </button>
      ) : null}
    </div>
  );
}
