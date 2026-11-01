import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CalendarPost } from "../../lib/calendar-api";
import type { T } from "../../i18n";
import { PostChip, addDays, dayKey, isPostDrag, postDate, readPostDrag, sameDay, weekdayName } from "./shared";

const HOURS = Array.from({ length: 24 }, (_, h) => h);

/**
 * Week time-grid: one row per hour, one column per day (Mon–Sun). Posts are points in time,
 * so they stack as chips inside their hour cell. Click an empty slot to create; drop a chip
 * on a slot to move it to that day + hour (minutes kept).
 */
export function WeekView({
  weekStart,
  posts,
  t,
  lang,
  onOpen,
  onCreateAt,
  onDropAt,
}: {
  weekStart: Date;
  posts: CalendarPost[];
  t: T;
  lang: string;
  onOpen: (post: CalendarPost) => void;
  onCreateAt: (when: Date) => void;
  onDropAt: (postId: string, day: Date, hour: number) => void;
}) {
  const [over, setOver] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart]);
  const bySlot = useMemo(() => {
    const map = new Map<string, CalendarPost[]>();
    for (const post of posts) {
      const d = postDate(post);
      if (!d) continue;
      const key = `${dayKey(d)}@${d.getHours()}`;
      map.set(key, [...(map.get(key) ?? []), post]);
    }
    for (const list of map.values()) list.sort((a, b) => (a.scheduled_at ?? "").localeCompare(b.scheduled_at ?? ""));
    return map;
  }, [posts]);
  const countByDay = useMemo(() => {
    const map = new Map<string, number>();
    for (const post of posts) {
      const d = postDate(post);
      if (d) map.set(dayKey(d), (map.get(dayKey(d)) ?? 0) + 1);
    }
    return map;
  }, [posts]);

  // Open scrolled to the working day (08:00, or earlier when the week has earlier posts).
  const scrollRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const weekKey = dayKey(weekStart);
  useEffect(() => {
    const box = scrollRef.current;
    if (!box) return;
    const hours = posts.map((p) => postDate(p)?.getHours()).filter((h): h is number => h !== undefined);
    const target = Math.max(0, Math.min(8, hours.length ? Math.min(...hours) - 1 : 8));
    const row = box.querySelector<HTMLElement>(`[data-hour="${target}"]`);
    if (row) box.scrollTop = row.offsetTop - (headerRef.current?.offsetHeight ?? 0) - 10;
    // Only when the week changes, not on every refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekKey, posts.length > 0]);

  return (
    <div className="overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-panel)]">
      <div ref={scrollRef} className="relative max-h-[max(440px,calc(100vh-300px))] overflow-y-auto">
        <div ref={headerRef} className="sticky top-0 z-10 grid grid-cols-[52px_repeat(7,minmax(0,1fr))] border-b border-[var(--border)] bg-[var(--surface-muted)]">
          <div />
          {days.map((day) => {
            const isToday = sameDay(day, now);
            const count = countByDay.get(dayKey(day)) ?? 0;
            return (
              <div key={dayKey(day)} className="flex items-center gap-2 border-l border-[var(--border-subtle)] px-2.5 py-2">
                <span className="text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[var(--text-tertiary)]">
                  {weekdayName(t, day)}
                </span>
                <span
                  className={clsx(
                    "flex h-6 min-w-6 items-center justify-center rounded-full px-1 text-[13px] font-semibold tabular-nums",
                    isToday ? "bg-[var(--primary)] text-white" : "text-[var(--text-primary)]",
                  )}
                >
                  {day.getDate()}
                </span>
                {count ? <span className="ml-auto text-[11px] tabular-nums text-[var(--text-tertiary)]">{count}</span> : null}
              </div>
            );
          })}
        </div>
        <div className="grid grid-cols-[52px_repeat(7,minmax(0,1fr))]">
          {HOURS.map((hour) => (
            <div key={hour} className="contents">
              <div data-hour={hour} className="border-b border-[var(--border-subtle)] pr-2 text-right">
                <span className="relative -top-2 text-[11px] tabular-nums text-[var(--text-tertiary)]">
                  {hour === 0 ? "" : `${String(hour).padStart(2, "0")}:00`}
                </span>
              </div>
              {days.map((day) => {
                const key = `${dayKey(day)}@${hour}`;
                const list = bySlot.get(key) ?? [];
                const isNowSlot = sameDay(day, now) && now.getHours() === hour;
                const past = new Date(day.getFullYear(), day.getMonth(), day.getDate(), hour + 1) <= now;
                return (
                  <div
                    key={key}
                    data-slot={key}
                    onClick={() => onCreateAt(new Date(day.getFullYear(), day.getMonth(), day.getDate(), hour))}
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
                      onDropAt(id, day, hour);
                    }}
                    className={clsx(
                      "relative grid min-h-[40px] min-w-0 cursor-pointer content-start gap-1 border-b border-l border-[var(--border-subtle)] p-1 transition-colors hover:bg-[var(--surface-hover)]",
                      past && "bg-[var(--surface-muted)]/60",
                      over === key && "!bg-[var(--primary-soft)] outline outline-2 -outline-offset-2 outline-[var(--primary)]",
                    )}
                  >
                    {isNowSlot ? (
                      <span
                        aria-hidden="true"
                        className="pointer-events-none absolute inset-x-0 z-[1] h-0.5 bg-[var(--danger)]"
                        style={{ top: `${(now.getMinutes() / 60) * 100}%` }}
                      >
                        <span className="absolute -left-1 -top-[3px] h-2 w-2 rounded-full bg-[var(--danger)]" />
                      </span>
                    ) : null}
                    {list.map((post) => (
                      <PostChip key={post.id} post={post} t={t} lang={lang} onOpen={onOpen} />
                    ))}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
