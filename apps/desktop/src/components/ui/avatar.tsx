import clsx from "clsx";
import { useState } from "react";
import type { ReactNode } from "react";

const sizes = { xs: "h-6 w-6 text-[10px]", sm: "h-8 w-8 text-xs", md: "h-10 w-10 text-sm", lg: "h-12 w-12 text-base", xl: "h-16 w-16 text-lg" };
const palette = ["#2563eb", "#0891b2", "#7c3aed", "#db2777", "#0f766e", "#c2410c", "#4338ca"];

function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts.length === 1 ? parts[0].slice(0, 2) : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function colorFor(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

/** Image avatar with deterministic colored initials fallback; optional corner badge (e.g. platform icon). */
export function Avatar({
  src,
  name,
  size = "md",
  shape = "circle",
  badge,
  className,
}: {
  src?: string | null;
  name: string;
  size?: keyof typeof sizes;
  shape?: "circle" | "rounded";
  badge?: ReactNode;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const showImage = src && !failed;
  return (
    <span className={clsx("relative inline-flex shrink-0", className)}>
      <span
        className={clsx(
          "inline-flex items-center justify-center overflow-hidden font-semibold text-white ring-1 ring-black/5",
          sizes[size],
          shape === "circle" ? "rounded-full" : "rounded-[var(--radius-panel-sm)]",
        )}
        style={showImage ? undefined : { background: colorFor(name) }}
        title={name}
      >
        {showImage ? (
          <img src={src} alt="" className="h-full w-full object-cover" onError={() => setFailed(true)} />
        ) : (
          initials(name)
        )}
      </span>
      {badge ? (
        <span className="absolute -bottom-0.5 -right-0.5 flex items-center justify-center rounded-full bg-white p-px shadow-[var(--shadow-xs)]">
          {badge}
        </span>
      ) : null}
    </span>
  );
}
