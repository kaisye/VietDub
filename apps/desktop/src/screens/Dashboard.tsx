import clsx from "clsx";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Clapperboard,
  Eye,
  Film,
  Heart,
  LayoutDashboard,
  Link2,
  Plus,
  RefreshCw,
  Sparkles,
  Tv,
  UserPlus,
  Users,
  Wand2,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { listJobs } from "../api";
import { UpcomingPostsCard } from "../components/calendar/UpcomingPostsCard";
import { ViralTodayCard } from "../components/viral/ViralTodayCard";
import { ChartLegend, LineChart, ShareBars } from "../components/channels/charts";
import type { LineSeries } from "../components/channels/charts";
import { ACTIVE_JOB_STATUSES, JobStatusBadge, JobThumb, jobTitle } from "../components/channels/jobs";
import { ReviewBadge, useReviewSummaries } from "../components/review/ReviewBadge";
import {
  ChannelStatusBadge,
  PLATFORM_CHART_COLOR,
  PLATFORM_LABEL,
  PlatformIcon,
  fmtCompact,
  fmtDuration,
  fmtPercent,
  fmtRelative,
  fmtShortDate,
  fmtSigned,
  growthPct,
  setChannelsIntent,
} from "../components/channels/platform";
import { Page, PageHeader } from "../components/layout/PageHeader";
import type { View } from "../components/layout/nav";
import {
  Avatar,
  Button,
  Card,
  EmptyState,
  InlineNotice,
  ProgressBar,
  Skeleton,
  StatCard,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  Tabs,
} from "../components/ui";
import { tt, useT } from "../i18n";
import type { T } from "../i18n";
import { getDashboard, getSyncStatus, listChannels, PLATFORMS, syncChannels } from "../lib/channels-api";
import type { ActivityItem, Channel, ChannelVideo, Dashboard, Platform } from "../lib/channels-api";
import { openUrl } from "../lib/open-url";
import type { Job } from "../types";

const RANGES = [7, 28, 90] as const;
const RANGE_KEY = "vd.dashboard.range";

type Metric = "followers" | "views";

function readRange(): number {
  try {
    const v = Number(localStorage.getItem(RANGE_KEY));
    return (RANGES as readonly number[]).includes(v) ? v : 28;
  } catch {
    return 28;
  }
}

export default function DashboardScreen({
  onNavigate,
  onOpenJob,
  onOpenEditor,
}: {
  onNavigate?: (view: View) => void;
  onOpenJob?: (jobId: string) => void;
  onOpenEditor?: (jobId: string) => void;
}) {
  const { t, lang } = useT();
  const [days, setDays] = useState<number>(readRange);
  const [platform, setPlatform] = useState<Platform | "">("");
  const [metric, setMetric] = useState<Metric>("followers");
  const [data, setData] = useState<Dashboard | null>(null);
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [dash, list] = await Promise.all([getDashboard(days, platform), listChannels()]);
      if (!mounted.current) return;
      setData(dash);
      setChannels(list);
      setError("");
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [days, platform]);

  useEffect(() => {
    void load();
  }, [load]);

  // Studio projects (independent of the channel API so they show even if it fails).
  useEffect(() => {
    let cancelled = false;
    const fetchJobs = () =>
      listJobs()
        .then((list) => !cancelled && setJobs(list))
        .catch(() => !cancelled && setJobs((prev) => prev ?? []));
    void fetchJobs();
    const id = window.setInterval(fetchJobs, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const pollSync = useCallback(async () => {
    setSyncing(true);
    try {
      for (let i = 0; i < 150 && mounted.current; i += 1) {
        const status = await getSyncStatus();
        if (!status.running) break;
        await new Promise((r) => setTimeout(r, 2000));
      }
    } catch {
      /* sync errors surface in channel status / activity */
    } finally {
      if (mounted.current) {
        setSyncing(false);
        void load();
      }
    }
  }, [load]);

  // On open: refresh OAuth channels that haven't synced for 6 h (cheap no-op otherwise).
  const autoSynced = useRef(false);
  useEffect(() => {
    if (autoSynced.current || !channels) return;
    autoSynced.current = true;
    if (!channels.some((c) => c.source === "oauth" && c.status !== "disconnected")) return;
    syncChannels({ only_stale_hours: 6 })
      .then((res) => {
        if (res.queued_channel_ids.length || res.running) void pollSync();
      })
      .catch(() => undefined);
  }, [channels, pollSync]);

  const syncAll = async () => {
    try {
      await syncChannels({});
      void pollSync();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const changeRange = (value: number) => {
    setDays(value);
    try {
      localStorage.setItem(RANGE_KEY, String(value));
    } catch {
      /* ignore */
    }
  };

  const goChannels = (intent?: Parameters<typeof setChannelsIntent>[0]) => {
    if (intent) setChannelsIntent(intent);
    onNavigate?.("channels");
  };

  const hasChannels = (channels?.length ?? 0) > 0;
  const hasOAuth = channels?.some((c) => c.source === "oauth" && c.status !== "disconnected") ?? false;
  const firstLoad = loading && !data;

  return (
    <Page>
      <PageHeader
        icon={<LayoutDashboard size={20} />}
        title={t.nav_dashboard}
        description={t.dash_sub}
        actions={
          <>
            {hasOAuth ? (
              <Button leadingIcon={<RefreshCw size={15} className={clsx(syncing && "animate-spin")} />} onClick={syncAll} disabled={syncing}>
                {syncing ? t.chn_syncing : t.chn_sync_all}
              </Button>
            ) : null}
            <Button variant="primary" leadingIcon={<Plus size={16} />} onClick={() => onNavigate?.("new")}>
              {t.dsh_new_dub}
            </Button>
          </>
        }
      />

      {error ? (
        <InlineNotice
          tone="danger"
          title={t.dsh_load_error}
          className="mb-5"
          action={
            <Button size="sm" onClick={() => void load()}>
              {t.dsh_retry}
            </Button>
          }
        >
          {error}
        </InlineNotice>
      ) : null}

      {firstLoad ? (
        <DashboardSkeleton />
      ) : !hasChannels ? (
        <div className="grid gap-6">
          <EmptyChannels t={t} onConnect={() => goChannels("connect")} onManual={() => goChannels("manual")} />
          <StudioStrip t={t} lang={lang} jobs={jobs} onOpenJob={onOpenJob} onOpenEditor={onOpenEditor} onNavigate={onNavigate} />
          <div className="grid gap-6 xl:grid-cols-2">
            <UpcomingPostsCard onOpenCalendar={() => onNavigate?.("calendar")} />
            <ViralTodayCard onOpen={() => onNavigate?.("trends")} />
          </div>
        </div>
      ) : data ? (
        <div className={clsx("grid gap-6 transition-opacity", loading && "opacity-60")}>
          {/* Filters: one row above everything they scope */}
          <div className="flex flex-wrap items-center gap-3">
            <Tabs
              variant="pills"
              value={String(days)}
              onChange={(v) => changeRange(Number(v))}
              items={RANGES.map((r) => ({ id: String(r), label: tt(t.dsh_range_days, { n: r }) }))}
            />
            <Tabs
              variant="pills"
              value={platform || "all"}
              onChange={(v) => setPlatform(v === "all" ? "" : (v as Platform))}
              items={[
                { id: "all", label: t.dsh_all_platforms },
                ...PLATFORMS.map((p) => ({
                  id: p,
                  label: PLATFORM_LABEL[p],
                  icon: <PlatformIcon platform={p} size={13} />,
                })),
              ]}
            />
            <span className="ml-auto text-xs text-[var(--text-tertiary)]">
              {fmtShortDate(data.start_date, lang)} – {fmtShortDate(data.end_date, lang)}
            </span>
          </div>

          {data.totals.needs_attention > 0 ? (
            <InlineNotice
              tone="warning"
              title={tt(t.dsh_attention_title, { n: data.totals.needs_attention })}
              action={
                <Button size="sm" onClick={() => goChannels()}>
                  {t.dsh_attention_action}
                </Button>
              }
            >
              {t.dsh_attention_desc}
            </InlineNotice>
          ) : null}

          <KpiRow t={t} lang={lang} data={data} />

          <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <GrowthChart t={t} lang={lang} data={data} platform={platform} metric={metric} setMetric={setMetric} dimmed={loading} />
            <PlatformBreakdown t={t} lang={lang} data={data} metric={metric} />
          </div>

          <StudioStrip t={t} lang={lang} jobs={jobs} onOpenJob={onOpenJob} onOpenEditor={onOpenEditor} onNavigate={onNavigate} />

          <ChannelTable t={t} lang={lang} data={data} onOpen={(id) => goChannels(`detail:${id}`)} onManage={() => goChannels()} />

          <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <TopVideos t={t} lang={lang} data={data} />
            <div className="grid content-start gap-6">
              <UpcomingPostsCard onOpenCalendar={() => onNavigate?.("calendar")} />
              <ViralTodayCard onOpen={() => onNavigate?.("trends")} />
              <RecentActivity t={t} lang={lang} items={data.recent_activity} />
            </div>
          </div>
        </div>
      ) : null}
    </Page>
  );
}

// --- sections -------------------------------------------------------------------

function DashboardSkeleton() {
  return (
    <div className="grid gap-6">
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <StatCard key={i} label={<Skeleton className="h-3 w-24" />} value="" loading />
        ))}
      </div>
      <Skeleton className="h-72 w-full" rounded="lg" />
      <Skeleton className="h-48 w-full" rounded="lg" />
    </div>
  );
}

function EmptyChannels({ t, onConnect, onManual }: { t: T; onConnect: () => void; onManual: () => void }) {
  return (
    <Card padding="none" className="overflow-hidden">
      <div className="grid items-center gap-8 bg-[linear-gradient(135deg,var(--primary-soft)_0%,var(--surface)_55%)] px-10 py-12 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--primary-soft-strong)] bg-[var(--surface)] px-2.5 py-1 text-xs font-medium text-[var(--primary-hover)]">
            <Sparkles size={13} /> {t.dsh_empty_eyebrow}
          </span>
          <h2 className="mt-4 text-[24px] font-semibold leading-tight tracking-[-0.02em] text-[var(--text-primary)]">{t.dsh_empty_title}</h2>
          <p className="mt-2 max-w-lg text-[14px] leading-6 text-[var(--text-secondary)]">{t.dsh_empty_desc}</p>
          <div className="mt-6 flex flex-wrap gap-2">
            <Button variant="primary" size="lg" leadingIcon={<Link2 size={16} />} onClick={onConnect}>
              {t.chn_connect}
            </Button>
            <Button size="lg" leadingIcon={<UserPlus size={16} />} onClick={onManual}>
              {t.chn_add_manual}
            </Button>
          </div>
        </div>
        <div className="grid gap-3">
          {PLATFORMS.map((p) => (
            <div
              key={p}
              className="flex items-center gap-3 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] px-4 py-3 shadow-[var(--shadow-xs)]"
            >
              <PlatformIcon platform={p} size={26} />
              <div className="min-w-0">
                <div className="text-[13.5px] font-semibold text-[var(--text-primary)]">
                  {p === "facebook" ? t.dsh_platform_fb : PLATFORM_LABEL[p]}
                </div>
                <div className="truncate text-xs text-[var(--text-secondary)]">
                  {p === "youtube" ? t.dsh_platform_yt_desc : p === "facebook" ? t.dsh_platform_fb_desc : t.dsh_platform_tt_desc}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function KpiRow({ t, lang, data }: { t: T; lang: string; data: Dashboard }) {
  const tot = data.totals;
  const baseVideos = tot.video_count - tot.videos_in_period;
  const periodLabel = tt(t.dsh_in_days, { n: data.days });
  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <StatCard
        label={t.dsh_kpi_followers}
        value={fmtCompact(tot.followers, lang)}
        icon={<Users size={16} />}
        accent="primary"
        delta={growthPct(tot.followers, tot.followers_change)}
        deltaLabel={tot.followers_change == null ? t.dsh_no_history : `${fmtSigned(tot.followers_change, lang)} ${periodLabel}`}
      />
      <StatCard
        label={t.dsh_kpi_views}
        value={fmtCompact(tot.views, lang)}
        icon={<Eye size={16} />}
        accent="cyan"
        delta={growthPct(tot.views, tot.views_change)}
        deltaLabel={tot.views_change == null ? t.dsh_no_history : `${fmtSigned(tot.views_change, lang)} ${periodLabel}`}
      />
      <StatCard
        label={t.dsh_kpi_videos}
        value={fmtCompact(tot.video_count, lang)}
        icon={<Film size={16} />}
        accent="violet"
        delta={baseVideos > 0 && tot.videos_in_period > 0 ? (tot.videos_in_period / baseVideos) * 100 : null}
        deltaLabel={tt(t.dsh_new_videos, { n: tot.videos_in_period, d: data.days })}
      />
      <StatCard
        label={t.dsh_kpi_engagement}
        value={fmtPercent(tot.engagement_rate, lang, 2)}
        icon={<Heart size={16} />}
        accent="pink"
        hint={
          tot.engagement_rate == null
            ? t.dsh_engagement_none
            : tt(t.dsh_engagement_hint, { views: fmtCompact(tot.period_video_views, lang) })
        }
      />
    </div>
  );
}

function GrowthChart({
  t,
  lang,
  data,
  platform,
  metric,
  setMetric,
  dimmed,
}: {
  t: T;
  lang: string;
  data: Dashboard;
  platform: Platform | "";
  metric: Metric;
  setMetric: (m: Metric) => void;
  dimmed: boolean;
}) {
  const { labels, series } = useMemo(() => {
    const labels = data.series.map((p) => p.date);
    const present = PLATFORMS.filter((p) => data.series.some((pt) => pt.by_platform[p]));
    let series: LineSeries[];
    if (platform || present.length <= 1) {
      const p = (platform || present[0]) as Platform | undefined;
      series = [
        {
          key: "total",
          label: p ? PLATFORM_LABEL[p] : metric === "followers" ? t.dsh_kpi_followers : t.dsh_kpi_views,
          color: p ? PLATFORM_CHART_COLOR[p] : "var(--primary)",
          values: data.series.map((pt) => (pt.has_data ? pt[metric] : null)),
        },
      ];
    } else {
      series = present.map((p) => ({
        key: p,
        label: PLATFORM_LABEL[p],
        color: PLATFORM_CHART_COLOR[p],
        values: data.series.map((pt) => pt.by_platform[p]?.[metric] ?? null),
      }));
    }
    return { labels, series };
  }, [data, platform, metric, t]);

  return (
    <Card
      title={t.dsh_growth_title}
      description={metric === "followers" ? t.dsh_growth_followers_desc : t.dsh_growth_views_desc}
      actions={
        <Tabs
          variant="pills"
          value={metric}
          onChange={(v) => setMetric(v as Metric)}
          items={[
            { id: "followers", label: t.dsh_metric_followers },
            { id: "views", label: t.dsh_metric_views },
          ]}
        />
      }
    >
      {series.length > 1 ? <ChartLegend className="mb-3" items={series.map((s) => ({ label: s.label, color: s.color }))} /> : null}
      <LineChart
        labels={labels}
        series={series}
        height={250}
        dimmed={dimmed}
        formatValue={(v) => fmtCompact(v, lang)}
        formatLabel={(d) => fmtShortDate(d, lang)}
        emptyLabel={t.dsh_chart_empty}
      />
    </Card>
  );
}

function PlatformBreakdown({ t, lang, data, metric }: { t: T; lang: string; data: Dashboard; metric: Metric }) {
  const rows = data.per_platform.filter((p) => p.channels > 0);
  const total = rows.reduce((sum, r) => sum + (r[metric] || 0), 0);
  return (
    <Card title={t.dsh_breakdown_title} description={metric === "followers" ? t.dsh_breakdown_followers : t.dsh_breakdown_views}>
      {rows.length ? (
        <>
          <ShareBars
            formatValue={(v) => fmtCompact(v, lang)}
            rows={rows.map((r) => ({
              key: r.platform,
              color: PLATFORM_CHART_COLOR[r.platform],
              value: r[metric] || 0,
              label: (
                <>
                  <PlatformIcon platform={r.platform} size={15} />
                  {PLATFORM_LABEL[r.platform]}
                </>
              ),
              sub: `${tt(t.dsh_n_channels, { n: r.channels })} · ${total ? fmtPercent((r[metric] || 0) / total, lang, 0) : "—"}`,
            }))}
          />
          <div className="mt-5 grid grid-cols-3 gap-2 border-t border-[var(--border-subtle)] pt-4">
            {rows.map((r) => (
              <div key={r.platform} className="min-w-0">
                <div className="flex items-center gap-1 text-[11.5px] text-[var(--text-tertiary)]">
                  <PlatformIcon platform={r.platform} size={11} /> {t.dsh_engagement_short}
                </div>
                <div className="text-[14px] font-semibold tabular-nums text-[var(--text-primary)]">
                  {fmtPercent(r.engagement_rate, lang, 1)}
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <EmptyState size="sm" title={t.dsh_breakdown_empty} />
      )}
    </Card>
  );
}

function StudioStrip({
  t,
  lang,
  jobs,
  onOpenJob,
  onOpenEditor,
  onNavigate,
}: {
  t: T;
  lang: string;
  jobs: Job[] | null;
  onOpenJob?: (id: string) => void;
  onOpenEditor?: (id: string) => void;
  onNavigate?: (v: View) => void;
}) {
  const recent = useMemo(
    () =>
      [...(jobs ?? [])]
        .sort((a, b) => (b.updated_at || b.created_at || "").localeCompare(a.updated_at || a.created_at || ""))
        .slice(0, 4),
    [jobs],
  );
  const running = (jobs ?? []).filter((j) => ACTIVE_JOB_STATUSES.has(j.status)).length;
  const readyIds = useMemo(() => recent.filter((job) => job.status === "ready").map((job) => job.id), [recent]);
  const reviewSummaries = useReviewSummaries(readyIds);
  return (
    <Card
      icon={<Clapperboard size={17} />}
      title={t.dsh_studio_title}
      description={running ? tt(t.dsh_studio_running, { n: running }) : t.dsh_studio_desc}
      actions={
        <>
          <Button size="sm" variant="ghost" trailingIcon={<ArrowRight size={14} />} onClick={() => onNavigate?.("history")}>
            {t.dsh_view_all}
          </Button>
          <Button size="sm" variant="outline" leadingIcon={<Wand2 size={14} />} onClick={() => onNavigate?.("new")}>
            {t.dsh_new_dub}
          </Button>
        </>
      }
    >
      {jobs === null ? (
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="aspect-[4/3] w-full" rounded="lg" />
          ))}
        </div>
      ) : recent.length === 0 ? (
        <EmptyState
          size="sm"
          icon={<Clapperboard size={18} />}
          title={t.dsh_studio_empty}
          description={t.dsh_studio_empty_desc}
          action={
            <Button variant="primary" size="sm" leadingIcon={<Plus size={14} />} onClick={() => onNavigate?.("new")}>
              {t.dsh_new_dub}
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          {recent.map((job) => {
            const active = ACTIVE_JOB_STATUSES.has(job.status);
            return (
              <div
                key={job.id}
                className="group flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] transition hover:border-[color-mix(in_srgb,var(--primary)_40%,var(--border))] hover:shadow-[var(--shadow-float)]"
              >
                <button type="button" className="relative block text-left" onClick={() => onOpenJob?.(job.id)}>
                  <JobThumb job={job} />
                  <span className="absolute left-2 top-2">
                    <JobStatusBadge t={t} status={job.status} />
                  </span>
                </button>
                <div className="flex min-w-0 flex-1 flex-col gap-2 p-3">
                  <div className="line-clamp-2 [overflow-wrap:anywhere] text-[13px] font-semibold leading-5 text-[var(--text-primary)]" title={jobTitle(job)}>
                    {jobTitle(job)}
                  </div>
                  {active ? (
                    <div className="grid gap-1">
                      <ProgressBar value={job.progress} size="sm" />
                      <span className="truncate text-[11.5px] text-[var(--text-tertiary)]">
                        {Math.round(job.progress)}% · {job.current_step}
                      </span>
                    </div>
                  ) : (
                    <span className="text-[11.5px] text-[var(--text-tertiary)]">
                      {(job.source_language || "auto").toUpperCase()} → {(job.target_language || "VI").toUpperCase()} ·{" "}
                      {fmtRelative(job.updated_at || job.created_at, lang)}
                    </span>
                  )}
                  <ReviewBadge
                    summary={reviewSummaries[job.id]}
                    onClick={() => (onOpenEditor ? onOpenEditor(job.id) : onOpenJob?.(job.id))}
                  />
                  <div className="mt-auto flex gap-1.5 pt-1">
                    <Button size="xs" onClick={() => onOpenJob?.(job.id)}>
                      {active ? t.dsh_job_progress : t.dsh_job_open}
                    </Button>
                    {job.status === "ready" && onOpenEditor ? (
                      <Button size="xs" variant="subtle" onClick={() => onOpenEditor(job.id)}>
                        {t.dsh_job_editor}
                      </Button>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function ChannelTable({
  t,
  lang,
  data,
  onOpen,
  onManage,
}: {
  t: T;
  lang: string;
  data: Dashboard;
  onOpen: (id: string) => void;
  onManage: () => void;
}) {
  return (
    <Card
      padding="none"
      title={t.dsh_channels_title}
      description={tt(t.dsh_channels_desc, { n: data.totals.channels, c: data.totals.connected })}
      actions={
        <Button size="sm" variant="ghost" trailingIcon={<ArrowRight size={14} />} onClick={onManage}>
          {t.dsh_manage_channels}
        </Button>
      }
    >
      <Table>
        <THead>
          <TR>
            <TH>{t.chn_col_channel}</TH>
            <TH>{t.chn_col_status}</TH>
            <TH align="right">{t.dsh_metric_followers}</TH>
            <TH align="right">{tt(t.dsh_col_change, { n: data.days })}</TH>
            <TH align="right">{t.dsh_metric_views}</TH>
            <TH align="right">{t.dsh_col_new_videos}</TH>
            <TH align="right">{t.dsh_engagement_short}</TH>
          </TR>
        </THead>
        <TBody>
          {data.channels.map((c) => (
            <TR key={c.id} onClick={() => onOpen(c.id)}>
              <TD>
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar src={c.avatar_url} name={c.name} size="sm" badge={<PlatformIcon platform={c.platform} size={12} />} />
                  <div className="min-w-0">
                    <div className="truncate font-semibold">{c.name}</div>
                    <div className="truncate text-xs text-[var(--text-tertiary)]">
                      {c.handle || PLATFORM_LABEL[c.platform]}
                      {c.source === "oauth" && c.last_synced_at ? ` · ${fmtRelative(c.last_synced_at, lang)}` : ""}
                    </div>
                  </div>
                </div>
              </TD>
              <TD>
                <ChannelStatusBadge t={t} status={c.status} />
              </TD>
              <TD align="right" className="font-semibold">
                {fmtCompact(c.followers, lang)}
              </TD>
              <TD align="right">
                <Delta value={c.followers_change} lang={lang} />
              </TD>
              <TD align="right">{fmtCompact(c.views, lang)}</TD>
              <TD align="right" muted>
                {c.videos_in_period}
              </TD>
              <TD align="right" muted>
                {fmtPercent(c.engagement_rate, lang, 1)}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

function Delta({ value, lang }: { value: number | null; lang: string }) {
  if (value == null) return <span className="text-[var(--text-tertiary)]">—</span>;
  return (
    <span
      className={clsx(
        "font-medium",
        value > 0 ? "text-[var(--success)]" : value < 0 ? "text-[var(--danger)]" : "text-[var(--text-tertiary)]",
      )}
    >
      {fmtSigned(value, lang)}
    </span>
  );
}

function VideoThumb({ video }: { video: ChannelVideo }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="relative aspect-video w-28 shrink-0 overflow-hidden rounded-md bg-[var(--surface-sunken)]">
      {video.thumbnail_url && !failed ? (
        <img src={video.thumbnail_url} alt="" className="h-full w-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-[var(--text-tertiary)]">
          <Film size={18} />
        </div>
      )}
      {video.duration_seconds ? (
        <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[10px] font-medium text-white tabular-nums">
          {fmtDuration(video.duration_seconds)}
        </span>
      ) : null}
    </div>
  );
}

function TopVideos({ t, lang, data }: { t: T; lang: string; data: Dashboard }) {
  const videos = data.top_videos.slice(0, 6);
  return (
    <Card
      padding="none"
      title={t.dsh_top_videos}
      description={data.top_videos_scope === "all_time" ? t.dsh_top_videos_all_time : tt(t.dsh_top_videos_period, { n: data.days })}
    >
      {videos.length === 0 ? (
        <EmptyState size="sm" icon={<Film size={18} />} title={t.dsh_top_videos_empty} description={t.dsh_top_videos_empty_desc} />
      ) : (
        <ol className="divide-y divide-[var(--border-subtle)]">
          {videos.map((v, i) => (
            <li key={v.id}>
              <button
                type="button"
                disabled={!v.url}
                onClick={() => v.url && void openUrl(v.url)}
                className="flex w-full min-w-0 items-center gap-3 px-5 py-3 text-left transition hover:bg-[var(--surface-hover)] disabled:cursor-default"
              >
                <span className="w-4 shrink-0 text-center text-xs font-semibold text-[var(--text-tertiary)] tabular-nums">{i + 1}</span>
                <VideoThumb video={v} />
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 text-[13px] font-semibold leading-5 text-[var(--text-primary)]">{v.title}</div>
                  <div className="mt-0.5 flex items-center gap-1.5 truncate text-xs text-[var(--text-tertiary)]">
                    <PlatformIcon platform={v.platform} size={11} />
                    <span className="truncate">{v.channel_name}</span>
                    {v.published_at ? <span>· {fmtRelative(v.published_at, lang)}</span> : null}
                    {v.media_job_id ? (
                      <span className="inline-flex items-center gap-0.5 text-[var(--primary)]">
                        · <Clapperboard size={11} /> {t.chn_dubbed}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="text-[13px] font-semibold tabular-nums text-[var(--text-primary)]">{fmtCompact(v.views, lang)}</div>
                  <div className="text-[11px] text-[var(--text-tertiary)]">
                    {fmtCompact(v.likes, lang)} {t.dsh_likes_short}
                  </div>
                </div>
              </button>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function activityView(t: T, item: ActivityItem): { icon: ReactNode; tone: string; text: string; sub?: string } {
  const name = item.channel_name;
  switch (item.type) {
    case "video_published":
      return { icon: <Film size={14} />, tone: "primary", text: tt(t.dsh_act_video, { c: name }), sub: item.title };
    case "channel_connected":
      return { icon: <Link2 size={14} />, tone: "success", text: tt(t.dsh_act_connected, { c: name }) };
    case "channel_added":
      return { icon: <Tv size={14} />, tone: "success", text: tt(t.dsh_act_added, { c: name }) };
    case "channel_needs_reauth":
      return { icon: <AlertTriangle size={14} />, tone: "warning", text: tt(t.dsh_act_reauth, { c: name }) };
    case "sync_error":
    default:
      return { icon: <XCircle size={14} />, tone: "danger", text: tt(t.dsh_act_sync_error, { c: name }), sub: item.title };
  }
}

const activityTone: Record<string, string> = {
  primary: "bg-[var(--primary-soft)] text-[var(--primary)]",
  success: "bg-[var(--success-soft)] text-[var(--success)]",
  warning: "bg-[var(--warning-soft)] text-[var(--warning)]",
  danger: "bg-[var(--danger-soft)] text-[var(--danger)]",
};

function RecentActivity({ t, lang, items }: { t: T; lang: string; items: ActivityItem[] }) {
  return (
    <Card title={t.dsh_activity_title} icon={<Activity size={17} />}>
      {items.length === 0 ? (
        <EmptyState size="sm" title={t.dsh_activity_empty} />
      ) : (
        <ol className="relative grid gap-4">
          {items.slice(0, 8).map((item, i) => {
            const view = activityView(t, item);
            return (
              <li key={`${item.type}-${item.at}-${i}`} className="flex min-w-0 gap-3">
                <span className={clsx("mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full", activityTone[view.tone])}>
                  {view.icon}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-1.5 truncate text-[13px] font-medium text-[var(--text-primary)]">
                      <PlatformIcon platform={item.platform} size={12} />
                      <span className="truncate">{view.text}</span>
                    </span>
                    <span className="shrink-0 text-[11.5px] text-[var(--text-tertiary)]">{fmtRelative(item.at, lang)}</span>
                  </div>
                  {view.sub ? <div className="mt-0.5 truncate text-xs text-[var(--text-secondary)]">{view.sub}</div> : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
