import clsx from "clsx";
import { CalendarDays, ExternalLink, Film, Hash } from "lucide-react";
import { useMemo } from "react";
import type { CalendarPost } from "../../lib/calendar-api";
import type { T } from "../../i18n";
import { openUrl } from "../../lib/open-url";
import { PLATFORM_LABEL, PlatformIcon } from "../channels/platform";
import { Badge, Button, EmptyState } from "../ui";
import { LABEL_COLOR, dayKey, fmtLongDay, fmtTime, platformColor, postDate, sameDay, startOfDay, statusMeta } from "./shared";

/** Timeline grouped by day (list / agenda view). */
export function AgendaView({
  posts,
  t,
  lang,
  onOpen,
  onCreate,
}: {
  posts: CalendarPost[];
  t: T;
  lang: string;
  onOpen: (post: CalendarPost) => void;
  onCreate: () => void;
}) {
  const groups = useMemo(() => {
    const map = new Map<string, { day: Date; posts: CalendarPost[] }>();
    const sorted = [...posts].sort((a, b) => (a.scheduled_at ?? "").localeCompare(b.scheduled_at ?? ""));
    for (const post of sorted) {
      const d = postDate(post);
      if (!d) continue;
      const key = dayKey(d);
      if (!map.has(key)) map.set(key, { day: startOfDay(d), posts: [] });
      map.get(key)!.posts.push(post);
    }
    return [...map.values()];
  }, [posts]);
  const today = new Date();

  if (!groups.length) {
    return (
      <div className="rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-panel)]">
        <EmptyState
          icon={<CalendarDays size={22} />}
          title={t.cal_empty_title}
          description={t.cal_empty_desc}
          action={
            <Button size="sm" variant="primary" onClick={onCreate}>
              {t.cal_new_post}
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <ol className="grid gap-5">
      {groups.map(({ day, posts: list }) => {
        const isToday = sameDay(day, today);
        const past = day < startOfDay(today);
        return (
          <li key={dayKey(day)} className="grid grid-cols-[148px_minmax(0,1fr)] gap-4">
            <div className="pt-2.5">
              <div className={clsx("text-[13px] font-semibold", isToday ? "text-[var(--primary)]" : past ? "text-[var(--text-tertiary)]" : "text-[var(--text-primary)]")}>
                {fmtLongDay(t, day, lang)}
              </div>
              {isToday ? (
                <Badge tone="primary" size="sm" className="mt-1">
                  {t.cal_today}
                </Badge>
              ) : null}
            </div>
            <div className="overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-panel)]">
              {list.map((post, i) => {
                const meta = statusMeta(t, post.status);
                const d = postDate(post);
                return (
                  <div
                    key={post.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => onOpen(post)}
                    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onOpen(post)}
                    className={clsx(
                      "flex min-w-0 cursor-pointer items-center gap-3.5 px-4 py-3 transition-colors hover:bg-[var(--surface-hover)]",
                      i > 0 && "border-t border-[var(--border-subtle)]",
                    )}
                  >
                    <span className="w-12 shrink-0 text-[13px] font-semibold tabular-nums text-[var(--text-secondary)]">{fmtTime(d, lang)}</span>
                    <span className="h-9 w-[3px] shrink-0 rounded-full" style={{ background: platformColor(post) }} aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <div className={clsx("flex min-w-0 items-center gap-2 text-[13.5px] font-medium text-[var(--text-primary)]", post.status === "cancelled" && "line-through opacity-60")}>
                        {post.platform ? <PlatformIcon platform={post.platform} size={14} className="shrink-0" /> : null}
                        <span className="truncate">{post.title}</span>
                      </div>
                      <div className="mt-0.5 flex min-w-0 items-center gap-2.5 text-[12px] text-[var(--text-tertiary)]">
                        <span className="truncate">
                          {post.channel?.name ?? (post.platform ? PLATFORM_LABEL[post.platform] : t.cal_no_platform)}
                        </span>
                        {post.media_job ? (
                          <span className="flex min-w-0 items-center gap-1 truncate">
                            <Film size={12} className="shrink-0" />
                            <span className="truncate">{post.media_job.title}</span>
                          </span>
                        ) : null}
                        {post.hashtags.length ? (
                          <span className="flex shrink-0 items-center gap-0.5">
                            <Hash size={12} />
                            {post.hashtags.length}
                          </span>
                        ) : null}
                      </div>
                    </div>
                    {post.label ? (
                      <Badge size="sm" tone="neutral" className="max-w-[140px]" icon={post.color ? <span className="h-1.5 w-1.5 rounded-full" style={{ background: LABEL_COLOR[post.color] }} /> : undefined}>
                        {post.label}
                      </Badge>
                    ) : null}
                    <Badge tone={meta.tone} size="sm" dot>
                      {meta.label}
                    </Badge>
                    {post.status === "published" && post.published_url ? (
                      <button
                        type="button"
                        aria-label={t.cal_open_link}
                        title={t.cal_open_link}
                        onClick={(e) => {
                          e.stopPropagation();
                          void openUrl(post.published_url);
                        }}
                        className="rounded-md p-1.5 text-[var(--text-tertiary)] hover:bg-[var(--surface-hover)] hover:text-[var(--primary)]"
                      >
                        <ExternalLink size={14} />
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
