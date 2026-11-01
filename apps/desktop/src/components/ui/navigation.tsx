import clsx from "clsx";
import { Check } from "lucide-react";
import type { ReactNode } from "react";

export type StepItem = {
  id: string;
  label: string;
  description?: string;
  status?: "complete" | "current" | "upcoming" | "error";
};

export function StepNavigation({
  steps,
  current,
  onChange,
}: {
  steps: StepItem[];
  current: string;
  onChange?: (id: string) => void;
}) {
  return (
    <nav aria-label="Setup steps" className="max-w-full overflow-x-auto">
      <ol className="flex min-w-max gap-2 lg:grid lg:min-w-0 lg:gap-1">
        {steps.map((step, index) => {
          const active = step.id === current;
          const complete = step.status === "complete";
          return (
            <li key={step.id}>
              <button
                type="button"
                onClick={() => onChange?.(step.id)}
                aria-current={active ? "step" : undefined}
                className={clsx(
                  "grid min-h-12 grid-cols-[32px_minmax(0,1fr)] items-center gap-2 rounded-[var(--radius-panel-sm)] border px-2.5 py-2 text-left transition lg:w-full",
                  active
                    ? "border-[var(--primary-soft-strong)] bg-[var(--primary-soft)]"
                    : "border-transparent hover:bg-[var(--surface-hover)]",
                )}
              >
                <span
                  className={clsx(
                    "flex h-7 w-7 items-center justify-center rounded-full border font-mono text-[11px] font-semibold",
                    complete
                      ? "border-transparent bg-[var(--primary)] text-white"
                      : active
                        ? "border-[var(--primary)] bg-[var(--surface)] text-[var(--primary)] shadow-[0_0_0_3px_var(--primary-ring)]"
                        : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)]",
                  )}
                >
                  {complete ? <Check size={15} strokeWidth={3} /> : String(index + 1).padStart(2, "0")}
                </span>
                <span className="hidden min-w-0 lg:block">
                  <strong className={clsx("block truncate text-[13px] font-semibold", active ? "text-[var(--primary-ink)]" : "text-[var(--text-primary)]")}>{step.label}</strong>
                  {step.description ? (
                    <span className="mt-0.5 block truncate text-xs text-[var(--text-secondary)]">{step.description}</span>
                  ) : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export type TabItem = {
  id: string;
  label: ReactNode;
  icon?: ReactNode;
  count?: number;
  disabled?: boolean;
};

/**
 * Horizontal tabs. `variant="underline"` for page-level sections,
 * `variant="pills"` for compact segmented filters inside cards/toolbars.
 */
export function Tabs({
  items,
  value,
  onChange,
  variant = "underline",
  className,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  variant?: "underline" | "pills";
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={clsx(
        "flex min-w-0 items-center overflow-x-auto",
        variant === "underline"
          ? "gap-5 border-b border-[var(--border)]"
          : "w-fit gap-1 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-sunken)] p-1",
        className,
      )}
    >
      {items.map((item) => {
        const active = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={item.disabled}
            onClick={() => onChange(item.id)}
            className={clsx(
              "inline-flex shrink-0 items-center gap-2 whitespace-nowrap text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45",
              variant === "underline"
                ? clsx(
                    "-mb-px border-b-2 px-0.5 pb-2.5 pt-1",
                    active
                      ? "border-[var(--primary)] text-[var(--text-primary)]"
                      : "border-transparent text-[var(--text-secondary)] hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]",
                  )
                : clsx(
                    "min-h-7 rounded-md px-3",
                    active
                      ? "bg-[var(--surface)] text-[var(--primary-hover)] shadow-[var(--shadow-sm)]"
                      : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
                  ),
            )}
          >
            {item.icon}
            {item.label}
            {typeof item.count === "number" ? (
              <span
                className={clsx(
                  "rounded-full px-1.5 py-px text-[10.5px] font-semibold tabular-nums",
                  active ? "bg-[var(--primary-soft)] text-[var(--primary)]" : "bg-[var(--surface-sunken)] text-[var(--text-secondary)]",
                )}
              >
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
