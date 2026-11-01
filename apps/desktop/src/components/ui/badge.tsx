import clsx from "clsx";
import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "primary" | "success" | "warning" | "danger" | "info" | "violet" | "cyan" | "pink";

const tones: Record<BadgeTone, string> = {
  neutral: "border-[var(--border)] bg-[var(--surface-muted)] text-[var(--text-secondary)]",
  primary: "border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] text-[var(--primary-hover)]",
  success: "border-[var(--success-border)] bg-[var(--success-soft)] text-[var(--success)]",
  warning: "border-[var(--warning-border)] bg-[var(--warning-soft)] text-[var(--warning)]",
  danger: "border-[var(--danger-border)] bg-[var(--danger-soft)] text-[var(--danger)]",
  info: "border-[var(--info-border)] bg-[var(--info-soft)] text-[var(--info)]",
  violet: "border-transparent bg-[var(--accent-violet-soft)] text-[var(--accent-violet)]",
  cyan: "border-transparent bg-[var(--accent-cyan-soft)] text-[var(--accent-cyan)]",
  pink: "border-transparent bg-[var(--accent-pink-soft)] text-[var(--accent-pink)]",
};

export function Badge({
  children,
  tone = "neutral",
  size = "md",
  dot = false,
  icon,
  className,
  title,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  size?: "sm" | "md";
  dot?: boolean;
  icon?: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex max-w-full shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border font-medium leading-none",
        size === "sm" ? "px-1.5 py-[3px] text-[10.5px]" : "px-2 py-1 text-[11.5px]",
        tones[tone],
        className,
      )}
    >
      {dot ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" aria-hidden="true" /> : null}
      {icon}
      <span className="truncate">{children}</span>
    </span>
  );
}

/** Small colored dot for live status (online / connecting / offline). */
export function StatusDot({ tone = "success", pulse = false }: { tone?: "success" | "warning" | "danger" | "neutral" | "primary"; pulse?: boolean }) {
  const color = {
    success: "bg-[var(--success)]",
    warning: "bg-[var(--warning)]",
    danger: "bg-[var(--danger)]",
    neutral: "bg-[var(--text-tertiary)]",
    primary: "bg-[var(--primary)]",
  }[tone];
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0" aria-hidden="true">
      {pulse ? <span className={clsx("absolute inset-0 animate-ping rounded-full opacity-60", color)} /> : null}
      <span className={clsx("relative inline-flex h-2 w-2 rounded-full", color)} />
    </span>
  );
}
