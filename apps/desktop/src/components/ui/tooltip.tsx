import clsx from "clsx";
import type { ReactNode } from "react";

/**
 * Lightweight CSS tooltip (hover / focus-within). No portal: avoid inside
 * containers with overflow:hidden near their edge.
 */
export function Tooltip({
  content,
  side = "top",
  children,
  className,
  disabled = false,
}: {
  content: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  children: ReactNode;
  className?: string;
  disabled?: boolean;
}) {
  if (disabled || !content) return <>{children}</>;
  return (
    <span className={clsx("vd-tooltip", className)}>
      {children}
      <span role="tooltip" className="vd-tooltip-bubble" data-side={side}>
        {content}
      </span>
    </span>
  );
}
