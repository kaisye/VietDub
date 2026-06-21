import clsx from "clsx";
import type { ReactNode } from "react";

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
