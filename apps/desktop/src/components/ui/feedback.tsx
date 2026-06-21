import clsx from "clsx";
import { AlertCircle, CheckCircle2, Info, Loader2, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

type NoticeTone = "info" | "success" | "warning" | "danger";

const noticeTone: Record<NoticeTone, { className: string; icon: typeof Info }> = {
  info: { className: "border-[var(--border)] bg-[var(--info-soft)] text-[var(--info)]", icon: Info },
  success: { className: "border-[var(--border)] bg-[var(--success-soft)] text-[var(--success)]", icon: CheckCircle2 },
  warning: { className: "border-[var(--border)] bg-[var(--warning-soft)] text-[var(--warning)]", icon: TriangleAlert },
  danger: { className: "border-[var(--border)] bg-[var(--danger-soft)] text-[var(--danger)]", icon: AlertCircle },
};

export function InlineNotice({
  title,
  children,
  tone = "info",
}: {
  title: string;
  children: ReactNode;
  tone?: NoticeTone;
}) {
  const Icon = noticeTone[tone].icon;
  return (
    <div className={clsx("flex gap-3 rounded-[var(--radius-panel-sm)] border p-4", noticeTone[tone].className)}>
      <Icon className="mt-0.5 shrink-0" size={18} />
      <div className="min-w-0">
        <strong className="block text-sm">{title}</strong>
        <div className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">{children}</div>
      </div>
    </div>
  );
}

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex min-h-40 items-center justify-center gap-2 text-sm font-semibold text-[var(--text-secondary)]" role="status">
      <Loader2 className="animate-spin" size={18} />
      {label}
    </div>
  );
}
