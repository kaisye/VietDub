import clsx from "clsx";
import { Check } from "lucide-react";

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
                    ? "border-[color-mix(in_srgb,var(--primary)_35%,transparent)] bg-[var(--primary-soft)]"
                    : "border-transparent hover:bg-[var(--surface-muted)]",
                )}
              >
                <span
                  className={clsx(
                    "flex h-8 w-8 items-center justify-center rounded-lg border font-mono text-xs font-bold",
                    complete
                      ? "border-transparent bg-[var(--success-soft)] text-[var(--success)]"
                      : active
                        ? "border-[var(--primary)] bg-[var(--surface)] text-[var(--primary-hover)]"
                        : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)]",
                  )}
                >
                  {complete ? <Check size={15} strokeWidth={3} /> : String(index + 1).padStart(2, "0")}
                </span>
                <span className="hidden min-w-0 lg:block">
                  <strong className="block truncate text-sm">{step.label}</strong>
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
