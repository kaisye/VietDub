import clsx from "clsx";
import { Plus } from "lucide-react";
import { useMemo, useState } from "react";
import type { CalendarPost } from "../../lib/calendar-api";
import type { T } from "../../i18n";
import { tt } from "../../i18n";
import {
  DEFAULT_HOUR,
  PostChip,
  addDays,
  dayKey,
  isPostDrag,
  postDate,
  readPostDrag,
  sameDay,
  startOfMonth,
  startOfWeek,
  weekdayNames,
} from "./shared";

const MAX_CHIPS = 3;

/** Month grid (Mon–Sun, 5–6 weeks). Click an empty day to create, drag chips between days. */
export function MonthView({
  anchor,
  posts,
  t,
  lang,
  onOpen,
  onCreateAt,
  onDropOnDay,
  onShowDay,
}: {
  anchor: Date;
  posts: CalendarPost[];
  t: T;
  lang: string;
  onOpen: (post: CalendarPost) => void;
  onCreateAt: (when: Date) => void;
  /** Drop a post on a day: the screen keeps its time-of-day. */
  onDropOnDay: (postId: string, day: Date) => void;
  /** "+N more" -> open that day's week. */
  onShowDay: (day: Date) => void;
}) {
  const [over, setOver] = useState<string | null>(null);
  const month = anchor.getMonth();
  const today = new Date();

  const days = useMemo(() => {
    const first = startOfWeek(startOfMonth(anchor));
    const lastOfMonth = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
    const end = addDays(startOfWeek(lastOfMonth), 7);
    const out: Date[] = [];
    for (let d = first; d < end; d = addDays(d, 1)) out.push(d);
    return out;
  }, [anchor]);

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarPost[]>();
    for (const post of posts) {
      const d = postDate(post);
      if (!d) continue;
      const key = dayKey(d);
      map.set(key, [...(map.get(key) ?? []), post]);
    }
    for (const list of map.values()) list.sort((a, b) => (a.scheduled_at ?? "").localeCompare(b.scheduled_at ?? ""));
    return map;
  }, [posts]);

  return (
    <div className="overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-panel)]">
      <div className="grid grid-cols-7 border-b border-[var(--border)] bg-[var(--surface-muted)]">
        {weekdayNames(t).map((name, i) => (
          <div
            key={name}
            className={clsx(
              "px-2.5 py-2 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[var(--text-tertiary)]",
              i >= 5 && "text-[var(--text-secondary)]",
            )}
          >
            {name}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {days.map((day, index) => {
          const key = dayKey(day);
          const list = byDay.get(key) ?? [];
          const inMonth = day.getMonth() === month;
          const isToday = sameDay(day, today);
          const hidden = list.length - MAX_CHIPS;
          return (
            <div
              key={key}
              role="gridcell"
              aria-label={key}
              data-day={key}
              onClick={() => onCreateAt(new Date(day.getFullYear(), day.getMonth(), day.getDate(), DEFAULT_HOUR))}
              onDragOver={(e) => {
                if (!isPostDrag(e)) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                if (over !== key) setOver(key);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver((v) => (v === key ? null : v));
              }}
              onDrop={(e) => {
                const id = readPostDrag(e);
                setOver(null);
                if (!id) return;
                e.preventDefault();
                onDropOnDay(id, day);
              }}
              className={clsx(
                "group relative flex min-h-[112px] min-w-0 cursor-pointer flex-col gap-1 border-[var(--border-subtle)] p-1.5 transition-colors",
                index % 7 !== 6 && "border-r",
                index < days.length - 7 && "border-b",
                inMonth ? "bg-[var(--surface)] hover:bg-[var(--surface-hover)]" : "bg-[var(--surface-muted)]",
                over === key && "!bg-[var(--primary-soft)] outline outline-2 -outline-offset-2 outline-[var(--primary)]",
              )}
            >
              <div className="flex items-center justify-between px-0.5">
                <span
                  className={clsx(
                    "flex h-6 min-w-6 items-center justify-center rounded-full px-1 text-[12px] font-semibold tabular-nums",
                    isToday
                      ? "bg-[var(--primary)] text-white"
                      : inMonth
                        ? "text-[var(--text-primary)]"
                        : "text-[var(--text-tertiary)]",
                  )}
                >
                  {day.getDate() === 1 && !isToday ? `${day.getDate()}/${day.getMonth() + 1}` : day.getDate()}
                </span>
                <Plus
                  size={14}
                  aria-hidden="true"
                  className="text-[var(--text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100"
                />
              </div>
              <div className="grid min-w-0 gap-1">
                {list.slice(0, MAX_CHIPS).map((post) => (
                  <PostChip key={post.id} post={post} t={t} lang={lang} onOpen={onOpen} />
                ))}
                {hidden > 0 ? (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onShowDay(day);
                    }}
                    className="w-fit rounded px-1.5 py-0.5 text-left text-[var(--primary)] hover:bg-[var(--primary-soft)]"
                  >
                    <span className="text-[11.5px] font-semibold">{tt(t.cal_more, { n: hidden })}</span>
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
