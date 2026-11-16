import clsx from "clsx";
import { useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { EditorTextOverlay, EditorWorkspace } from "../../../types";
import { overlayStyle, overlayVisible } from "./model";

/**
 * Text boxes on the preview frame, drawn as the render will: click selects, drag moves
 * (committed on release), double-click jumps to the text field in the inspector.
 */
export function TextOverlayLayer({
  workspace,
  time,
  selectedId,
  onSelect,
  onMove,
  onEdit,
}: {
  workspace: EditorWorkspace;
  time: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onMove: (id: string, x: number, y: number) => void;
  onEdit: (id: string) => void;
}) {
  const [drag, setDrag] = useState<{ id: string; x: number; y: number } | null>(null);
  const items = (workspace.text_overlays ?? []).filter((item) => overlayVisible(item, time) || item.id === drag?.id);
  if (!items.length) return null;

  const begin = (item: EditorTextOverlay) => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(item.id);
    const frame = event.currentTarget.closest(".editor-video")?.getBoundingClientRect();
    if (!frame) return;
    const origin = { x: event.clientX, y: event.clientY };
    let latest = { x: item.x, y: item.y };
    let moved = false;
    const clamp = (value: number) => Math.min(1, Math.max(0, value));
    const onPointerMove = (move: PointerEvent) => {
      const dx = move.clientX - origin.x;
      const dy = move.clientY - origin.y;
      if (!moved && Math.hypot(dx, dy) < 3) return; // A click, not a drag.
      moved = true;
      latest = { x: clamp(item.x + dx / frame.width), y: clamp(item.y + dy / frame.height) };
      setDrag({ id: item.id, ...latest });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onUp);
      setDrag(null);
      if (moved) onMove(item.id, Math.round(latest.x * 1000) / 1000, Math.round(latest.y * 1000) / 1000);
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <>
      {items.map((item) => {
        const live = drag?.id === item.id ? { ...item, x: drag.x, y: drag.y } : item;
        const { box, line } = overlayStyle(live, workspace);
        return (
          <div
            key={item.id}
            className={clsx("editor-text-box", item.id === selectedId && "selected")}
            style={box}
            onPointerDown={begin(item)}
            onDoubleClick={(event) => {
              event.stopPropagation();
              onEdit(item.id);
            }}
          >
            {item.text.split("\n").map((text, index) => (
              <div key={index}>
                <span style={line}>{text || " "}</span>
              </div>
            ))}
          </div>
        );
      })}
    </>
  );
}
