import clsx from "clsx";
import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "subtle" | "outline";
export type ButtonSize = "xs" | "sm" | "md" | "lg";

const variants: Record<ButtonVariant, string> = {
  primary:
    "border-[var(--primary)] bg-[var(--primary)] text-white shadow-[var(--shadow-primary)] hover:border-[var(--primary-hover)] hover:bg-[var(--primary-hover)] active:bg-[var(--primary-active)]",
  secondary:
    "border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] shadow-[var(--shadow-xs)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-hover)]",
  outline:
    "border-[color-mix(in_srgb,var(--primary)_40%,transparent)] bg-[var(--surface)] text-[var(--primary)] hover:border-[var(--primary)] hover:bg-[var(--primary-soft)]",
  subtle:
    "border-transparent bg-[var(--primary-soft)] text-[var(--primary-hover)] hover:bg-[var(--primary-soft-strong)]",
  ghost:
    "border-transparent bg-transparent text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]",
  danger:
    "border-[var(--danger-border)] bg-[var(--danger-soft)] text-[var(--danger)] hover:border-[var(--danger)] hover:bg-[color-mix(in_srgb,var(--danger)_12%,white)]",
};

const sizes: Record<ButtonSize, string> = {
  xs: "min-h-7 gap-1.5 px-2.5 text-xs",
  sm: "min-h-8 gap-1.5 px-3 text-[13px]",
  md: "min-h-9 gap-2 px-4 text-[13.5px]",
  lg: "min-h-11 gap-2 px-5 text-sm",
};

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: ButtonVariant;
    size?: ButtonSize;
    loading?: boolean;
    leadingIcon?: ReactNode;
    trailingIcon?: ReactNode;
    block?: boolean;
  }
>(function Button(
  {
    children,
    className,
    variant = "secondary",
    size = "md",
    loading = false,
    leadingIcon,
    trailingIcon,
    block = false,
    type = "button",
    ...props
  },
  ref,
) {
  return (
    <button
      {...props}
      ref={ref}
      type={type}
      disabled={props.disabled || loading}
      className={clsx(
        "inline-flex select-none items-center justify-center whitespace-nowrap rounded-[var(--radius-button)] border font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:shadow-[var(--shadow-focus)] disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        block && "w-full",
        className,
      )}
    >
      {loading ? (
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" aria-hidden="true" />
      ) : (
        leadingIcon
      )}
      {children}
      {trailingIcon}
    </button>
  );
});

export const IconButton = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    label: string;
    children: ReactNode;
    size?: "sm" | "md";
    variant?: "outline" | "ghost";
    active?: boolean;
  }
>(function IconButton({ label, children, className, size = "md", variant = "outline", active = false, type = "button", ...props }, ref) {
  return (
    <button
      {...props}
      ref={ref}
      type={type}
      aria-label={label}
      title={props.title ?? label}
      className={clsx(
        "inline-flex shrink-0 items-center justify-center rounded-[var(--radius-button)] border transition-colors focus-visible:outline-none focus-visible:shadow-[var(--shadow-focus)] disabled:opacity-45",
        size === "sm" ? "h-8 w-8" : "h-9 w-9",
        variant === "outline"
          ? "border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-xs)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-hover)]"
          : "border-transparent bg-transparent hover:bg-[var(--surface-hover)]",
        active ? "text-[var(--primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
        className,
      )}
    >
      {children}
    </button>
  );
});
