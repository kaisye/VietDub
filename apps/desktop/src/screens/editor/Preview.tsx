import clsx from "clsx";
import { ChevronFirst, ChevronLast, Headphones, Loader2, Maximize2, Pause, Play, SkipBack, SkipForward, StepBack, StepForward } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { getEditorPreviewVideo, outputUrl } from "../../api";
import { useT } from "../../i18n";
import type { EditorBlur, EditorSegment, EditorWorkspace } from "../../types";
import { AUDIO_DELAY_MAX_MS, audioDelayMs, onAudioDelayChange, reportsOutputLatency, setAudioDelayMs } from "./audioDelay";
import { BlurBox, SubtitlePreview } from "./overlays";
import { TextOverlayLayer } from "./text/TextOverlayLayer";
import type { Playback } from "./usePlayback";
import { formatTimecode } from "./utils";

/** Center stage: the video at its aspect ratio with blur / subtitle overlays, plus the transport bar. */
export function Preview({
  workspace,
  playback,
  subtitle,
  showSubtitles,
  onMoveSubtitle,
  onBlurChange,
  onStepCue,
  textSelectedId,
  onSelectText,
  onMoveText,
  onEditText,
}: {
  workspace: EditorWorkspace;
  playback: Playback;
  subtitle: EditorSegment | null;
  showSubtitles: boolean;
  onMoveSubtitle: (positionY: number) => void;
  onBlurChange: (blur: Partial<EditorBlur>) => void;
  onStepCue: (direction: 1 | -1) => void;
  textSelectedId: string | null;
  onSelectText: (id: string) => void;
  onMoveText: (id: string, x: number, y: number) => void;
  onEditText: (id: string) => void;
}) {
  const { t } = useT();
  const stageRef = useRef<HTMLDivElement | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const aspect = (workspace.width || 16) / (workspace.height || 9);
  const source = usePreviewSource(workspace);

  // Fit the frame inside the stage both ways, so the blur box maps 1:1 onto the picture.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const update = () => {
      const width = Math.max(0, el.clientWidth - 24);
      const height = Math.max(0, el.clientHeight - 24);
      const fitWidth = Math.min(width, height * aspect);
      setBox({ width: fitWidth, height: fitWidth / aspect });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [aspect]);

  const { videoRef, previewRef, time, playing, rate, togglePlay, stepFrames, seek, videoHandlers } = playback;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-[var(--surface)]">
      <div ref={stageRef} className="relative flex min-h-0 flex-1 items-center justify-center bg-[var(--navy-950)]">
        <div
          ref={frameRef}
          className="editor-video !rounded-md shadow-[0_10px_40px_-12px_rgba(0,0,0,0.6)]"
          style={{ width: box.width, height: box.height }}
        >
          <video
            ref={videoRef}
            src={source ? (outputUrl(source) ?? undefined) : undefined}
            preload="auto"
            {...videoHandlers}
            onClick={togglePlay}
          />
          {!source ? (
            <div className="absolute inset-0 z-10 flex items-center justify-center gap-2 bg-black/70 px-6 text-center text-[13px] text-white">
              <Loader2 size={16} className="shrink-0 animate-spin" />
              <span>{t.ed_preview_preparing}</span>
            </div>
          ) : null}
          <BlurBox blur={workspace.blur} onChange={onBlurChange} hint={t.ed_blur_hint} />
          {showSubtitles ? (
            <SubtitlePreview workspace={workspace} segment={subtitle} time={time} onMove={onMoveSubtitle} />
          ) : null}
          <TextOverlayLayer
            workspace={workspace}
            time={time}
            selectedId={textSelectedId}
            onSelect={onSelectText}
            onMove={onMoveText}
            onEdit={onEditText}
          />
          <audio ref={previewRef} preload="none" />
        </div>
        {rate !== 1 ? (
          <span className="absolute right-3 top-3 rounded-full bg-black/55 px-2 py-0.5 font-mono text-[11px] font-semibold text-white">
            {rate}×
          </span>
        ) : null}
      </div>

      <div className="@container grid h-12 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 border-t border-[var(--border)] px-3">
        <div className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap font-mono text-[12px] tabular-nums">
          <span className="font-semibold text-[var(--primary)]" title={formatTimecode(workspace.duration)}>{formatTimecode(time)}</span>
          <span className="hidden text-[var(--text-tertiary)] @[640px]:inline">/ {formatTimecode(workspace.duration)}</span>
        </div>
        <div className="flex items-center gap-0.5">
          <TransportButton label={`${t.ed_go_start} (Home)`} onClick={() => seek(0)} wide>
            <ChevronFirst size={16} />
          </TransportButton>
          <TransportButton label={`${t.ed_prev_cue} (↑)`} onClick={() => onStepCue(-1)}>
            <SkipBack size={15} />
          </TransportButton>
          <TransportButton label={`${t.ed_prev_frame} (←)`} onClick={() => stepFrames(-1)}>
            <StepBack size={15} />
          </TransportButton>
          <button
            type="button"
            onClick={togglePlay}
            title={`${playing ? t.ed_pause : t.ed_play} (Space)`}
            aria-label={playing ? t.ed_pause : t.ed_play}
            className="mx-1 flex h-9 w-9 items-center justify-center rounded-full bg-[var(--primary)] text-white shadow-[var(--shadow-primary)] transition hover:bg-[var(--primary-hover)]"
          >
            {playing ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" className="ml-0.5" />}
          </button>
          <TransportButton label={`${t.ed_next_frame} (→)`} onClick={() => stepFrames(1)}>
            <StepForward size={15} />
          </TransportButton>
          <TransportButton label={`${t.ed_next_cue} (↓)`} onClick={() => onStepCue(1)}>
            <SkipForward size={15} />
          </TransportButton>
          <TransportButton label={`${t.ed_go_end} (End)`} onClick={() => seek(workspace.duration)} wide>
            <ChevronLast size={16} />
          </TransportButton>
        </div>
        <div className="flex items-center justify-end gap-1">
          <AudioDelayControl />
          <TransportButton
            label={t.ed_fullscreen}
            onClick={() => void frameRef.current?.requestFullscreen?.().catch(() => undefined)}
          >
            <Maximize2 size={14} />
          </TransportButton>
        </div>
      </div>
    </div>
  );
}

/**
 * Asks the backend for a playable source. H.264 sources come back at once; AV1/VP9
 * ones (which WebKit on M1/M2 Macs cannot decode) wait for a one-time H.264 proxy.
 */
function usePreviewSource(workspace: EditorWorkspace): string | null {
  const [state, setState] = useState({ key: "", url: "" });
  const key = `${workspace.job_id}:${workspace.video_url}`;
  useEffect(() => {
    let cancelled = false;
    getEditorPreviewVideo(workspace.job_id)
      .then((data) => !cancelled && setState({ key, url: data.url }))
      // Fall back to the original: it may still play (e.g. in a browser with AV1 support).
      .catch(() => !cancelled && setState({ key, url: workspace.video_url }));
    return () => {
      cancelled = true;
    };
  }, [key, workspace.job_id, workspace.video_url]);
  return state.key === key ? state.url : null;
}

/** Manual output-latency compensation for the preview (Bluetooth headphones in WebKit). */
function AudioDelayControl() {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const [ms, setMs] = useState(audioDelayMs);
  const boxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => onAudioDelayChange(setMs), []);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        title={t.ed_audio_delay}
        aria-label={t.ed_audio_delay}
        onClick={() => setOpen((value) => !value)}
        className={clsx(
          "flex h-8 items-center gap-1 rounded-[var(--radius-sm)] px-1.5 text-[11px] font-medium tabular-nums transition-colors hover:bg-[var(--surface-hover)]",
          ms > 0 ? "text-[var(--primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
        )}
      >
        <Headphones size={14} />
        {ms > 0 ? `${ms} ms` : null}
      </button>
      {open ? (
        <div className="absolute bottom-10 right-0 z-30 w-72 rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] p-3 text-[12px] shadow-[var(--shadow-float)]">
          <div className="mb-1 flex items-center justify-between font-semibold text-[var(--text-primary)]">
            <span>{t.ed_audio_delay}</span>
            <span className="font-mono tabular-nums text-[var(--primary)]">{ms} ms</span>
          </div>
          <p className="mb-2 leading-snug text-[var(--text-secondary)]">
            {reportsOutputLatency() ? t.ed_audio_delay_auto : t.ed_audio_delay_hint}
          </p>
          <input
            type="range"
            min={0}
            max={AUDIO_DELAY_MAX_MS}
            step={10}
            value={ms}
            onChange={(event) => setAudioDelayMs(Number(event.target.value))}
            className="w-full accent-[var(--primary)]"
          />
          <div className="mt-2 flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setAudioDelayMs(200)}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] px-2 py-1 hover:bg-[var(--surface-hover)]"
            >
              {t.ed_audio_delay_bt} · 200 ms
            </button>
            <button
              type="button"
              onClick={() => setAudioDelayMs(0)}
              className="rounded-[var(--radius-sm)] px-2 py-1 text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
            >
              {t.ed_audio_delay_reset}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** `wide`: only shown when the preview is wide enough (container query). */
function TransportButton({
  label,
  onClick,
  children,
  active,
  wide,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  active?: boolean;
  wide?: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className={clsx(
        "h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] transition-colors hover:bg-[var(--surface-hover)]",
        wide ? "hidden @[520px]:flex" : "flex",
        active ? "text-[var(--primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
      )}
    >
      {children}
    </button>
  );
}
