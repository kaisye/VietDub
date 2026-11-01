import clsx from "clsx";
import { AlertCircle, CheckCircle2, Info, Loader2, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

export type NoticeTone = "info" | "success" | "warning" | "danger";

const noticeTone: Record<NoticeTone, { className: string; icon: typeof Info; iconClass: string }> = {
  info: { className: "border-[var(--primary-soft-strong)] bg-[var(--primary-soft)]", icon: Info, iconClass: "text-[var(--primary)]" },
  success: { className: "border-[var(--success-border)] bg-[var(--success-soft)]", icon: CheckCircle2, iconClass: "text-[var(--success)]" },
  warning: { className: "border-[var(--warning-border)] bg-[var(--warning-soft)]", icon: TriangleAlert, iconClass: "text-[var(--warning)]" },
  danger: { className: "border-[var(--danger-border)] bg-[var(--danger-soft)]", icon: AlertCircle, iconClass: "text-[var(--danger)]" },
};

export function InlineNotice({
  title,
  children,
  tone = "info",
  action,
  className,
}: {
  title?: ReactNode;
  children?: ReactNode;
  tone?: NoticeTone;
  action?: ReactNode;
  className?: string;
}) {
  const Icon = noticeTone[tone].icon;
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={clsx("flex gap-3 rounded-[var(--radius-panel-sm)] border px-4 py-3", noticeTone[tone].className, className)}
    >
      <Icon className={clsx("mt-0.5 shrink-0", noticeTone[tone].iconClass)} size={16} />
      <div className="min-w-0 flex-1">
        {title ? <strong className="block text-[13px] font-semibold text-[var(--text-primary)]">{title}</strong> : null}
        {children ? (
          <div className={clsx("text-[13px] leading-5 text-[var(--text-secondary)]", title && "mt-0.5")}>{children}</div>
        ) : null}
      </div>
      {action ? <div className="shrink-0 self-center">{action}</div> : null}
    </div>
  );
}

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return <Loader2 className={clsx("animate-spin text-[var(--primary)]", className)} size={size} aria-hidden="true" />;
}

export function LoadingState({ label = "Loading", className }: { label?: string; className?: string }) {
  return (
    <div
      className={clsx("flex min-h-40 items-center justify-center gap-2 text-sm font-medium text-[var(--text-secondary)]", className)}
      role="status"
    >
      <Spinner size={18} />
      {label}
    </div>
  );
}

export function ProgressBar({
  value,
  max = 100,
  tone = "primary",
  size = "md",
  className,
  label,
}: {
  value: number;
  max?: number;
  tone?: "primary" | "success" | "warning" | "danger";
  size?: "sm" | "md";
  className?: string;
  label?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  const color = {
    primary: "bg-[var(--primary)]",
    success: "bg-[var(--success)]",
    warning: "bg-[var(--warning)]",
    danger: "bg-[var(--danger)]",
  }[tone];
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={clsx("w-full overflow-hidden rounded-full bg-[var(--surface-sunken)]", size === "sm" ? "h-1.5" : "h-2", className)}
    >
      <div className={clsx("h-full rounded-full transition-[width] duration-500", color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Centered placeholder for empty lists, unbuilt screens and zero-data states. */
export function EmptyState({
  icon,
  title,
  description,
  action,
  secondaryAction,
  size = "md",
  className,
  children,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  secondaryAction?: ReactNode;
  size?: "sm" | "md" | "lg";
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={clsx(
        "flex flex-col items-center justify-center text-center",
        size === "sm" ? "gap-2 px-4 py-8" : size === "lg" ? "gap-3 px-8 py-20" : "gap-3 px-6 py-14",
        className,
      )}
    >
      {icon ? (
        <div
          className={clsx(
            "relative mb-1 flex items-center justify-center rounded-2xl border border-[var(--primary-soft-strong)] bg-gradient-to-b from-[var(--primary-soft)] to-white text-[var(--primary)] shadow-[var(--shadow-xs)]",
            size === "sm" ? "h-10 w-10" : size === "lg" ? "h-16 w-16" : "h-12 w-12",
          )}
        >
          {icon}
        </div>
      ) : null}
      <h3 className={clsx("font-semibold tracking-[-0.01em] text-[var(--text-primary)]", size === "lg" ? "text-lg" : "text-[15px]")}>
        {title}
      </h3>
      {description ? <p className="max-w-md text-[13.5px] leading-6 text-[var(--text-secondary)]">{description}</p> : null}
      {children}
      {action || secondaryAction ? (
        <div className="mt-2 flex items-center gap-2">
          {action}
          {secondaryAction}
        </div>
      ) : null}
    </div>
  );
}

/** Shimmering placeholder block. Pass sizing via className (e.g. "h-4 w-32"). */
export function Skeleton({ className, rounded = "md" }: { className?: string; rounded?: "sm" | "md" | "lg" | "full" }) {
  return (
    <div
      aria-hidden="true"
      className={clsx(
        "vd-skeleton",
        { sm: "rounded", md: "rounded-md", lg: "rounded-[var(--radius-panel)]", full: "rounded-full" }[rounded],
        className,
      )}
    />
  );
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={clsx("grid gap-2", className)}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={clsx("h-3", i === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}
