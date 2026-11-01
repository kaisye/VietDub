import clsx from "clsx";
import { Check, Search } from "lucide-react";
import { forwardRef } from "react";
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
    <label className="grid min-w-0 gap-1.5">
      <span className="text-[13px] font-semibold text-[var(--text-primary)]">
        {label}
        {required ? <span className="ml-1 text-[var(--danger)]">*</span> : null}
      </span>
      {children}
      {error ? (
        <span className="text-xs font-medium text-[var(--danger)]">{error}</span>
      ) : help ? (
        <span className="text-xs leading-5 text-[var(--text-secondary)]">{help}</span>
      ) : null}
    </label>
  );
}

const controlClass =
  "aether-control min-h-9 w-full px-3 text-[13.5px] outline-none transition placeholder:text-[var(--text-tertiary)] disabled:bg-[var(--surface-muted)] disabled:text-[var(--text-tertiary)]";

export const TextField = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function TextField(
  { className, ...props },
  ref,
) {
  return <input {...props} ref={ref} className={clsx(controlClass, className)} />;
});

/** Text input with a leading search icon and optional trailing hint (e.g. a shortcut). */
export const SearchInput = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { hint?: ReactNode; wrapperClassName?: string }
>(function SearchInput({ className, wrapperClassName, hint, ...props }, ref) {
  return (
    <div className={clsx("relative flex items-center", wrapperClassName)}>
      <Search size={15} className="pointer-events-none absolute left-3 text-[var(--text-tertiary)]" aria-hidden="true" />
      <input
        type="search"
        {...props}
        ref={ref}
        className={clsx(controlClass, "pl-9", hint ? "pr-14" : "", className)}
      />
      {hint ? (
        <span className="pointer-events-none absolute right-2 rounded border border-[var(--border)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[10.5px] text-[var(--text-tertiary)]">
          {hint}
        </span>
      ) : null}
    </div>
  );
});

export function Checkbox({
  checked,
  onChange,
  label,
  description,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={clsx("flex cursor-pointer items-start gap-2.5", disabled && "cursor-not-allowed opacity-50")}>
      <input
        type="checkbox"
        className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--primary)]"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="min-w-0">
        <span className="block text-[13.5px] font-medium text-[var(--text-primary)]">{label}</span>
        {description ? <span className="mt-0.5 block text-xs leading-5 text-[var(--text-secondary)]">{description}</span> : null}
      </span>
    </label>
  );
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
      <div className="mb-1.5 text-[13px] font-semibold">{label}</div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(90px,1fr))] gap-1 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-sunken)] p-1">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={value === option.value}
            onClick={() => onChange(option.value)}
            className={clsx(
              "min-h-8 rounded-md px-3 text-[13px] font-semibold transition",
              value === option.value
                ? "bg-[var(--surface)] text-[var(--primary-hover)] shadow-[var(--shadow-sm)]"
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
      className="flex w-full items-center justify-between gap-5 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] p-3 text-left transition hover:border-[var(--border-strong)] disabled:opacity-45"
    >
      <span>
        <strong className="block text-[13.5px] font-semibold">{label}</strong>
        {description ? <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">{description}</span> : null}
      </span>
      <span
        className={clsx(
          "relative h-5 w-9 shrink-0 rounded-full border transition",
          checked
            ? "border-[var(--primary)] bg-[var(--primary)]"
            : "border-[var(--border-strong)] bg-[var(--surface-muted)]",
        )}
      >
        <span
          className={clsx(
            "absolute top-[2px] flex h-3.5 w-3.5 items-center justify-center rounded-full bg-white text-[var(--primary)] shadow-sm transition-all",
            checked ? "left-[18px]" : "left-[2px]",
          )}
        >
          {checked ? <Check size={9} strokeWidth={3} /> : null}
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
      <span className="flex items-center justify-between gap-4 text-[13px] font-semibold">
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
