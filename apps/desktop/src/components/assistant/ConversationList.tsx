import clsx from "clsx";
import { Check, Film, MessageSquarePlus, Pencil, Sparkles, Target, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { parseServerDate } from "../../lib/assistant-api";
import type { AssistantMode, Conversation } from "../../lib/assistant-api";
import { Button, SearchInput, Skeleton } from "../ui";

export const MODE_ICON: Record<AssistantMode, LucideIcon> = {
  chat: Sparkles,
  write: Pencil,
  strategy: Target,
};

type Group = { key: string; label: string; items: Conversation[] };

function groupByDate(t: T, items: Conversation[]): Group[] {
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86_400_000;
  const buckets: Group[] = [
    { key: "today", label: t.as_group_today, items: [] },
    { key: "yesterday", label: t.as_group_yesterday, items: [] },
    { key: "week", label: t.as_group_week, items: [] },
    { key: "month", label: t.as_group_month, items: [] },
    { key: "older", label: t.as_group_older, items: [] },
  ];
  for (const item of items) {
    const ts = parseServerDate(item.updated_at)?.getTime() ?? 0;
    const idx = ts >= startToday ? 0 : ts >= startToday - day ? 1 : ts >= startToday - 7 * day ? 2 : ts >= startToday - 30 * day ? 3 : 4;
    buckets[idx].items.push(item);
  }
  return buckets.filter((b) => b.items.length);
}

function Row({
  conv,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  conv: Conversation;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => Promise<void>;
  onDelete: () => void;
}) {
  const { t } = useT();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(conv.title);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) {
      setDraft(conv.title);
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [editing, conv.title]);
  const Icon = MODE_ICON[conv.mode] ?? Sparkles;

  const commit = async () => {
    const title = draft.trim();
    setEditing(false);
    if (title && title !== conv.title) await onRename(title.slice(0, 200));
  };

  if (editing) {
    return (
      <div className="flex items-center gap-1 rounded-[var(--radius-control)] border border-[var(--primary)] bg-[var(--surface)] py-0.5 pl-2 pr-1 shadow-[0_0_0_3px_var(--primary-ring)]">
        <input
          ref={inputRef}
          value={draft}
          maxLength={200}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void commit();
            if (e.key === "Escape") setEditing(false);
          }}
          onBlur={() => void commit()}
          aria-label={t.as_rename}
          className="min-w-0 flex-1 bg-transparent py-1 text-[13px] outline-none"
        />
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => void commit()}
          aria-label={t.as_rename}
          className="rounded p-1 text-[var(--primary)] hover:bg-[var(--primary-soft)]"
        >
          <Check size={14} />
        </button>
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => setEditing(false)}
          aria-label={t.cancel}
          className="rounded p-1 text-[var(--text-tertiary)] hover:bg-[var(--surface-hover)]"
        >
          <X size={14} />
        </button>
      </div>
    );
  }

  return (
    <div
      className={clsx(
        "group relative flex items-center rounded-[var(--radius-control)] transition-colors",
        active ? "bg-[var(--primary-soft)]" : "hover:bg-[var(--surface-hover)]",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        onDoubleClick={() => setEditing(true)}
        aria-current={active ? "true" : undefined}
        className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2 text-left"
      >
        <Icon size={14} className={clsx("shrink-0", active ? "text-[var(--primary)]" : "text-[var(--text-tertiary)]")} />
        <span
          className={clsx(
            "min-w-0 flex-1 truncate text-[13px]",
            active ? "font-semibold text-[var(--primary-ink)]" : "text-[var(--text-primary)]",
          )}
        >
          {conv.title || t.as_untitled}
        </span>
        {conv.job_id ? <Film size={12} className="shrink-0 text-[var(--text-tertiary)] group-hover:hidden" aria-label={t.as_attached_job} /> : null}
      </button>
      <div
        className={clsx(
          "absolute right-1 hidden items-center gap-0.5 rounded-md pl-3 group-hover:flex group-focus-within:flex",
          active
            ? "bg-gradient-to-r from-transparent via-[var(--primary-soft)] to-[var(--primary-soft)]"
            : "bg-gradient-to-r from-transparent via-[var(--surface-hover)] to-[var(--surface-hover)]",
        )}
      >
        <button
          type="button"
          onClick={() => setEditing(true)}
          aria-label={t.as_rename}
          title={t.as_rename}
          className="rounded p-1 text-[var(--text-tertiary)] hover:bg-[var(--surface)] hover:text-[var(--text-primary)]"
        >
          <Pencil size={13} />
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label={t.delete}
          title={t.delete}
          className="rounded p-1 text-[var(--text-tertiary)] hover:bg-[var(--surface)] hover:text-[var(--danger)]"
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}

export function ConversationList({
  items,
  loading,
  activeId,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: {
  items: Conversation[];
  loading: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (conv: Conversation) => void;
}) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? items.filter((c) => (c.title || "").toLowerCase().includes(q)) : items;
  }, [items, query]);
  const groups = useMemo(() => groupByDate(t, filtered), [t, filtered]);

  return (
    <aside className="flex h-full w-[264px] shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface-muted)]">
      <div className="grid gap-2.5 p-3">
        <Button variant="primary" block leadingIcon={<MessageSquarePlus size={16} />} onClick={onNew}>
          {t.as_new_chat}
        </Button>
        <SearchInput
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t.as_search_chats}
          aria-label={t.as_search_chats}
          className="bg-[var(--surface)]"
        />
      </div>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" aria-label={t.as_history}>
        {loading && !items.length ? (
          <div className="grid gap-2 px-1 pt-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-7" rounded="md" />
            ))}
          </div>
        ) : groups.length ? (
          groups.map((group) => (
            <div key={group.key} className="mb-2">
              <div className="px-2.5 pb-1 pt-3 text-[11.5px] font-semibold text-[var(--text-tertiary)]">{group.label}</div>
              <div className="grid grid-cols-1 gap-px">
                {group.items.map((conv) => (
                  <Row
                    key={conv.id}
                    conv={conv}
                    active={conv.id === activeId}
                    onSelect={() => onSelect(conv.id)}
                    onRename={(title) => onRename(conv.id, title)}
                    onDelete={() => onDelete(conv)}
                  />
                ))}
              </div>
            </div>
          ))
        ) : (
          <p className="px-3 py-8 text-center text-[12.5px] leading-5 text-[var(--text-tertiary)]">
            {query ? t.as_search_empty : t.as_history_empty}
          </p>
        )}
      </nav>
    </aside>
  );
}
