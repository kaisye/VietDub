import clsx from "clsx";
import { Check } from "lucide-react";
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export function Field({
  label,
  help,
  error,
  required,
  children,
}: {
  label: string;
  help?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="grid min-w-0 gap-2">
      <span className="text-sm font-bold text-[var(--text-primary)]">
        {label}
        {required ? <span className="ml-1 text-[var(--danger)]">*</span> : null}
      </span>
      {children}
      {error ? (
        <span className="text-xs font-semibold text-[var(--danger)]">{error}</span>
      ) : help ? (
        <span className="text-xs leading-5 text-[var(--text-secondary)]">{help}</span>
      ) : null}
    </label>
  );
}

const controlClass =
  "aether-control min-h-10 w-full px-3 text-sm outline-none transition placeholder:text-[var(--text-tertiary)] disabled:bg-[var(--surface-muted)] disabled:text-[var(--text-tertiary)]";

export function TextField({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(controlClass, className)} />;
}

export function TextArea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx(controlClass, "min-h-24 resize-y py-3 leading-6", className)} />;
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={clsx(controlClass, "pr-9", className)}>
      {children}
    </select>
  );
}

export function SegmentedControl({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <div className="mb-2 text-sm font-bold">{label}</div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(90px,1fr))] gap-1 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-muted)] p-1">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={value === option.value}
            onClick={() => onChange(option.value)}
            className={clsx(
              "min-h-9 rounded-lg px-3 text-sm font-bold transition",
              value === option.value
                ? "bg-[var(--surface)] text-[var(--text-primary)] shadow-sm"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-5 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] p-3 text-left disabled:opacity-45"
    >
      <span>
        <strong className="block text-sm">{label}</strong>
        {description ? <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">{description}</span> : null}
      </span>
      <span
        className={clsx(
          "relative h-6 w-11 shrink-0 rounded-full border transition",
          checked
            ? "border-[var(--primary)] bg-[var(--primary)]"
            : "border-[var(--border-strong)] bg-[var(--surface-muted)]",
        )}
      >
        <span
          className={clsx(
            "absolute top-[3px] flex h-4 w-4 items-center justify-center rounded-full bg-white text-[var(--primary)] shadow-sm transition",
            checked ? "left-[22px]" : "left-[3px]",
          )}
        >
          {checked ? <Check size={10} strokeWidth={3} /> : null}
        </span>
      </span>
    </button>
  );
}

export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  suffix = "",
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="grid gap-2">
      <span className="flex items-center justify-between gap-4 text-sm font-bold">
        {label}
        <output className="font-mono text-xs text-[var(--text-secondary)]">
          {value}
          {suffix}
        </output>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full accent-[var(--primary)]"
      />
    </label>
  );
}
