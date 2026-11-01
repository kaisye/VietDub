import clsx from "clsx";
import {
  Bookmark,
  Eye,
  Flame,
  Link2,
  Plus,
  Radar,
  RefreshCw,
  Save,
  Search,
  Settings2,
  Sparkles,
  Trash2,
  TrendingUp,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtCompact, fmtRelative } from "../components/channels/platform";
import { Page, PageHeader } from "../components/layout/PageHeader";
import type { View } from "../components/layout/nav";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  EmptyState,
  Field,
  InlineNotice,
  ProgressBar,
  Select,
  Skeleton,
  Tabs,
  TextArea,
  TextField,
} from "../components/ui";
import { KeywordChips } from "../components/viral/KeywordChips";
import { ProviderStatusList, SourcesDrawer } from "../components/viral/SourcesDrawer";
import { VideoCard } from "../components/viral/VideoCard";
import { VideoDrawer } from "../components/viral/VideoDrawer";
import { ViralPlatformIcon, VIRAL_PLATFORM_LABEL } from "../components/viral/shared";
import { tt, useT } from "../i18n";
import {
  analyzeUrls,
  createTopic,
  deleteTopic,
  expandKeywords,
  getDouyinHot,
  getScan,
  getViralSettings,
  listTopics,
  listVideos,
  refreshVideos,
  SOURCE_LANGUAGES,
  startScan,
  updateTopic,
} from "../lib/viral-api";
import type {
  HotWord,
  Keywords,
  SourceLanguage,
  ViralDuration,
  ViralPlatform,
  ViralScan,
  ViralSettings,
  ViralSort,
  ViralTopic,
  ViralVideo,
  ViralWindow,
} from "../lib/viral-api";

type Mode = "discover" | "links" | "watch";
type PlatformChoice = "all" | ViralPlatform;

type FormState = {
  topicId: string | null;
  name: string;
  keywords: Keywords;
  platform: PlatformChoice;
  window: ViralWindow;
  region: string;
  duration: ViralDuration;
  minViews: number;
  sourceLanguage: SourceLanguage;
};

const STATE_KEY = "vd.trends.state";
const PAGE = 24;

const EMPTY_FORM: FormState = {
  topicId: null,
  name: "",
  keywords: { youtube: [], douyin: [] },
  platform: "all",
  window: "7d",
  region: "",
  duration: "any",
  minViews: 0,
  sourceLanguage: "auto",
};

function readState(): { mode: Mode; form: FormState; sort: ViralSort } {
  try {
    const raw = localStorage.getItem(STATE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return { mode: parsed.mode ?? "discover", form: { ...EMPTY_FORM, ...parsed.form }, sort: parsed.sort ?? "score" };
    }
  } catch {
    /* ignore */
  }
  return { mode: "discover", form: EMPTY_FORM, sort: "score" };
}

function platformsOf(choice: PlatformChoice): ViralPlatform[] {
  return choice === "all" ? ["youtube", "douyin"] : [choice];
}

function formFromTopic(topic: ViralTopic): FormState {
  const ps = topic.filters.platforms ?? ["youtube", "douyin"];
  return {
    topicId: topic.id,
    name: topic.name,
    keywords: { youtube: topic.keywords.youtube ?? [], douyin: topic.keywords.douyin ?? [] },
    platform: ps.length === 1 ? ps[0] : "all",
    window: topic.filters.window ?? "7d",
    region: topic.filters.region ?? "",
    duration: topic.filters.duration ?? "any",
    minViews: topic.filters.min_views ?? 0,
    sourceLanguage: topic.filters.source_language ?? "auto",
  };
}

export default function TrendsScreen({ onNavigate }: { onNavigate?: (v: View) => void }) {
  const { t, lang } = useT();
  const initial = useMemo(readState, []);
  const [mode, setMode] = useState<Mode>(initial.mode);
  const [form, setForm] = useState<FormState>(initial.form);
  const [sort, setSort] = useState<ViralSort>(initial.sort);
  const [settings, setSettings] = useState<ViralSettings | null>(null);
  const [topics, setTopics] = useState<ViralTopic[]>([]);
  const [videos, setVideos] = useState<ViralVideo[] | null>(null);
  const [total, setTotal] = useState(0);
  const [showDismissed, setShowDismissed] = useState(false);
  const [showMismatched, setShowMismatched] = useState(false);
  const [error, setError] = useState("");
  const [expanding, setExpanding] = useState(false);
  const [expandNote, setExpandNote] = useState("");
  const [scan, setScan] = useState<ViralScan | null>(null);
  const [lastScanId, setLastScanId] = useState<string | null>(null);
  const [hot, setHot] = useState<HotWord[] | null>(null);
  const [hotError, setHotError] = useState("");
  const [openVideo, setOpenVideo] = useState<string | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [savingTopic, setSavingTopic] = useState(false);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify({ mode, form, sort }));
    } catch {
      /* ignore */
    }
  }, [mode, form, sort]);

  const loadSettings = useCallback(() => {
    getViralSettings().then(setSettings).catch(() => undefined);
  }, []);

  const loadTopics = useCallback(() => {
    listTopics()
      .then((list) => {
        setTopics(list);
        setForm((f) => (f.topicId && !list.some((tp) => tp.id === f.topicId) ? { ...f, topicId: null } : f));
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    loadSettings();
    loadTopics();
  }, [loadSettings, loadTopics]);

  // --- results ---------------------------------------------------------------------------------
  const resultQuery = useMemo(() => {
    if (mode === "watch") return { status: "watched", sort };
    const base = {
      sort,
      status: showDismissed ? "all" : undefined,
      platform: form.platform === "all" ? undefined : form.platform,
      source_language: showMismatched ? undefined : form.sourceLanguage,
    };
    if (form.topicId) return { ...base, topic_id: form.topicId };
    if (lastScanId) return { ...base, scan_id: lastScanId };
    return base;
  }, [mode, sort, showDismissed, showMismatched, form.platform, form.sourceLanguage, form.topicId, lastScanId]);

  const loadVideos = useCallback(
    (append = false, offset = 0) => {
      if (!append) setVideos(null);
      listVideos({ ...resultQuery, offset, limit: PAGE })
        .then((res) => {
          setTotal(res.total);
          setVideos((prev) => (append && prev ? [...prev, ...res.items] : res.items));
        })
        .catch((e: Error) => {
          setError(e.message);
          setVideos([]);
        });
    },
    [resultQuery],
  );

  useEffect(() => {
    if (mode !== "links") loadVideos();
  }, [loadVideos, mode]);

  // --- Douyin hot words (public board) ----------------------------------------------------------
  const wantsDouyin = form.platform !== "youtube";
  useEffect(() => {
    if (mode !== "discover" || !wantsDouyin || hot !== null || settings?.douyin_mode === "off") return;
    getDouyinHot(24)
      .then((res) => setHot(res.words))
      .catch((e: Error) => {
        setHot([]);
        setHotError(e.message);
      });
  }, [mode, wantsDouyin, hot, settings?.douyin_mode]);

  // --- scan ------------------------------------------------------------------------------------
  const stopPolling = () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = null;
  };
  useEffect(() => stopPolling, []);

  const poll = useCallback(
    (id: string) => {
      stopPolling();
      pollRef.current = window.setInterval(() => {
        getScan(id)
          .then((s) => {
            setScan(s);
            if (s.state === "done" || s.state === "failed") {
              stopPolling();
              setLastScanId(s.id);
              loadTopics();
              loadSettings();
              loadVideos();
              if (Array.isArray(s.douyin_hot) && s.douyin_hot.length) setHot(s.douyin_hot);
            }
          })
          .catch(() => stopPolling());
      }, 1200);
    },
    [loadTopics, loadSettings, loadVideos],
  );

  const scanning = scan != null && (scan.state === "queued" || scan.state === "running");
  const activePlatforms = platformsOf(form.platform);
  const hasKeywords = activePlatforms.some((p) => (form.keywords[p] ?? []).length > 0) || Boolean(form.name.trim());

  const runScan = async () => {
    setError("");
    try {
      const keywords: Keywords = {};
      for (const p of activePlatforms) keywords[p] = form.keywords[p] ?? [];
      const s = await startScan({
        topic_id: form.topicId ?? undefined,
        query: form.name.trim() || undefined,
        keywords,
        platforms: activePlatforms,
        window: form.window,
        region: form.region || undefined,
        duration: form.duration,
        min_views: form.minViews || undefined,
        source_language: form.sourceLanguage,
      });
      setScan(s);
      poll(s.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const expand = async () => {
    if (!form.name.trim()) return;
    setExpanding(true);
    setExpandNote("");
    try {
      const res = await expandKeywords(form.name.trim());
      setForm((f) => ({ ...f, keywords: { youtube: res.youtube, douyin: res.douyin } }));
      if (res.source === "fallback") setExpandNote(res.message ? `${t.vir_expand_fallback} (${res.message})` : t.vir_expand_fallback);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setExpanding(false);
    }
  };

  const filters = () => ({
    platforms: activePlatforms,
    window: form.window,
    region: form.region || undefined,
    duration: form.duration,
    min_views: form.minViews || undefined,
    source_language: form.sourceLanguage,
  });

  const saveTopic = async () => {
    if (!form.name.trim()) return;
    setSavingTopic(true);
    try {
      const keywords = { youtube: form.keywords.youtube ?? [], douyin: form.keywords.douyin ?? [] };
      const topic = form.topicId
        ? await updateTopic(form.topicId, { name: form.name.trim(), keywords, filters: filters() })
        : await createTopic({ name: form.name.trim(), keywords, filters: filters() });
      setForm((f) => ({ ...f, topicId: topic.id }));
      loadTopics();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSavingTopic(false);
    }
  };

  const removeTopic = async (id: string) => {
    try {
      await deleteTopic(id);
      setForm(EMPTY_FORM);
      loadTopics();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onVideoChanged = useCallback((next: ViralVideo) => {
    setVideos((list) => list?.map((v) => (v.id === next.id ? { ...v, ...next } : v)) ?? list);
  }, []);

  const youtubeApi = settings?.providers.find((p) => p.id === "youtube_api")?.available;
  const quotaUnits = youtubeApi && activePlatforms.includes("youtube") ? Math.min(3, (form.keywords.youtube ?? []).length || 1) * 102 : null;
  const isEmptyWorkspace = topics.length === 0 && videos !== null && videos.length === 0 && !scan && !form.topicId && !lastScanId;

  return (
    <Page>
      <PageHeader
        icon={<Flame size={20} />}
        title={t.vir_title}
        description={t.vir_sub}
        actions={
          <Button variant="outline" leadingIcon={<Settings2 size={15} />} onClick={() => setSourcesOpen(true)}>
            {t.vir_open_sources}
          </Button>
        }
      >
        <Tabs
          value={mode}
          onChange={(id) => setMode(id as Mode)}
          items={[
            { id: "discover", label: t.vir_mode_discover, icon: <Radar size={15} /> },
            { id: "links", label: t.vir_mode_links, icon: <Link2 size={15} /> },
            { id: "watch", label: t.vir_mode_watch, icon: <Eye size={15} /> },
          ]}
        />
      </PageHeader>

      {error ? (
        <InlineNotice tone="danger" className="mb-4" action={<Button size="xs" variant="ghost" onClick={() => setError("")}>×</Button>}>
          {error}
        </InlineNotice>
      ) : null}

      {mode === "discover" ? (
        <div className="grid gap-5">
          {/* Saved topics */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-1 text-[12.5px] font-semibold text-[var(--text-secondary)]">{t.vir_topics}</span>
            <button
              type="button"
              onClick={() => {
                setForm({ ...EMPTY_FORM, platform: form.platform, window: form.window });
                setLastScanId(null);
              }}
              className={clsx(
                "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[12.5px] font-medium transition",
                !form.topicId
                  ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary-hover)]"
                  : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]",
              )}
            >
              <Plus size={13} /> {t.vir_new_topic}
            </button>
            {topics.map((topic) => (
              <button
                key={topic.id}
                type="button"
                onClick={() => {
                  setForm(formFromTopic(topic));
                  setLastScanId(null);
                }}
                className={clsx(
                  "inline-flex h-8 max-w-[260px] items-center gap-1.5 rounded-full border px-3 text-[12.5px] font-medium transition",
                  form.topicId === topic.id
                    ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary-hover)]"
                    : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] hover:border-[var(--border-strong)]",
                )}
                title={topic.last_scanned_at ? `${t.vir_last_scan}: ${fmtRelative(topic.last_scanned_at, lang)}` : undefined}
              >
                {topic.kind === "creator" ? (
                  <Users size={13} className="shrink-0" />
                ) : (
                  <TrendingUp size={13} className="shrink-0" />
                )}
                <span className="truncate">{topic.name}</span>
                {topic.last_scan_count != null ? (
                  <span className="rounded-full bg-[var(--surface-sunken)] px-1.5 text-[10.5px] text-[var(--text-secondary)]">{topic.last_scan_count}</span>
                ) : null}
              </button>
            ))}
          </div>

          {/* Query builder */}
          <Card padding="lg">
            <div className="grid gap-4">
              <div className="grid items-end gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
                <Field label={t.vir_topic_label}>
                  <TextField
                    value={form.name}
                    onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void expand();
                    }}
                    placeholder={t.vir_topic_ph}
                  />
                </Field>
                <Button leadingIcon={<Sparkles size={15} />} loading={expanding} disabled={!form.name.trim()} onClick={() => void expand()}>
                  {expanding ? t.vir_expanding : t.vir_expand}
                </Button>
              </div>
              {expandNote ? <InlineNotice tone="warning">{expandNote}</InlineNotice> : null}
              <div className={clsx("grid gap-3", activePlatforms.length > 1 && "lg:grid-cols-2")}>
                {activePlatforms.map((p) => (
                  <KeywordChips
                    key={p}
                    platform={p}
                    label={p === "youtube" ? t.vir_kw_youtube : t.vir_kw_douyin}
                    words={form.keywords[p] ?? []}
                    onChange={(words) => setForm((f) => ({ ...f, keywords: { ...f.keywords, [p]: words } }))}
                    placeholder={t.vir_kw_add}
                  />
                ))}
              </div>
              <p className="-mt-1 text-xs text-[var(--text-tertiary)]">{t.vir_kw_hint}</p>

              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                <Field label={t.vir_platform}>
                  <Select value={form.platform} onChange={(e) => setForm((f) => ({ ...f, platform: e.target.value as PlatformChoice }))}>
                    <option value="all">{t.vir_platform_all}</option>
                    <option value="youtube">YouTube</option>
                    <option value="douyin">Douyin</option>
                  </Select>
                </Field>
                <Field label={t.vir_window}>
                  <Select value={form.window} onChange={(e) => setForm((f) => ({ ...f, window: e.target.value as ViralWindow }))}>
                    <option value="24h">{t.vir_w_24h}</option>
                    <option value="7d">{t.vir_w_7d}</option>
                    <option value="30d">{t.vir_w_30d}</option>
                    <option value="90d">{t.vir_w_90d}</option>
                  </Select>
                </Field>
                <Field label={t.vir_region}>
                  <Select value={form.region} onChange={(e) => setForm((f) => ({ ...f, region: e.target.value }))}>
                    <option value="">{settings?.default_region ? `${t.vir_region_default} (${settings.default_region})` : t.vir_region_any}</option>
                    {["US", "GB", "IN", "JP", "KR", "TW", "VN", "TH", "ID", "PH"].map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label={t.vir_duration}>
                  <Select value={form.duration} onChange={(e) => setForm((f) => ({ ...f, duration: e.target.value as ViralDuration }))}>
                    <option value="any">{t.vir_d_any}</option>
                    <option value="short">{t.vir_d_short}</option>
                    <option value="long">{t.vir_d_long}</option>
                  </Select>
                </Field>
                <Field label={t.vir_source_lang}>
                  <Select value={form.sourceLanguage} onChange={(e) => setForm((f) => ({ ...f, sourceLanguage: e.target.value as SourceLanguage }))}>
                    {SOURCE_LANGUAGES.map((code) => (
                      <option key={code} value={code}>
                        {langLabel(t, code)}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label={t.vir_min_views}>
                  <Select value={String(form.minViews)} onChange={(e) => setForm((f) => ({ ...f, minViews: Number(e.target.value) }))}>
                    {[0, 1000, 10000, 100000, 1000000].map((n) => (
                      <option key={n} value={n}>
                        {n === 0 ? t.vir_min_views_any : `≥ ${fmtCompact(n, lang)}`}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>

              <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border-subtle)] pt-4">
                <span className="text-xs text-[var(--text-secondary)]">
                  {quotaUnits != null
                    ? tt(t.vir_quota_estimate, { units: quotaUnits, used: settings?.youtube_quota_used_today ?? 0 })
                    : activePlatforms.includes("youtube")
                      ? t.vir_ytdlp_note
                      : ""}
                </span>
                <div className="ml-auto flex flex-wrap gap-2">
                  {form.topicId ? (
                    <Button variant="ghost" leadingIcon={<Trash2 size={15} />} onClick={() => void removeTopic(form.topicId as string)}>
                      {t.vir_delete_topic}
                    </Button>
                  ) : null}
                  <Button variant="outline" leadingIcon={<Save size={15} />} loading={savingTopic} disabled={!form.name.trim()} onClick={() => void saveTopic()}>
                    {form.topicId ? t.vir_update_topic : t.vir_save_topic}
                  </Button>
                  <Button variant="primary" leadingIcon={<Search size={15} />} loading={scanning} disabled={!hasKeywords || scanning} onClick={() => void runScan()}>
                    {scanning ? t.vir_scanning : t.vir_scan}
                  </Button>
                </div>
              </div>
            </div>
          </Card>

          {scan ? <ScanStatus scan={scan} /> : null}

          {wantsDouyin && settings?.douyin_mode !== "off" ? (
            <HotWords
              words={hot}
              error={hotError}
              onUse={(word) =>
                setForm((f) => {
                  const list = f.keywords.douyin ?? [];
                  return list.includes(word) ? f : { ...f, keywords: { ...f.keywords, douyin: [word, ...list].slice(0, 8) } };
                })
              }
            />
          ) : null}

          {isEmptyWorkspace ? (
            <Onboarding settings={settings} onOpenSources={() => setSourcesOpen(true)} />
          ) : (
            <Results
              videos={videos}
              total={total}
              sort={sort}
              onSort={setSort}
              showDismissed={showDismissed}
              onShowDismissed={setShowDismissed}
              showMismatched={showMismatched}
              onShowMismatched={setShowMismatched}
              languageFiltered={form.sourceLanguage !== "any"}
              onOpen={(v) => setOpenVideo(v.id)}
              onMore={() => loadVideos(true, videos?.length ?? 0)}
            />
          )}
        </div>
      ) : null}

      {mode === "links" ? <LinksPanel onOpen={(v) => setOpenVideo(v.id)} /> : null}

      {mode === "watch" ? (
        <WatchPanel
          videos={videos}
          total={total}
          sort={sort}
          onSort={setSort}
          onOpen={(v) => setOpenVideo(v.id)}
          onMore={() => loadVideos(true, videos?.length ?? 0)}
          onRefreshed={() => loadVideos()}
        />
      ) : null}

      <VideoDrawer
        videoId={openVideo}
        onClose={() => setOpenVideo(null)}
        onChanged={onVideoChanged}
        onDub={() => onNavigate?.("new")}
        onOpenCalendar={() => onNavigate?.("calendar")}
        llmConfigured={Boolean(settings?.llm.configured)}
      />
      <SourcesDrawer open={sourcesOpen} onClose={() => setSourcesOpen(false)} settings={settings} onSaved={setSettings} />
    </Page>
  );
}

// --- pieces ---------------------------------------------------------------------------------------

function langLabel(t: ReturnType<typeof useT>["t"], code: SourceLanguage): string {
  return {
    auto: t.vir_lang_auto,
    any: t.vir_lang_any,
    en: t.vir_lang_en,
    zh: t.vir_lang_zh,
    ja: t.vir_lang_ja,
    ko: t.vir_lang_ko,
    es: t.vir_lang_es,
    other: t.vir_lang_other,
  }[code];
}

function ScanStatus({ scan }: { scan: ViralScan }) {
  const { t } = useT();
  const running = scan.state === "queued" || scan.state === "running";
  const phase = {
    queued: t.vir_phase_queued,
    searching: t.vir_phase_searching,
    scoring: t.vir_phase_scoring,
    translating: t.vir_phase_translating,
    done: "",
  }[scan.phase];
  const problems = Object.entries(scan.providers).filter(([, p]) => p && p.error_code);
  return (
    <div className="grid gap-2">
      {running ? (
        <Card padding="md">
          <div className="mb-2 flex items-center justify-between text-[13px]">
            <span className="font-medium">{phase}</span>
            <span className="tabular-nums text-[var(--text-secondary)]">
              {scan.progress.done}/{scan.progress.total}
            </span>
          </div>
          <ProgressBar value={scan.phase === "searching" || scan.phase === "queued" ? (scan.progress.done / scan.progress.total) * 80 : 92} />
        </Card>
      ) : scan.state === "failed" ? (
        <InlineNotice tone="danger" title={t.vir_scan_failed}>
          {scan.error}
        </InlineNotice>
      ) : (
        <InlineNotice tone="success">
          {tt(t.vir_scan_done, { found: scan.found, new: scan.new })}
          {scan.language_mismatched ? ` ${tt(t.vir_scan_lang_hidden, { n: scan.language_mismatched })}` : ""}
        </InlineNotice>
      )}
      {!running
        ? problems.map(([platform, p]) => (
            <InlineNotice key={platform} tone={p!.error_code === "needs_key" ? "info" : "warning"}>
              <span className="inline-flex items-center gap-1.5">
                <ViralPlatformIcon platform={platform as ViralPlatform} size={14} />
                {p!.error_code === "needs_key" ? t.vir_douyin_needs_key : tt(t.vir_provider_error, { platform: VIRAL_PLATFORM_LABEL[platform as ViralPlatform], message: p!.message })}
              </span>
            </InlineNotice>
          ))
        : null}
    </div>
  );
}

function HotWords({ words, error, onUse }: { words: HotWord[] | null; error: string; onUse: (word: string) => void }) {
  const { t, lang } = useT();
  const [expanded, setExpanded] = useState(false);
  return (
    <Card
      title={t.vir_hot_title}
      description={t.vir_hot_desc}
      icon={<ViralPlatformIcon platform="douyin" size={17} />}
      padding="md"
      actions={
        words && words.length > 8 ? (
          <Button size="sm" variant="ghost" onClick={() => setExpanded((v) => !v)}>
            {expanded ? t.vir_hot_less : tt(t.vir_hot_more, { n: Math.min(words.length, 20) })}
          </Button>
        ) : null
      }
    >
      {words === null ? (
        <div className="flex flex-wrap gap-2">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="h-7 w-24" rounded="full" />
          ))}
        </div>
      ) : words.length === 0 ? (
        <p className="text-xs text-[var(--text-secondary)]">{error || t.vir_hot_empty}</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {words.slice(0, expanded ? 20 : 8).map((w, i) => (
            <button
              key={w.word}
              type="button"
              onClick={() => onUse(w.word)}
              title={`${t.vir_hot_use} · ${fmtCompact(w.hot_value, lang)}`}
              className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-[12.5px] transition hover:border-[var(--primary-soft-strong)] hover:bg-[var(--primary-soft)]"
            >
              <span className={clsx("tabular-nums text-[11px] font-semibold", i < 3 ? "text-[var(--accent-pink)]" : "text-[var(--text-tertiary)]")}>{i + 1}</span>
              {w.word}
            </button>
          ))}
        </div>
      )}
    </Card>
  );
}

function SortSelect({ sort, onSort }: { sort: ViralSort; onSort: (s: ViralSort) => void }) {
  const { t } = useT();
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-[var(--text-secondary)]">{t.vir_sort}</span>
      <Select value={sort} onChange={(e) => onSort(e.target.value as ViralSort)} className="!w-auto min-w-[150px]">
        <option value="score">{t.vir_sort_score}</option>
        <option value="velocity">{t.vir_sort_velocity}</option>
        <option value="views">{t.vir_sort_views}</option>
        <option value="newest">{t.vir_sort_newest}</option>
        <option value="recent">{t.vir_sort_recent}</option>
      </Select>
    </div>
  );
}

function VideoGrid({ videos, total, onOpen, onMore, empty }: { videos: ViralVideo[] | null; total: number; onOpen: (v: ViralVideo) => void; onMore: () => void; empty: string }) {
  const { t } = useT();
  if (videos === null) {
    return (
      <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)]">
            <Skeleton className="aspect-video w-full" rounded="sm" />
            <div className="grid gap-2 p-3.5">
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          </div>
        ))}
      </div>
    );
  }
  if (videos.length === 0) return <EmptyState size="sm" icon={<Search size={20} />} title={empty} />;
  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-4">
        {videos.map((v) => (
          <VideoCard key={v.id} video={v} onOpen={onOpen} />
        ))}
      </div>
      {videos.length < total ? (
        <div className="mt-4 flex justify-center">
          <Button variant="outline" onClick={onMore}>
            {t.vir_load_more}
          </Button>
        </div>
      ) : null}
    </>
  );
}

function Results(props: {
  videos: ViralVideo[] | null;
  total: number;
  sort: ViralSort;
  onSort: (s: ViralSort) => void;
  showDismissed: boolean;
  onShowDismissed: (v: boolean) => void;
  showMismatched: boolean;
  onShowMismatched: (v: boolean) => void;
  languageFiltered: boolean;
  onOpen: (v: ViralVideo) => void;
  onMore: () => void;
}) {
  const { t } = useT();
  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 className="text-[15px] font-semibold">{t.vir_results}</h2>
        <Badge size="sm">{tt(t.vir_results_count, { n: props.total })}</Badge>
        <div className="ml-auto flex flex-wrap items-center gap-4">
          {props.languageFiltered ? (
            <Checkbox checked={props.showMismatched} onChange={props.onShowMismatched} label={t.vir_show_mismatched} />
          ) : null}
          <Checkbox checked={props.showDismissed} onChange={props.onShowDismissed} label={t.vir_show_dismissed} />
          <SortSelect sort={props.sort} onSort={props.onSort} />
        </div>
      </div>
      <VideoGrid videos={props.videos} total={props.total} onOpen={props.onOpen} onMore={props.onMore} empty={t.vir_no_results} />
    </section>
  );
}

function Onboarding({ settings, onOpenSources }: { settings: ViralSettings | null; onOpenSources: () => void }) {
  const { t } = useT();
  return (
    <Card padding="none" className="overflow-hidden">
      <div className="grid gap-8 bg-[linear-gradient(135deg,var(--primary-soft)_0%,var(--surface)_60%)] px-8 py-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div>
          <div className="mb-4 grid h-11 w-11 place-items-center rounded-xl bg-[var(--surface)] text-[var(--accent-pink)] shadow-[var(--shadow-sm)]">
            <Flame size={22} />
          </div>
          <h2 className="text-[20px] font-semibold tracking-[-0.01em]">{t.vir_empty_title}</h2>
          <p className="mt-2 max-w-[520px] text-[13.5px] leading-6 text-[var(--text-secondary)]">{t.vir_empty_desc}</p>
          <ol className="mt-5 grid gap-2 text-[13px] text-[var(--text-primary)]">
            {[t.vir_step_1, t.vir_step_2, t.vir_step_3].map((s, i) => (
              <li key={s} className="flex gap-2.5">
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[var(--primary)] text-[11px] font-semibold text-white">{i + 1}</span>
                {s}
              </li>
            ))}
          </ol>
          <Button className="mt-6" variant="outline" leadingIcon={<Settings2 size={15} />} onClick={onOpenSources}>
            {t.vir_open_sources}
          </Button>
        </div>
        <div className="grid content-start gap-3">
          <div className="text-[12.5px] font-semibold text-[var(--text-secondary)]">{t.vir_src_status}</div>
          {settings ? <ProviderStatusList providers={settings.providers} /> : <Skeleton className="h-40" />}
          <p className="text-xs leading-5 text-[var(--text-secondary)]">{t.vir_empty_dy}</p>
        </div>
      </div>
    </Card>
  );
}

function LinksPanel({ onOpen }: { onOpen: (v: ViralVideo) => void }) {
  const { t } = useT();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Awaited<ReturnType<typeof analyzeUrls>> | null>(null);
  const [error, setError] = useState("");
  const urls = text
    .split(/\s+/)
    .map((s) => s.trim())
    .filter((s) => /^https?:\/\//.test(s));
  const run = async () => {
    setBusy(true);
    setError("");
    try {
      setResult(await analyzeUrls(urls.slice(0, 10)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-5">
      <Card title={t.vir_links_title} description={t.vir_links_desc} icon={<Link2 size={17} />} padding="lg">
        <div className="grid gap-3">
          <TextArea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder={t.vir_links_ph} className="font-[var(--font-code)] text-[12.5px]" />
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-[var(--text-tertiary)]">{tt(t.vir_links_count, { n: urls.length })}</span>
            <Button variant="primary" leadingIcon={<Search size={15} />} loading={busy} disabled={!urls.length} onClick={() => void run()}>
              {t.vir_links_analyze}
            </Button>
          </div>
        </div>
      </Card>
      {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      {result ? (
        <>
          {result.creators.length ? <InlineNotice tone="success">{tt(t.vir_links_creators, { n: result.creators.length })}</InlineNotice> : null}
          {result.errors.length ? (
            <InlineNotice tone="warning" title={tt(t.vir_links_errors, { n: result.errors.length })}>
              <ul className="mt-1 grid gap-0.5 text-xs">
                {result.errors.map((e) => (
                  <li key={e.url} className="truncate">
                    <span className="font-medium">{e.url}</span> — {e.message}
                  </li>
                ))}
              </ul>
            </InlineNotice>
          ) : null}
          {result.videos.some((v) => v.platform === "douyin" && v.score == null) ? <InlineNotice tone="info">{t.vir_douyin_link_no_stats}</InlineNotice> : null}
          <VideoGrid videos={result.videos} total={result.videos.length} onOpen={onOpen} onMore={() => undefined} empty={t.vir_no_results} />
        </>
      ) : null}
    </div>
  );
}

function WatchPanel(props: {
  videos: ViralVideo[] | null;
  total: number;
  sort: ViralSort;
  onSort: (s: ViralSort) => void;
  onOpen: (v: ViralVideo) => void;
  onMore: () => void;
  onRefreshed: () => void;
}) {
  const { t } = useT();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const refresh = async () => {
    setBusy(true);
    setNote("");
    try {
      const res = await refreshVideos();
      const errs = Object.entries(res.errors).map(([p, e]) => `${p}: ${e.message}`);
      setNote(errs.join(" · "));
      props.onRefreshed();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-4">
      <Card padding="md">
        <div className="flex flex-wrap items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--primary-soft)] text-[var(--primary)]">
            <Bookmark size={17} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-semibold">{t.vir_watch_title}</div>
            <div className="text-xs text-[var(--text-secondary)]">{t.vir_watch_desc}</div>
          </div>
          <SortSelect sort={props.sort} onSort={props.onSort} />
          <Button leadingIcon={<RefreshCw size={15} />} loading={busy} disabled={!props.total} onClick={() => void refresh()}>
            {t.vir_watch_refresh}
          </Button>
        </div>
      </Card>
      {note ? <InlineNotice tone="warning">{note}</InlineNotice> : null}
      <VideoGrid videos={props.videos} total={props.total} onOpen={props.onOpen} onMore={props.onMore} empty={t.vir_watch_empty} />
    </div>
  );
}
