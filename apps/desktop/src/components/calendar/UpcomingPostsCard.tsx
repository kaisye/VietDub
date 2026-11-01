import { ArrowRight, CalendarClock } from "lucide-react";
import { useEffect, useState } from "react";
import { useT } from "../../i18n";
import { setCalendarIntent, upcomingPosts } from "../../lib/calendar-api";
import type { CalendarPost } from "../../lib/calendar-api";
import { PLATFORM_LABEL, PlatformIcon } from "../channels/platform";
import { Badge, Button, Card, EmptyState, Skeleton } from "../ui";
import { fmtTime, postDate, statusMeta, weekdayName } from "./shared";

/** Dashboard card: the next planned posts, linking into the content calendar. */
export function UpcomingPostsCard({ onOpenCalendar }: { onOpenCalendar: () => void }) {
  const { t, lang } = useT();
  const [posts, setPosts] = useState<CalendarPost[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    upcomingPosts(5)
      .then((list) => !cancelled && setPosts(list))
      .catch(() => {
        if (cancelled) return;
        setFailed(true);
        setPosts([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card
      title={t.cal_upcoming_title}
      icon={<CalendarClock size={17} />}
      actions={
        <Button size="sm" variant="ghost" trailingIcon={<ArrowRight size={14} />} onClick={onOpenCalendar}>
          {t.cal_upcoming_open}
        </Button>
      }
    >
      {posts === null ? (
        <div className="grid gap-2.5">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-11" />
          ))}
        </div>
      ) : posts.length === 0 ? (
        <EmptyState
          size="sm"
          title={failed ? t.cal_upcoming_error : t.cal_upcoming_empty}
          action={
            <Button size="sm" onClick={onOpenCalendar}>
              {t.cal_new_post}
            </Button>
          }
        />
      ) : (
        <ol className="grid gap-1">
          {posts.map((post) => {
            const d = postDate(post);
            const meta = statusMeta(t, post.status);
            return (
              <li key={post.id}>
                <button
                  type="button"
                  onClick={() => {
                    setCalendarIntent(post.id);
                    onOpenCalendar();
                  }}
                  className="-mx-2 flex w-[calc(100%+16px)] min-w-0 items-center gap-3 rounded-[var(--radius-control)] px-2 py-2 text-left transition-colors hover:bg-[var(--surface-hover)]"
                >
                  <span className="flex w-11 shrink-0 flex-col items-center rounded-[8px] border border-[var(--border)] bg-[var(--surface-muted)] py-1 leading-none">
                    <span className="text-[10px] font-semibold uppercase text-[var(--text-tertiary)]">{d ? weekdayName(t, d) : ""}</span>
                    <span className="mt-0.5 text-[15px] font-semibold tabular-nums text-[var(--text-primary)]">{d?.getDate()}</span>
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-1.5 text-[13px] font-medium text-[var(--text-primary)]">
                      {post.platform ? <PlatformIcon platform={post.platform} size={12} className="shrink-0" /> : null}
                      <span className="truncate">{post.title}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-[11.5px] text-[var(--text-tertiary)]">
                      {fmtTime(d, lang)}
                      {d ? ` · ${d.getDate()}/${d.getMonth() + 1}` : ""}
                      {" · "}
                      {post.channel?.name ?? (post.platform ? PLATFORM_LABEL[post.platform] : t.cal_no_platform)}
                    </span>
                  </span>
                  <Badge tone={meta.tone} size="sm" dot>
                    {meta.label}
                  </Badge>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
