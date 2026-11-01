// Shared bits for the content calendar: local-date math (weeks start Monday), status
// metadata, label colors, drag & drop payload and the post chip used by every view.
import clsx from "clsx";
import { CheckCircle2, CircleAlert, Clock3, Lightbulb, PencilLine, XCircle } from "lucide-react";
import type { DragEvent } from "react";
import type { LucideIcon } from "lucide-react";
import type { BadgeTone } from "../ui";
import { PLATFORM_CHART_COLOR, PlatformIcon } from "../channels/platform";
import { parseUtc } from "../../lib/channels-api";
import type { CalendarPost, PostColor, PostStatus } from "../../lib/calendar-api";
import type { T } from "../../i18n";

// --- dates (local time) --------------------------------------------------------------
export const localeOf = (lang: string) => (lang === "en" ? "en-US" : "vi-VN");
export const DEFAULT_HOUR = 19;

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}
export function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, d.getHours(), d.getMinutes());
}
export function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}
/** Monday-based week start. */
export function startOfWeek(d: Date): Date {
  const day = startOfDay(d);
  return addDays(day, -((day.getDay() + 6) % 7));
}
export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
const pad = (n: number) => String(n).padStart(2, "0");
export function dayKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export function toDateInput(d: Date | null): string {
  return d ? dayKey(d) : "";
}
export function toTimeInput(d: Date | null): string {
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : "";
}
/** "YYYY-MM-DD" + "HH:MM" (local) -> Date, or null when no date. */
export function combineLocal(date: string, time: string): Date | null {
  if (!date) return null;
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = (time || `${DEFAULT_HOUR}:00`).split(":").map(Number);
  const out = new Date(y, (m || 1) - 1, d || 1, hh || 0, mm || 0);
  return Number.isNaN(out.getTime()) ? null : out;
}
/** Move to ``day`` keeping the time-of-day of ``from`` (or DEFAULT_HOUR for unscheduled ideas). */
export function onDayKeepTime(day: Date, from: Date | null, hour?: number): Date {
  const h = hour ?? (from ? from.getHours() : DEFAULT_HOUR);
  const m = from ? from.getMinutes() : 0;
  return new Date(day.getFullYear(), day.getMonth(), day.getDate(), h, m);
}
export function postDate(post: CalendarPost): Date | null {
  return parseUtc(post.scheduled_at);
}
export function fmtTime(d: Date | null, lang: string): string {
  if (!d) return "";
  return new Intl.DateTimeFormat(localeOf(lang), { hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
}
/** Monday-first short weekday names from i18n ("T2,T3,…,CN"). */
export function weekdayNames(t: T): string[] {
  return t.cal_weekdays_short.split(",");
}
export function weekdayName(t: T, d: Date): string {
  return weekdayNames(t)[(d.getDay() + 6) % 7];
}
export function fmtDayMonth(d: Date, lang: string): string {
  return lang === "en"
    ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(d)
    : `${d.getDate()}/${d.getMonth() + 1}`;
}
export function fmtMonthTitle(t: T, d: Date, lang: string): string {
  if (lang === "en") return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(d);
  return t.cal_month_title.replace("{m}", String(d.getMonth() + 1)).replace("{y}", String(d.getFullYear()));
}
export function fmtWeekTitle(start: Date, lang: string): string {
  const end = addDays(start, 6);
  if (lang === "en") {
    const f = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
    return `${f.format(start)} – ${f.format(end)}, ${end.getFullYear()}`;
  }
  return `${start.getDate()}/${start.getMonth() + 1} – ${end.getDate()}/${end.getMonth() + 1}/${end.getFullYear()}`;
}
export function fmtLongDay(t: T, d: Date, lang: string): string {
  if (lang === "en") return new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric" }).format(d);
  const names = t.cal_weekdays_long.split(",");
  return `${names[(d.getDay() + 6) % 7]}, ${d.getDate()}/${d.getMonth() + 1}/${d.getFullYear()}`;
}

// --- status / colors ------------------------------------------------------------------
export type StatusMeta = { label: string; tone: BadgeTone; icon: LucideIcon; color: string };

export function statusMeta(t: T, status: PostStatus): StatusMeta {
  switch (status) {
    case "idea":
      return { label: t.cal_status_idea, tone: "warning", icon: Lightbulb, color: "var(--warning)" };
    case "draft":
      return { label: t.cal_status_draft, tone: "neutral", icon: PencilLine, color: "var(--text-tertiary)" };
    case "scheduled":
      return { label: t.cal_status_scheduled, tone: "primary", icon: Clock3, color: "var(--primary)" };
    case "published":
      return { label: t.cal_status_published, tone: "success", icon: CheckCircle2, color: "var(--success)" };
    case "failed":
      return { label: t.cal_status_failed, tone: "danger", icon: CircleAlert, color: "var(--danger)" };
    default:
      return { label: t.cal_status_cancelled, tone: "neutral", icon: XCircle, color: "var(--text-tertiary)" };
  }
}

export const LABEL_COLOR: Record<Exclude<PostColor, "">, string> = {
  blue: "var(--primary)",
  violet: "var(--accent-violet)",
  pink: "var(--accent-pink)",
  cyan: "var(--accent-cyan)",
  green: "var(--success)",
  amber: "var(--warning)",
  slate: "var(--text-tertiary)",
};

export function platformColor(post: Pick<CalendarPost, "platform">): string {
  return post.platform ? PLATFORM_CHART_COLOR[post.platform] : "var(--border-strong)";
}

// --- drag & drop -----------------------------------------------------------------------
const DRAG_TYPE = "application/x-vd-post";

export function startPostDrag(event: DragEvent, post: CalendarPost) {
  event.dataTransfer.setData(DRAG_TYPE, post.id);
  event.dataTransfer.setData("text/plain", post.title);
  event.dataTransfer.effectAllowed = "move";
}
export function isPostDrag(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes(DRAG_TYPE);
}
export function readPostDrag(event: DragEvent): string | null {
  return event.dataTransfer.getData(DRAG_TYPE) || null;
}

/** Published / cancelled posts are history: they don't move. */
export function canDrag(post: CalendarPost): boolean {
  return post.status !== "published" && post.status !== "cancelled";
}

// --- chip --------------------------------------------------------------------------------
export function PostChip({
  post,
  t,
  lang,
  onOpen,
  showTime = true,
  className,
}: {
  post: CalendarPost;
  t: T;
  lang: string;
  onOpen: (post: CalendarPost) => void;
  showTime?: boolean;
  className?: string;
}) {
  const meta = statusMeta(t, post.status);
  const StatusIcon = meta.icon;
  const color = platformColor(post);
  const draggable = canDrag(post);
  const when = postDate(post);
  return (
    // A div (not <button>): the global `button { font: inherit }` rule is unlayered and would
    // override the Tailwind text-size utilities.
    <div
      role="button"
      tabIndex={0}
      draggable={draggable}
      onDragStart={draggable ? (e) => startPostDrag(e, post) : undefined}
      onClick={(e) => {
        e.stopPropagation();
        onOpen(post);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          e.stopPropagation();
          onOpen(post);
        }
      }}
      title={`${post.title} · ${meta.label}${post.channel ? ` · ${post.channel.name}` : ""}`}
      style={{
        borderLeftColor: color,
        borderLeftStyle: "solid",
        background: post.status === "cancelled" ? undefined : `color-mix(in srgb, ${color} 7%, var(--surface))`,
      }}
      className={clsx(
        "group grid w-full min-w-0 select-none gap-px rounded-[6px] border border-l-[3px] border-[var(--border)] px-1.5 py-1 text-left leading-[1.25] transition",
        "hover:border-[var(--primary-soft-strong)] hover:shadow-[var(--shadow-sm)] focus-visible:outline-none focus-visible:shadow-[var(--shadow-focus)]",
        draggable ? "cursor-grab active:cursor-grabbing" : "cursor-pointer",
        (post.status === "idea" || post.status === "draft") && "border-dashed",
        post.status === "cancelled" && "bg-[var(--surface-muted)] opacity-60",
        className,
      )}
    >
      <span className="flex min-w-0 items-center gap-1 text-[10.5px]">
        {post.platform ? <PlatformIcon platform={post.platform} size={11} className="shrink-0" /> : null}
        {showTime && when ? (
          <span className="font-semibold tabular-nums text-[var(--text-secondary)]">{fmtTime(when, lang)}</span>
        ) : null}
        <span className="flex-1" />
        {post.color ? (
          <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: LABEL_COLOR[post.color] }} aria-hidden="true" />
        ) : null}
        <StatusIcon size={12} className="shrink-0" style={{ color: meta.color }} aria-label={meta.label} />
      </span>
      <span
        className={clsx(
          "min-w-0 truncate text-[11.5px] font-medium text-[var(--text-primary)]",
          post.status === "cancelled" && "line-through",
        )}
      >
        {post.title}
      </span>
    </div>
  );
}
