import clsx from "clsx";
import { ChevronFirst, ChevronLast, Maximize2, Pause, Play, SkipBack, SkipForward, StepBack, StepForward } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { outputUrl } from "../../api";
import { useT } from "../../i18n";
import type { EditorBlur, EditorSegment, EditorWorkspace } from "../../types";
import { BlurBox, SubtitlePreview } from "./overlays";
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
}: {
  workspace: EditorWorkspace;
  playback: Playback;
  subtitle: EditorSegment | null;
  showSubtitles: boolean;
  onMoveSubtitle: (positionY: number) => void;
  onBlurChange: (blur: Partial<EditorBlur>) => void;
  onStepCue: (direction: 1 | -1) => void;
}) {
  const { t } = useT();
  const stageRef = useRef<HTMLDivElement | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const aspect = (workspace.width || 16) / (workspace.height || 9);

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
            src={outputUrl(workspace.video_url) ?? undefined}
            preload="auto"
            {...videoHandlers}
            onClick={togglePlay}
          />
          <BlurBox blur={workspace.blur} onChange={onBlurChange} hint={t.ed_blur_hint} />
          {showSubtitles ? (
            <SubtitlePreview workspace={workspace} segment={subtitle} time={time} onMove={onMoveSubtitle} />
          ) : null}
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
