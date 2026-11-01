import clsx from "clsx";
import type { ReactNode } from "react";

/**
 * Standard page title block: optional eyebrow, title, description, right-side actions,
 * and an optional bottom slot (e.g. <Tabs/>). Place it first inside <Page>.
 */
export function PageHeader({
  title,
  description,
  eyebrow,
  icon,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("mb-6", className)}>
      <div className="flex min-w-0 items-start justify-between gap-6">
        <div className="flex min-w-0 items-start gap-3.5">
          {icon ? (
            <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--radius-panel-sm)] border border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] text-[var(--primary)]">
              {icon}
            </span>
          ) : null}
          <div className="min-w-0">
            {eyebrow ? (
              <div className="mb-1 text-[11.5px] font-semibold uppercase tracking-[0.08em] text-[var(--primary)]">{eyebrow}</div>
            ) : null}
            <h1 className="truncate text-[22px] font-semibold leading-tight tracking-[-0.02em] text-[var(--text-primary)]">{title}</h1>
            {description ? <p className="mt-1 text-[14px] leading-6 text-[var(--text-secondary)]">{description}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      {children ? <div className="mt-5">{children}</div> : null}
    </div>
  );
}

/**
 * Scrollable page body inside the shell's content area. `width="default"` caps at
 * --page-max (1440px), "narrow" at 880px (forms), "full" uses the whole width
 * (editors, boards). `flush` drops the padding (for full-bleed layouts).
 */
export function Page({
  children,
  width = "default",
  flush = false,
  className,
}: {
  children: ReactNode;
  width?: "narrow" | "default" | "full";
  flush?: boolean;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "mx-auto w-full",
        width === "narrow" ? "max-w-[880px]" : width === "default" ? "max-w-[var(--page-max)]" : "max-w-none",
        !flush && "px-8 pb-12 pt-7",
        className,
      )}
    >
      {children}
    </div>
  );
}
