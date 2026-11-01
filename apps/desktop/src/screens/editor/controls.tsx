import clsx from "clsx";
import { Minus, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

/** Small uppercase-free section heading used inside the side panels. */
export function PanelSection({
  title,
  action,
  children,
  className,
}: {
  title: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx("border-b border-[var(--border-subtle)] px-4 py-3.5 last:border-b-0", className)}>
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <h3 className="text-[12.5px] font-semibold text-[var(--text-primary)]">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Kbd({ children, dark = false }: { children: ReactNode; dark?: boolean }) {
  return (
    <kbd
      className={clsx(
        "inline-flex min-w-[20px] items-center justify-center rounded-[5px] border px-1.5 py-px font-mono text-[11px] font-medium leading-4",
        dark
          ? "border-white/20 bg-white/10 text-white/85"
          : "border-[var(--border)] bg-[var(--surface-muted)] text-[var(--text-secondary)] shadow-[0_1px_0_var(--border)]",
      )}
    >
      {children}
    </kbd>
  );
}

/** Range input that commits on release (one undo step, one request). */
export function RangeField({
  label,
  value,
  min,
  max,
  step = 1,
  format,
  disabled,
  onCommit,
  title,
}: {
  label?: ReactNode;
  value: number;
  min: number;
  max: number;
  step?: number;
  format?: (value: number) => string;
  disabled?: boolean;
  onCommit: (value: number) => void;
  title?: string;
}) {
  const [live, setLive] = useState(value);
  useEffect(() => setLive(value), [value]);
  const commit = () => {
    if (live !== value) onCommit(live);
  };
  return (
    <label className={clsx("grid gap-1.5", disabled && "opacity-50")} title={title}>
      {label ? (
        <span className="flex items-center justify-between gap-3 text-[12px] font-medium text-[var(--text-secondary)]">
          {label}
          <output className="font-mono text-[11.5px] text-[var(--text-primary)]">{format ? format(live) : live}</output>
        </span>
      ) : null}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={Math.min(live, max)}
        disabled={disabled}
        onChange={(e) => setLive(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
        className="w-full accent-[var(--primary)]"
      />
    </label>
  );
}

/** Seconds input with −/+ nudges (0.1 s). */
export function TimeField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const nudge = (delta: number) => onChange(Math.max(0, Number(value) + delta).toFixed(2));
  return (
    <label className="grid min-w-0 gap-1">
      <span className="text-[11.5px] font-medium text-[var(--text-secondary)]">{label}</span>
      <span className="flex h-8 items-stretch overflow-hidden rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface)] focus-within:border-[var(--primary)] focus-within:shadow-[var(--shadow-focus)]">
        <button
          type="button"
          className="flex w-7 shrink-0 items-center justify-center text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
          onClick={() => nudge(-0.1)}
          aria-label="-0.1s"
        >
          <Minus size={13} />
        </button>
        <input
          value={value}
          inputMode="decimal"
          onChange={(e) => onChange(e.target.value)}
          className="w-full min-w-0 border-x border-[var(--border-subtle)] bg-transparent text-center font-mono text-[12.5px] outline-none"
        />
        <button
          type="button"
          className="flex w-7 shrink-0 items-center justify-center text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
          onClick={() => nudge(0.1)}
          aria-label="+0.1s"
        >
          <Plus size={13} />
        </button>
      </span>
    </label>
  );
}

/** Compact key/value line for the inspector. */
export function InfoRow({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-[12.5px]">
      <span className="text-[var(--text-secondary)]">{label}</span>
      <span className="min-w-0 truncate text-right font-medium text-[var(--text-primary)]">{children}</span>
    </div>
  );
}
