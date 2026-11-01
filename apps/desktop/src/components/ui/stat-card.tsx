import clsx from "clsx";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import { Skeleton } from "./feedback";

export type StatAccent = "primary" | "cyan" | "violet" | "pink" | "success" | "warning";

const accents: Record<StatAccent, string> = {
  primary: "bg-[var(--primary-soft)] text-[var(--primary)]",
  cyan: "bg-[var(--accent-cyan-soft)] text-[var(--accent-cyan)]",
  violet: "bg-[var(--accent-violet-soft)] text-[var(--accent-violet)]",
  pink: "bg-[var(--accent-pink-soft)] text-[var(--accent-pink)]",
  success: "bg-[var(--success-soft)] text-[var(--success)]",
  warning: "bg-[var(--warning-soft)] text-[var(--warning)]",
};

/**
 * KPI tile: label, big value, optional delta (positive = up/green unless
 * `invertDelta`), icon chip and footnote. `loading` shows skeletons.
 */
export function StatCard({
  label,
  value,
  delta,
  deltaLabel,
  invertDelta = false,
  icon,
  accent = "primary",
  hint,
  loading = false,
  onClick,
  className,
  children,
}: {
  label: ReactNode;
  value: ReactNode;
  delta?: number | null;
  deltaLabel?: ReactNode;
  invertDelta?: boolean;
  icon?: ReactNode;
  accent?: StatAccent;
  hint?: ReactNode;
  loading?: boolean;
  onClick?: () => void;
  className?: string;
  children?: ReactNode;
}) {
  const hasDelta = typeof delta === "number" && Number.isFinite(delta);
  const up = hasDelta && (delta as number) >= 0;
  const good = invertDelta ? !up : up;
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={clsx(
        "flex min-w-0 flex-col gap-3 rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] p-5 text-left shadow-[var(--shadow-panel)]",
        onClick && "transition hover:border-[color-mix(in_srgb,var(--primary)_40%,var(--border))] hover:shadow-[var(--shadow-float)]",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="text-[13px] font-medium text-[var(--text-secondary)]">{label}</span>
        {icon ? (
          <span className={clsx("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", accents[accent])}>{icon}</span>
        ) : null}
      </div>
      {loading ? (
        <div className="grid gap-2">
          <Skeleton className="h-7 w-24" />
          <Skeleton className="h-3 w-32" />
        </div>
      ) : (
        <div className="min-w-0">
          <div className="truncate text-[26px] font-semibold leading-tight tracking-[-0.02em] text-[var(--text-primary)] tabular-nums">
            {value}
          </div>
          {hasDelta || hint ? (
            <div className="mt-1.5 flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
              {hasDelta ? (
                <span
                  className={clsx(
                    "inline-flex items-center gap-0.5 rounded-full px-1.5 py-px font-semibold tabular-nums",
                    good ? "bg-[var(--success-soft)] text-[var(--success)]" : "bg-[var(--danger-soft)] text-[var(--danger)]",
                  )}
                >
                  {up ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                  {Math.abs(delta as number).toLocaleString(undefined, { maximumFractionDigits: 1 })}%
                </span>
              ) : null}
              {deltaLabel ? <span>{deltaLabel}</span> : null}
              {hint ? <span className="truncate">{hint}</span> : null}
            </div>
          ) : null}
        </div>
      )}
      {children}
    </Tag>
  );
}
