import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { EditorBlur, EditorSegment, EditorWorkspace } from "../../types";
import { hexAlpha } from "../EditorStyle";
import { wordTimes } from "../../lib/karaoke";

/** Burned-in subtitle as the render will draw it; drag it vertically to move it. */
export function SubtitlePreview({
  workspace,
  segment,
  time,
  onMove,
}: {
  workspace: EditorWorkspace;
  segment: EditorSegment | null;
  time: number;
  onMove: (positionY: number) => void;
}) {
  const [dragY, setDragY] = useState<number | null>(null);
  const part = segment?.display.find((item) => time >= item.start && time < item.end) ?? segment?.display[0];
  if (!workspace.subtitle || !part) return null;
  // Local style wins so edits show before the server answers; line wrapping comes from the server.
  const style = { ...workspace.subtitle, ...workspace.style };
  const fontPx = Math.max(8, Math.round((style.font_size * workspace.width) / 512));
  const positionY = dragY ?? style.position_y;
  // Sizes are in video pixels (ASS PlayRes = video size); cqw scales them to the preview width.
  const px = (value: number) => `${(value / workspace.width) * 100}cqw`;
  const mode = !style.box_opacity ? "none" : style.box_style === "panel" ? "panel" : "classic";
  const padding = mode === "panel" ? (style.box_padding ?? 0) * fontPx : mode === "classic" ? style.outline : 0;
  const boxHeight = part.lines.length * fontPx + 2 * padding;
  const stroke = mode === "classic" ? 0 : style.outline;
  const anchorX = style.position_x <= 0.33 ? "0%" : style.position_x >= 0.67 ? "-100%" : "-50%";
  const align = style.position_x <= 0.33 ? "left" : style.position_x >= 0.67 ? "right" : "center";
  const karaoke = style.karaoke_mode === "highlight" || style.karaoke_mode === "reveal" ? style.karaoke_mode : null;
  let lines: React.ReactNode[] = part.lines;
  if (karaoke && segment) {
    // The render lays words on the voice; here, on the part of the page the voice covers.
    const voiced = segment.audio_url && !segment.muted && segment.audio_text === segment.text && segment.audio_duration > 0;
    const spanStart = voiced ? Math.max(part.start, segment.audio_start) : part.start;
    const spanEnd = voiced ? Math.min(part.end, segment.audio_start + segment.audio_duration) : part.end;
    const words = part.lines.flatMap((line) => line.split(/\s+/).filter(Boolean));
    const times = spanEnd > spanStart ? wordTimes(words, spanStart, spanEnd) : wordTimes(words, part.start, part.end);
    let index = 0;
    lines = part.lines.map((line) =>
      line
        .split(/\s+/)
        .filter(Boolean)
        .map((word, position) => {
          const [start, end] = times[index++];
          const hidden = karaoke === "reveal" && time < start;
          const active = karaoke === "highlight" && time >= start && time < end;
          return (
            <span key={position}>
              {position ? " " : ""}
              <span style={{ visibility: hidden ? "hidden" : undefined, color: active ? style.karaoke_color ?? "#FFE14D" : undefined }}>
                {word}
              </span>
            </span>
          );
        }),
    );
  }

  const beginDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const frame = event.currentTarget.closest(".editor-video")?.getBoundingClientRect();
    if (!frame) return;
    let latest = positionY;
    const onMovePointer = (move: PointerEvent) => {
      latest = Math.min(0.95, Math.max(0.05, (move.clientY - frame.top) / frame.height));
      setDragY(latest);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMovePointer);
      window.removeEventListener("pointerup", onUp);
      onMove(Math.round(latest * 1000) / 1000);
      setDragY(null);
    };
    window.addEventListener("pointermove", onMovePointer);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <div
      className={`editor-sub-preview${dragY !== null ? " dragging" : ""}`}
      onPointerDown={beginDrag}
      style={{
        left: `${style.position_x * 100}%`,
        top: `${positionY * 100}%`,
        // Same as ASS \an5\pos: the block's centre sits on position_y whatever the line count.
        transform: `translate(${anchorX}, -50%)`,
        textAlign: align,
        fontFamily: `"${style.font_name}", Arial, sans-serif`,
        fontWeight: style.bold ? 700 : 400,
        fontSize: px(fontPx),
        color: style.text_color,
        padding: px(padding),
        background: mode === "none" ? "transparent" : hexAlpha(style.box_color, style.box_opacity),
        borderRadius: mode === "panel" ? px(((style.box_radius ?? 0) * boxHeight) / 2) : 0,
        WebkitTextStroke: stroke ? `${px(stroke * 2)} ${style.outline_color}` : undefined,
        textShadow: style.shadow && mode !== "classic" ? `${px(style.shadow)} ${px(style.shadow)} 0 rgba(0, 0, 0, 0.55)` : undefined,
      }}
    >
      {lines.map((line, index) => (
        <div key={index}>{line}</div>
      ))}
    </div>
  );
}

/** Hard-sub blur box over the video: click to select, drag to move, corner to resize. */
export function BlurBox({
  blur,
  onChange,
  hint,
}: {
  blur: EditorBlur;
  onChange: (blur: Partial<EditorBlur>) => void;
  hint: string;
}) {
  const [live, setLive] = useState(blur);
  // The frame and handle only show once the box is clicked, so they don't cover the video.
  const [active, setActive] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => setLive(blur), [blur]);
  useEffect(() => {
    if (!active) return;
    const onDown = (event: PointerEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setActive(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActive(false);
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [active]);
  if (!blur.enabled) return null;

  const begin = (mode: "move" | "resize") => (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const frame = event.currentTarget.closest(".editor-video")?.getBoundingClientRect();
    if (!frame) return;
    setActive(true);
    const origin = { x: event.clientX, y: event.clientY, box: live };
    let latest = live;
    const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
    const onMove = (move: PointerEvent) => {
      const dx = (move.clientX - origin.x) / frame.width;
      const dy = (move.clientY - origin.y) / frame.height;
      const box = origin.box;
      latest =
        mode === "move"
          ? { ...box, x: clamp(box.x + dx, 0.02, 0.98), y: clamp(box.y + dy, 0.02, 0.98) }
          : {
              ...box,
              // Resize from the bottom-right corner while keeping the top-left fixed.
              width: clamp(box.width + dx, 0.04, 1),
              height: clamp(box.height + dy, 0.03, 0.8),
              x: clamp(box.x + dx / 2, 0.02, 0.98),
              y: clamp(box.y + dy / 2, 0.02, 0.98),
            };
      setLive(latest);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (latest === origin.box) return; // A plain click only selects the box.
      const round = (value: number) => Math.round(value * 1000) / 1000;
      onChange({ x: round(latest.x), y: round(latest.y), width: round(latest.width), height: round(latest.height) });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const strength = Math.max(1, (live.strength / 50) * 10);
  const filter = live.style === "box" ? `blur(${strength}px)` : `blur(${strength * 0.6}px)`;
  return (
    <div
      ref={boxRef}
      className={`editor-blur${active ? " active" : ""}`}
      title={active ? undefined : hint}
      style={{
        left: `${(live.x - live.width / 2) * 100}%`,
        top: `${(live.y - live.height / 2) * 100}%`,
        width: `${live.width * 100}%`,
        height: `${live.height * 100}%`,
        backdropFilter: filter,
        WebkitBackdropFilter: filter,
      }}
      onPointerDown={begin("move")}
    >
      {active ? <div className="editor-blur-handle" onPointerDown={begin("resize")} /> : null}
    </div>
  );
}
