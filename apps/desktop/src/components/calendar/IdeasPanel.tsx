import clsx from "clsx";
import { GripVertical, Lightbulb, Plus } from "lucide-react";
import { useState } from "react";
import type { CalendarPost } from "../../lib/calendar-api";
import type { T } from "../../i18n";
import { PlatformIcon } from "../channels/platform";
import { Skeleton } from "../ui";
import { LABEL_COLOR, canDrag, isPostDrag, platformColor, readPostDrag, startPostDrag } from "./shared";

/**
 * Unscheduled backlog ("Ý tưởng"): posts without a date. Drag an idea onto the calendar to
 * schedule it; drop a calendar post here to move it back to the backlog.
 */
export function IdeasPanel({
  ideas,
  loading,
  t,
  onOpen,
  onQuickAdd,
  onDropToBacklog,
}: {
  ideas: CalendarPost[];
  loading: boolean;
  t: T;
  onOpen: (post: CalendarPost) => void;
  onQuickAdd: (title: string) => Promise<void>;
  onDropToBacklog: (postId: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);

  const submit = async () => {
    const title = draft.trim();
    if (!title || busy) return;
    setBusy(true);
    try {
      await onQuickAdd(title);
      setDraft("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside
      onDragOver={(e) => {
        if (!isPostDrag(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setOver(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(false);
      }}
      onDrop={(e) => {
        const id = readPostDrag(e);
        setOver(false);
        if (!id) return;
        e.preventDefault();
        onDropToBacklog(id);
      }}
      className={clsx(
        "flex min-w-0 flex-col rounded-[var(--radius-panel)] border bg-[var(--surface)] shadow-[var(--shadow-panel)] transition-colors",
        over ? "border-[var(--primary)] bg-[var(--primary-soft)]" : "border-[var(--border)]",
      )}
    >
      <header className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-3">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--warning-soft)] text-[var(--warning)]">
          <Lightbulb size={15} />
        </span>
        <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">{t.cal_ideas_title}</h2>
        <span className="ml-auto rounded-full bg-[var(--surface-sunken)] px-2 py-0.5 text-[11.5px] font-semibold tabular-nums text-[var(--text-secondary)]">
          {ideas.length}
        </span>
      </header>
      <form
        className="flex items-center gap-1.5 border-b border-[var(--border-subtle)] p-2.5"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t.cal_ideas_placeholder}
          aria-label={t.cal_ideas_placeholder}
          maxLength={500}
          className="aether-control min-h-8 w-full min-w-0 px-2.5 text-[13px] outline-none placeholder:text-[var(--text-tertiary)]"
        />
        <button
          type="submit"
          disabled={!draft.trim() || busy}
          aria-label={t.cal_ideas_add}
          title={t.cal_ideas_add}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-control)] bg-[var(--primary)] text-white transition hover:bg-[var(--primary-hover)] disabled:opacity-40"
        >
          <Plus size={16} />
        </button>
      </form>
      <div className="grid content-start gap-1.5 p-2.5">
        {loading && !ideas.length ? (
          [0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)
        ) : ideas.length ? (
          ideas.map((idea) => (
            <button
              key={idea.id}
              type="button"
              draggable={canDrag(idea)}
              onDragStart={(e) => startPostDrag(e, idea)}
              onClick={() => onOpen(idea)}
              style={{ borderLeftColor: platformColor(idea), borderLeftStyle: "solid" }}
              className="group flex min-w-0 cursor-grab items-start gap-2 rounded-[8px] border border-l-[3px] border-dashed border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-left transition hover:border-[var(--primary-soft-strong)] hover:shadow-[var(--shadow-sm)] active:cursor-grabbing"
            >
              <GripVertical size={14} className="mt-0.5 shrink-0 text-[var(--text-tertiary)] opacity-50 group-hover:opacity-100" />
              <span className="min-w-0 flex-1">
                <span className="line-clamp-2 text-[12.5px] font-medium leading-[1.35] text-[var(--text-primary)]">{idea.title}</span>
                {idea.channel || idea.label ? (
                  <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[11.5px] text-[var(--text-tertiary)]">
                    {idea.color ? <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: LABEL_COLOR[idea.color] }} /> : null}
                    <span className="truncate">{[idea.channel?.name, idea.label].filter(Boolean).join(" · ")}</span>
                  </span>
                ) : null}
              </span>
              {idea.platform ? <PlatformIcon platform={idea.platform} size={13} className="mt-0.5 shrink-0" /> : null}
            </button>
          ))
        ) : (
          <p className="px-2 py-6 text-center text-[12.5px] leading-5 text-[var(--text-tertiary)]">{t.cal_ideas_empty}</p>
        )}
      </div>
      <p className="mt-auto border-t border-[var(--border-subtle)] px-4 py-2.5 text-[11.5px] leading-[1.45] text-[var(--text-tertiary)]">
        {t.cal_ideas_hint}
      </p>
    </aside>
  );
}
