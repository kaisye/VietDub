import clsx from "clsx";
import { CalendarDays, CalendarRange, ChevronLeft, ChevronRight, LayoutGrid, List, Plus, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgendaView } from "../components/calendar/AgendaView";
import { IdeasPanel } from "../components/calendar/IdeasPanel";
import { MonthView } from "../components/calendar/MonthView";
import { PostDrawer } from "../components/calendar/PostDrawer";
import type { DrawerTarget } from "../components/calendar/PostDrawer";
import { WeekView } from "../components/calendar/WeekView";
import {
  DEFAULT_HOUR,
  addDays,
  addMonths,
  fmtMonthTitle,
  fmtWeekTitle,
  onDayKeepTime,
  postDate,
  startOfDay,
  startOfMonth,
  startOfWeek,
  statusMeta,
} from "../components/calendar/shared";
import { PLATFORM_LABEL, PlatformIcon } from "../components/channels/platform";
import { Page, PageHeader } from "../components/layout/PageHeader";
import type { View } from "../components/layout/nav";
import { Badge, Button, IconButton, InlineNotice, Select, Tabs } from "../components/ui";
import { useT } from "../i18n";
import { POST_STATUSES, createPost, getPost, listPosts, takeCalendarIntent, updatePost } from "../lib/calendar-api";
import type { CalendarPost, PostInput, PostStatus } from "../lib/calendar-api";
import { PLATFORMS, listChannels } from "../lib/channels-api";
import type { Channel, Platform } from "../lib/channels-api";

type Mode = "month" | "week" | "list";
const MODE_KEY = "vd.calendar.view";

function readMode(): Mode {
  try {
    const v = localStorage.getItem(MODE_KEY);
    return v === "week" || v === "list" ? v : "month";
  } catch {
    return "month";
  }
}

function rangeFor(mode: Mode, anchor: Date): { start: Date; end: Date } {
  if (mode === "week") {
    const start = startOfWeek(anchor);
    return { start, end: addDays(start, 7) };
  }
  if (mode === "list") {
    const start = startOfMonth(anchor);
    return { start, end: addMonths(start, 1) };
  }
  // Month grid shows leading/trailing days of the neighbouring months too.
  const start = startOfWeek(startOfMonth(anchor));
  const lastOfMonth = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
  return { start, end: addDays(startOfWeek(lastOfMonth), 7) };
}

export default function CalendarScreen({ onNavigate: _onNavigate }: { onNavigate?: (view: View) => void }) {
  const { t, lang } = useT();
  const [mode, setModeState] = useState<Mode>(readMode);
  const [anchor, setAnchor] = useState<Date>(() => startOfDay(new Date()));
  const [platform, setPlatform] = useState<Platform | "">("");
  const [channelId, setChannelId] = useState("");
  const [status, setStatus] = useState<PostStatus | "">("");
  const [posts, setPosts] = useState<CalendarPost[]>([]);
  const [ideas, setIdeas] = useState<CalendarPost[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [target, setTarget] = useState<DrawerTarget | null>(null);
  const requestId = useRef(0);

  const setMode = (next: Mode) => {
    setModeState(next);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      /* ignore */
    }
  };

  const range = useMemo(() => rangeFor(mode, anchor), [mode, anchor]);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const filters = { platform, channel_id: channelId || undefined };
      const [inRange, backlog] = await Promise.all([
        listPosts({ ...filters, status, start: range.start.toISOString(), end: range.end.toISOString() }),
        listPosts({ ...filters, unscheduled: true }),
      ]);
      if (id !== requestId.current) return;
      setPosts(inRange);
      setIdeas(backlog);
      setError("");
    } catch (err) {
      if (id === requestId.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [platform, channelId, status, range]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    listChannels()
      .then(setChannels)
      .catch(() => setChannels([]));
    // Dashboard "Sắp đăng" hand-off: open that post.
    const intent = takeCalendarIntent();
    if (intent) {
      getPost(intent)
        .then((post) => {
          const d = postDate(post);
          if (d) setAnchor(startOfDay(d));
          setTarget({ mode: "edit", post });
        })
        .catch(() => undefined);
    }
  }, []);

  const filteredChannels = useMemo(() => channels.filter((c) => !platform || c.platform === platform), [channels, platform]);
  useEffect(() => {
    if (channelId && !filteredChannels.some((c) => c.id === channelId)) setChannelId("");
  }, [filteredChannels, channelId]);

  // --- navigation --------------------------------------------------------------------------
  const step = (dir: -1 | 1) => setAnchor((a) => (mode === "week" ? addDays(a, 7 * dir) : addMonths(a, dir)));
  const title = mode === "week" ? fmtWeekTitle(startOfWeek(anchor), lang) : fmtMonthTitle(t, anchor, lang);

  // --- actions -----------------------------------------------------------------------------
  const openPost = (post: CalendarPost) => setTarget({ mode: "edit", post });
  const createAt = (when: Date | null) =>
    setTarget({ mode: "create", scheduledAt: when, platform: platform || null, channelId: channelId || null });

  const nextSlot = () => {
    const now = new Date();
    const base = startOfDay(anchor) > now ? anchor : now;
    const at = new Date(base.getFullYear(), base.getMonth(), base.getDate(), DEFAULT_HOUR);
    return at > now ? at : addDays(at, 1);
  };

  /** Optimistic move (drag & drop): ``when === null`` sends the post back to the backlog. */
  const move = async (postId: string, when: Date | null) => {
    const post = posts.find((p) => p.id === postId) ?? ideas.find((p) => p.id === postId);
    if (!post) return;
    const patch: PostInput = when ? { scheduled_at: when.toISOString() } : { scheduled_at: null, status: "idea" };
    if (when && post.scheduled_at && postDate(post)?.getTime() === when.getTime()) return;
    if (!when && !post.scheduled_at) return;
    const optimistic: CalendarPost = {
      ...post,
      scheduled_at: when ? when.toISOString().replace("Z", "").slice(0, 19) : null,
      status: when ? (post.status === "idea" ? "draft" : post.status) : "idea",
    };
    setPosts((list) => (when ? [...list.filter((p) => p.id !== postId), optimistic] : list.filter((p) => p.id !== postId)));
    setIdeas((list) => (when ? list.filter((p) => p.id !== postId) : [optimistic, ...list.filter((p) => p.id !== postId)]));
    try {
      await updatePost(postId, patch);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
    void load();
  };

  const findPost = (id: string) => posts.find((p) => p.id === id) ?? ideas.find((p) => p.id === id) ?? null;

  const quickAddIdea = async (ideaTitle: string) => {
    try {
      await createPost({ title: ideaTitle, status: "idea", platform: platform || null, channel_id: channelId || null });
      void load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const counts = useMemo(() => {
    const map = new Map<PostStatus, number>();
    for (const post of posts) map.set(post.status, (map.get(post.status) ?? 0) + 1);
    return map;
  }, [posts]);

  return (
    <Page>
      <PageHeader
        icon={<CalendarDays size={20} />}
        title={t.nav_calendar}
        description={t.cal_sub}
        actions={
          <Button variant="primary" leadingIcon={<Plus size={16} />} onClick={() => createAt(nextSlot())}>
            {t.cal_new_post}
          </Button>
        }
      />

      {/* Toolbar: navigation + view switch */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => setAnchor(startOfDay(new Date()))}>
          {t.cal_today}
        </Button>
        <div className="flex items-center">
          <IconButton label={t.cal_prev} size="sm" variant="ghost" onClick={() => step(-1)}>
            <ChevronLeft size={17} />
          </IconButton>
          <IconButton label={t.cal_next} size="sm" variant="ghost" onClick={() => step(1)}>
            <ChevronRight size={17} />
          </IconButton>
        </div>
        <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-[var(--text-primary)]" aria-live="polite">
          {title}
        </h2>
        <IconButton label={t.cal_reload} size="sm" variant="ghost" onClick={() => void load()}>
          <RefreshCw size={15} className={clsx(loading && "animate-spin")} />
        </IconButton>
        <div className="ml-auto">
          <Tabs
            variant="pills"
            value={mode}
            onChange={(v) => setMode(v as Mode)}
            items={[
              { id: "month", label: t.cal_view_month, icon: <LayoutGrid size={14} /> },
              { id: "week", label: t.cal_view_week, icon: <CalendarRange size={14} /> },
              { id: "list", label: t.cal_view_list, icon: <List size={14} /> },
            ]}
          />
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <Tabs
          variant="pills"
          value={platform || "all"}
          onChange={(v) => setPlatform(v === "all" ? "" : (v as Platform))}
          items={[
            { id: "all", label: t.cal_all_platforms },
            ...PLATFORMS.map((p) => ({ id: p, label: PLATFORM_LABEL[p], icon: <PlatformIcon platform={p} size={13} /> })),
          ]}
        />
        <Select
          aria-label={t.cal_filter_channel}
          value={channelId}
          onChange={(e) => setChannelId(e.target.value)}
          className="!w-52"
        >
          <option value="">{t.cal_all_channels}</option>
          {filteredChannels.map((c) => (
            <option key={c.id} value={c.id}>
              {platform ? c.name : `${PLATFORM_LABEL[c.platform]} · ${c.name}`}
            </option>
          ))}
        </Select>
        <Select aria-label={t.cal_filter_status} value={status} onChange={(e) => setStatus(e.target.value as PostStatus | "")} className="!w-44">
          <option value="">{t.cal_all_statuses}</option>
          {POST_STATUSES.map((s) => (
            <option key={s} value={s}>
              {statusMeta(t, s).label}
            </option>
          ))}
        </Select>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {(["scheduled", "draft", "published", "failed"] as PostStatus[]).map((s) =>
            counts.get(s) ? (
              <Badge key={s} tone={statusMeta(t, s).tone} dot>
                {statusMeta(t, s).label} · {counts.get(s)}
              </Badge>
            ) : null,
          )}
        </div>
      </div>

      {error ? (
        <InlineNotice
          tone="danger"
          title={t.cal_load_error}
          className="mb-4"
          action={
            <Button size="sm" onClick={() => void load()}>
              {t.cal_retry}
            </Button>
          }
        >
          {error}
        </InlineNotice>
      ) : null}

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_252px]">
        <div className={clsx("min-w-0 transition-opacity", loading && "opacity-70")}>
          {mode === "month" ? (
            <MonthView
              anchor={anchor}
              posts={posts}
              t={t}
              lang={lang}
              onOpen={openPost}
              onCreateAt={createAt}
              onDropOnDay={(id, day) => {
                const post = findPost(id);
                if (post) void move(id, onDayKeepTime(day, postDate(post)));
              }}
              onShowDay={(day) => {
                setAnchor(day);
                setMode("week");
              }}
            />
          ) : mode === "week" ? (
            <WeekView
              weekStart={startOfWeek(anchor)}
              posts={posts}
              t={t}
              lang={lang}
              onOpen={openPost}
              onCreateAt={createAt}
              onDropAt={(id, day, hour) => {
                const post = findPost(id);
                if (post) void move(id, onDayKeepTime(day, postDate(post), hour));
              }}
            />
          ) : (
            <AgendaView posts={posts} t={t} lang={lang} onOpen={openPost} onCreate={() => createAt(nextSlot())} />
          )}
        </div>
        <div className="xl:sticky xl:top-4">
          <IdeasPanel
            ideas={ideas}
            loading={loading}
            t={t}
            onOpen={openPost}
            onQuickAdd={quickAddIdea}
            onDropToBacklog={(id) => void move(id, null)}
          />
        </div>
      </div>

      <PostDrawer
        target={target}
        channels={channels}
        t={t}
        lang={lang}
        onClose={() => setTarget(null)}
        onSaved={() => {
          setTarget(null);
          void load();
        }}
        onDeleted={() => {
          setTarget(null);
          void load();
        }}
        onDuplicated={(copy) => {
          setTarget({ mode: "edit", post: copy });
          void load();
        }}
      />
    </Page>
  );
}
