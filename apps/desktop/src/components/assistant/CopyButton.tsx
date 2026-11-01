import clsx from "clsx";
import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback for webviews without clipboard permission.
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

/** Small "Copy" button that flips to a check mark for 1.5 s. `iconOnly` renders a square icon button. */
export function CopyButton({
  text,
  label,
  copiedLabel,
  size = "sm",
  iconOnly = false,
  className,
}: {
  text: string;
  label: string;
  copiedLabel: string;
  size?: "xs" | "sm";
  iconOnly?: boolean;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const onClick = async () => {
    if (await copyText(text)) {
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    }
  };
  const Icon = copied ? Check : Copy;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={copied ? copiedLabel : label}
      title={copied ? copiedLabel : label}
      className={clsx(
        "inline-flex shrink-0 items-center justify-center gap-1 rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:shadow-[var(--shadow-focus)]",
        copied ? "text-[var(--success)]" : "text-[var(--text-tertiary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]",
        iconOnly ? (size === "xs" ? "h-6 w-6" : "h-7 w-7") : size === "xs" ? "h-6 px-1.5 text-[11.5px]" : "h-7 px-2 text-xs",
        className,
      )}
    >
      <Icon size={size === "xs" ? 12 : 14} />
      {iconOnly ? null : <span>{copied ? copiedLabel : label}</span>}
    </button>
  );
}
