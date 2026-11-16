import { AlignCenter, AlignLeft, AlignRight, Bold, Copy, Trash2 } from "lucide-react";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Button } from "../../../components/ui";
import { useT } from "../../../i18n";
import type { EditorTextOverlay, EditorWorkspace } from "../../../types";
import { PanelSection, RangeField } from "../controls";
import { SecondsField } from "../music/ClipInspector";
import type { EditorController } from "../useEditor";
import { round3 } from "../utils";
import { TEXT_MIN_LENGTH } from "./model";

/** Right inspector for the selected text box. */
export function TextInspector({ editor }: { editor: EditorController }) {
  const { t } = useT();
  const workspace = editor.workspace as EditorWorkspace;
  const items = workspace.text_overlays ?? [];
  const item = items.find((other) => other.id === editor.textSelection);
  const [text, setText] = useState(item?.text ?? "");
  const timer = useRef<number | null>(null);
  useEffect(() => setText(item?.text ?? ""), [item?.id, item?.text]);
  useEffect(() => () => void (timer.current && window.clearTimeout(timer.current)), []);
  if (!item) return null;

  const update = (patch: Partial<EditorTextOverlay>, kind = `text:${item.id}`, coalesce = false) => {
    const next = { ...item, ...patch };
    next.start = Math.max(0, Math.min(next.start, workspace.duration - TEXT_MIN_LENGTH));
    next.end = Math.max(next.start + TEXT_MIN_LENGTH, Math.min(next.end, workspace.duration));
    editor.saveTextOverlays(
      items.map((other) => (other.id === item.id ? next : other)),
      kind,
      coalesce,
    );
  };
  // Typing saves after a short pause (one undo step per burst); an empty box is never saved.
  const typed = (value: string) => {
    setText(value);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (value.trim() && value !== item.text) update({ text: value }, `text-content:${item.id}`, true);
    }, 500);
  };
  const duplicate = () => {
    const length = item.end - item.start;
    const start = Math.min(item.end, workspace.duration - TEXT_MIN_LENGTH);
    const copy = {
      ...item,
      id: `txt-${Math.random().toString(36).slice(2, 10)}`,
      start: round3(start),
      end: round3(Math.min(workspace.duration, start + length)),
    };
    editor.saveTextOverlays([...items, copy], "add-text");
    editor.selectText(copy.id);
  };

  return (
    <>
      <PanelSection title={t.tx_content}>
        <textarea
          id="editor-text-overlay-input"
          value={text}
          rows={3}
          onChange={(event) => typed(event.target.value)}
          onBlur={() => {
            if (timer.current) window.clearTimeout(timer.current);
            if (text.trim() && text !== item.text) update({ text }, `text-content:${item.id}`, true);
            else if (!text.trim()) setText(item.text);
          }}
          className="w-full resize-y rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-[13px] leading-snug outline-none focus:border-[var(--primary)] focus:shadow-[var(--shadow-focus)]"
        />
        <p className="mt-1.5 text-[11.5px] text-[var(--text-tertiary)]">{t.tx_hint}</p>
      </PanelSection>

      <PanelSection title={t.tx_timing}>
        <div className="grid grid-cols-2 gap-2">
          <SecondsField label={t.mu_start} value={item.start} onCommit={(start) => update({ start, end: start + (item.end - item.start) })} />
          <SecondsField label={t.tx_end} value={item.end} onCommit={(end) => update({ end })} />
        </div>
      </PanelSection>

      <PanelSection title={t.tx_style}>
        <div className="grid gap-3">
          <RangeField
            label={t.tx_size}
            value={Math.round(item.size * 1000)}
            min={20}
            max={300}
            format={(value) => `${(value / 10).toFixed(1)}%`}
            onCommit={(value) => update({ size: value / 1000 })}
          />
          <div className="grid grid-cols-2 gap-2">
            <ColorField label={t.tx_color} value={item.color} onChange={(color) => update({ color }, `text-color:${item.id}`, true)} />
            <ColorField label={t.tx_bg} value={item.bg_color} onChange={(bg_color) => update({ bg_color }, `text-bg:${item.id}`, true)} />
          </div>
          <RangeField
            label={t.tx_bg_opacity}
            value={Math.round(item.bg_opacity * 100)}
            min={0}
            max={100}
            format={(value) => `${value}%`}
            onCommit={(value) => update({ bg_opacity: value / 100 })}
          />
          <div className="flex items-center justify-between gap-2">
            <div className="flex rounded-[var(--radius-control)] border border-[var(--border)] p-0.5">
              <Segment active={item.bold} label={t.tx_bold} onClick={() => update({ bold: !item.bold })}>
                <Bold size={14} />
              </Segment>
            </div>
            <div className="flex rounded-[var(--radius-control)] border border-[var(--border)] p-0.5">
              <Segment active={item.align === "left"} label={t.tx_left} onClick={() => update({ align: "left" })}>
                <AlignLeft size={14} />
              </Segment>
              <Segment active={item.align === "center"} label={t.tx_center} onClick={() => update({ align: "center" })}>
                <AlignCenter size={14} />
              </Segment>
              <Segment active={item.align === "right"} label={t.tx_right} onClick={() => update({ align: "right" })}>
                <AlignRight size={14} />
              </Segment>
            </div>
          </div>
        </div>
      </PanelSection>

      <PanelSection title={t.tx_position}>
        <div className="grid grid-cols-3 gap-1.5">
          {([
            [t.tx_top, 0.12],
            [t.tx_middle, 0.5],
            [t.tx_bottom, 0.85],
          ] as const).map(([label, y]) => (
            <Button key={label} size="xs" variant="secondary" onClick={() => update({ x: 0.5, y, align: "center" })}>
              {label}
            </Button>
          ))}
        </div>
      </PanelSection>

      <PanelSection title={t.tx_actions}>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={duplicate}>
            <Copy size={14} /> {t.tx_duplicate}
          </Button>
          <Button size="sm" variant="danger" onClick={editor.deleteText}>
            <Trash2 size={14} /> {t.tx_delete}
          </Button>
        </div>
      </PanelSection>
    </>
  );
}

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="grid gap-1">
      <span className="text-[11.5px] font-medium text-[var(--text-secondary)]">{label}</span>
      <span className="flex h-8 items-center gap-2 rounded-[var(--radius-control)] border border-[var(--border)] px-1.5">
        <input
          type="color"
          value={value}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
          className="h-6 w-7 cursor-pointer rounded border-0 bg-transparent p-0"
        />
        <span className="font-mono text-[11.5px] text-[var(--text-secondary)]">{value}</span>
      </span>
    </label>
  );
}

function Segment({ active, label, onClick, children }: { active: boolean; label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className={clsx(
        "flex h-7 w-7 items-center justify-center rounded-[calc(var(--radius-control)-2px)]",
        active ? "bg-[var(--primary)] text-white" : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]",
      )}
    >
      {children}
    </button>
  );
}
