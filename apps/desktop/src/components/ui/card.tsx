import clsx from "clsx";
import type { HTMLAttributes, ReactNode } from "react";

/** Plain white surface with a hairline border. `elevated` adds a soft shadow. */
export function Panel({
  children,
  className,
  elevated = false,
}: {
  children: ReactNode;
  className?: string;
  elevated?: boolean;
}) {
  return (
    <section
      className={clsx(
        "rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)]",
        elevated && "shadow-[var(--shadow-panel)]",
        className,
      )}
    >
      {children}
    </section>
  );
}

type CardPadding = "none" | "sm" | "md" | "lg";
const paddings: Record<CardPadding, string> = { none: "", sm: "p-4", md: "p-5", lg: "p-6" };

/**
 * Card with an optional header (title / description / actions) and footer.
 * Use `padding="none"` when the body is a Table or list that should touch the edges.
 */
export function Card({
  title,
  description,
  icon,
  actions,
  footer,
  padding = "md",
  interactive = false,
  className,
  bodyClassName,
  children,
  ...rest
}: Omit<HTMLAttributes<HTMLElement>, "title"> & {
  title?: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  padding?: CardPadding;
  interactive?: boolean;
  bodyClassName?: string;
  children?: ReactNode;
}) {
  const hasHeader = title || description || actions || icon;
  return (
    <section
      {...rest}
      className={clsx(
        "flex min-w-0 flex-col rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-panel)]",
        interactive &&
          "cursor-pointer transition hover:border-[color-mix(in_srgb,var(--primary)_40%,var(--border))] hover:shadow-[var(--shadow-float)]",
        className,
      )}
    >
      {hasHeader ? (
        <header
          className={clsx(
            "flex min-w-0 items-start justify-between gap-4",
            padding === "none" ? "border-b border-[var(--border-subtle)] px-5 py-4" : clsx(paddings[padding], "pb-0"),
          )}
        >
          <div className="flex min-w-0 items-start gap-3">
            {icon ? (
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-panel-sm)] bg-[var(--primary-soft)] text-[var(--primary)]">
                {icon}
              </span>
            ) : null}
            <div className="min-w-0">
              {title ? <h3 className="truncate text-[15px] font-semibold tracking-[-0.01em] text-[var(--text-primary)]">{title}</h3> : null}
              {description ? <p className="mt-0.5 text-[13px] leading-5 text-[var(--text-secondary)]">{description}</p> : null}
            </div>
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={clsx("min-w-0 flex-1", paddings[padding], hasHeader && padding !== "none" && "pt-4", bodyClassName)}>
        {children}
      </div>
      {footer ? (
        <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)] px-5 py-3 rounded-b-[var(--radius-panel)]">
          {footer}
        </footer>
      ) : null}
    </section>
  );
}
