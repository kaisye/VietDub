import clsx from "clsx";
import { Check, Film, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useT } from "../../i18n";
import type { Job } from "../../types";
import { Badge, SearchInput, Skeleton } from "../ui";
import type { BadgeTone } from "../ui";

export function jobTitle(job: Pick<Job, "source_title" | "video_url" | "id">): string {
  return job.source_title?.trim() || job.video_url?.replace(/^https?:\/\/(www\.)?/, "") || job.id.slice(0, 8);
}

function statusTone(status: string): BadgeTone {
  if (status === "ready" || status === "completed") return "success";
  if (status === "failed" || status === "error") return "danger";
  if (status === "cancelled") return "neutral";
  return "primary";
}

/**
 * Popover listing dubbing jobs to attach as assistant context. Rendered above its anchor
 * (the composer sits at the bottom of the screen).
 */
export function JobPicker({
  open,
  onClose,
  jobs,
  loading,
  error,
  onReload,
  selectedId,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  jobs: Job[];
  loading: boolean;
  error: string | null;
  onReload: () => void;
  selectedId: string | null;
  onSelect: (job: Job | null) => void;
}) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    setQuery("");
    const onDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node) && !(event.target as HTMLElement)?.closest?.("[data-job-picker-anchor]")) onClose();
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? jobs.filter((j) => jobTitle(j).toLowerCase().includes(q) || j.video_url?.toLowerCase().includes(q)) : jobs;
  }, [jobs, query]);

  if (!open) return null;
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={t.as_attach_title}
      className="absolute bottom-[calc(100%+8px)] left-0 z-30 flex max-h-[min(420px,60vh)] w-[380px] flex-col overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-float)] [animation:vd-pop-in_160ms_cubic-bezier(.2,.8,.2,1)]"
    >
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border-subtle)] px-3.5 py-2.5">
        <div>
          <div className="text-[13px] font-semibold">{t.as_attach_title}</div>
          <div className="text-[11.5px] text-[var(--text-tertiary)]">{t.as_attach_desc}</div>
        </div>
        <button
          type="button"
          onClick={onReload}
          aria-label={t.as_reload}
          title={t.as_reload}
          className="rounded-md p-1.5 text-[var(--text-tertiary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
        >
          <RefreshCw size={14} className={clsx(loading && "animate-spin")} />
        </button>
      </div>
      <div className="p-2.5 pb-1.5">
        <SearchInput autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t.as_attach_search} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {selectedId ? (
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="mb-1 flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[13px] text-[var(--danger)] hover:bg-[var(--danger-soft)]"
          >
            <X size={14} /> {t.as_attach_remove}
          </button>
        ) : null}
        {loading && !jobs.length ? (
          <div className="grid gap-2 p-1.5">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-11" />
            ))}
          </div>
        ) : error ? (
          <p className="px-3 py-6 text-center text-[12.5px] text-[var(--danger)]">{error}</p>
        ) : filtered.length ? (
          filtered.map((job) => {
            const selected = job.id === selectedId;
            return (
              <button
                key={job.id}
                type="button"
                onClick={() => onSelect(job)}
                className={clsx(
                  "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors",
                  selected ? "bg-[var(--primary-soft)]" : "hover:bg-[var(--surface-hover)]",
                )}
              >
                <span className="flex h-9 w-14 shrink-0 items-center justify-center overflow-hidden rounded-md bg-[var(--navy-900)] text-white/70">
                  {job.thumbnail_url ? (
                    <img src={job.thumbnail_url} alt="" className="h-full w-full object-cover" loading="lazy" />
                  ) : (
                    <Film size={15} />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium text-[var(--text-primary)]">{jobTitle(job)}</span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-[var(--text-tertiary)]">
                    <Badge tone={statusTone(job.status)} size="sm">
                      {job.status === "ready" || job.status === "completed"
                        ? t.as_job_ready
                        : job.status === "failed"
                          ? t.as_job_failed
                          : job.status === "cancelled"
                            ? t.as_job_cancelled
                            : t.as_job_running}
                    </Badge>
                    <span className="truncate">
                      {job.source_language} → {job.target_language}
                    </span>
                  </span>
                </span>
                {selected ? <Check size={15} className="shrink-0 text-[var(--primary)]" /> : null}
              </button>
            );
          })
        ) : (
          <p className="px-3 py-6 text-center text-[12.5px] text-[var(--text-tertiary)]">{query ? t.as_search_empty : t.as_attach_empty}</p>
        )}
      </div>
    </div>
  );
}
