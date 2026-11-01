// Client for viral discovery (apps/api/app/viral_api.py, prefix /api/viral).
// Datetimes are naive UTC ISO strings (parse with `parseUtc` from channels-api).
import { API_BASE_URL } from "../api";

export type ViralPlatform = "youtube" | "douyin";
export const VIRAL_PLATFORMS: ViralPlatform[] = ["youtube", "douyin"];
export type ViralWindow = "24h" | "7d" | "30d" | "90d";
export type ViralDuration = "any" | "short" | "long";
export type ViralOrder = "views" | "relevance" | "date";
export type SourceLanguage = "auto" | "any" | "en" | "zh" | "ja" | "ko" | "es" | "other";
export const SOURCE_LANGUAGES: SourceLanguage[] = ["auto", "any", "en", "zh", "ja", "ko", "es", "other"];
export type ViralSort = "score" | "velocity" | "views" | "newest" | "recent";
export type ViralTier = "very_high" | "high" | "medium" | "low" | "";
export type ViralStatus = "new" | "saved" | "dismissed" | "dubbed";
export type ViralBadge = "outlier" | "accelerating" | "dub_fit" | "new";
export type Keywords = Partial<Record<ViralPlatform, string[]>>;

export type ViralFilters = {
  platforms?: ViralPlatform[];
  window?: ViralWindow;
  region?: string;
  duration?: ViralDuration;
  order?: ViralOrder;
  limit?: number;
  min_views?: number;
  source_language?: SourceLanguage;
};

export type ViralTopic = {
  id: string;
  name: string;
  kind: "keyword" | "creator";
  platform: ViralPlatform | null;
  source_url: string;
  keywords: Keywords;
  filters: ViralFilters;
  watched: boolean;
  last_scanned_at: string | null;
  last_scan_count: number | null;
  created_at: string;
  updated_at: string;
};

export type Momentum = {
  delta_views: number;
  gap_hours: number;
  recent_vph: number;
  lifetime_vph: number | null;
  ratio: number | null;
};

export type DubHints = {
  fit: number;
  good: boolean;
  format: "short" | "long" | "very_long" | null;
  speech: boolean | null;
  music_only: boolean;
  vietnamese: boolean;
  language: string | null;
  language_mismatch: boolean;
  reasons: string[];
};

export type ViralMetrics = {
  views: number | null;
  views_estimated: boolean;
  hours_since_publish: number | null;
  views_per_hour: number | null;
  outlier_ratio: number | null;
  engagement_rate: number | null;
  momentum: Momentum | null;
};

export type ComponentKey = "velocity" | "outlier" | "engagement" | "momentum" | "freshness";

export type ViralVideo = {
  id: string;
  platform: ViralPlatform;
  external_id: string;
  url: string;
  title: string;
  title_vi: string;
  creator_name: string;
  creator_id: string;
  creator_url: string;
  followers: number | null;
  published_at: string | null;
  duration: number | null;
  thumbnail_url: string;
  /** Detected source language (ISO 639-1-ish: en, zh, kn, ar…), null when unknown. */
  language: string | null;
  stats: { views: number | null; likes: number | null; comments: number | null; shares: number | null; collects: number | null };
  score: number | null;
  tier: ViralTier;
  views_per_hour: number | null;
  badges: ViralBadge[];
  metrics: Partial<ViralMetrics>;
  dub: Partial<DubHints>;
  confidence: number | null;
  topic_ids: string[];
  status: ViralStatus;
  watched: boolean;
  media_job_id: string | null;
  calendar_post_id: string | null;
  has_analysis: boolean;
  source: string;
  first_seen_at: string;
  last_scanned_at: string;
};

export type ViralAnalysis = {
  why_viral: string[];
  hook: string;
  title_vi: string;
  alt_titles_vi: string[];
  angle: string;
  dub_tips: string[];
  risks: string[];
  provider?: string;
  model?: string;
};

export type ViralVideoDetail = ViralVideo & {
  description: string;
  breakdown: {
    components?: Partial<Record<ComponentKey, number | null>>;
    weights?: Record<ComponentKey, number>;
    window?: string | null;
    scored_at?: string;
  };
  snapshots: { scanned_at: string; views: number | null; likes: number | null; comments: number | null; shares: number | null }[];
  analysis: ViralAnalysis | null;
  analysis_at: string | null;
};

export type ProviderInfo = {
  id: "youtube_api" | "youtube_ytdlp" | "douyin_public" | "douyin_tikhub";
  platform: ViralPlatform;
  label: string;
  available: boolean;
  reason: string;
  reliability: "stable" | "best_effort" | "unverified";
  capabilities: string[];
  limits: Record<string, number | boolean>;
};

export type HotWord = { word: string; hot_value: number | null; label: number | null };

export type ScanProviderResult = {
  provider: string;
  label: string;
  ok: boolean;
  count: number;
  error_code: string | null;
  message: string;
};

export type ViralScan = {
  id: string;
  state: "queued" | "running" | "done" | "failed";
  trigger: string;
  phase: "queued" | "searching" | "scoring" | "translating" | "done";
  progress: { done: number; total: number };
  providers: Partial<Record<ViralPlatform, ScanProviderResult>>;
  found: number;
  new: number;
  language_mismatched: number;
  douyin_hot: HotWord[] | { error_code: string; message: string } | null;
  estimate: { youtube_quota_units: number | null; youtube_quota_used_today: number };
  error: string | null;
  request: { topic_id: string | null; query: string; platforms: ViralPlatform[]; keywords: Keywords; window: ViralWindow };
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  video_count: number;
};

export type ViralSettings = {
  youtube_mode: "auto" | "api" | "ytdlp";
  default_region: string;
  douyin_mode: "public" | "tikhub" | "off";
  tikhub_base_url: string;
  translate_titles: boolean;
  auto_rescan_enabled: boolean;
  auto_rescan_hours: number;
  youtube_api_key_set: boolean;
  youtube_api_key_masked: string;
  tikhub_api_key_set: boolean;
  tikhub_api_key_masked: string;
  youtube_quota_used_today: number;
  youtube_quota_daily: number;
  providers: ProviderInfo[];
  llm: { configured: boolean; reason: string; provider: string; model: string };
  scheduler: { enabled: boolean; interval_hours: number; last_run_at: string | null; next_run_at: string | null };
};

export type ViralSettingsPatch = Partial<
  Pick<ViralSettings, "youtube_mode" | "default_region" | "douyin_mode" | "tikhub_base_url" | "translate_titles" | "auto_rescan_enabled" | "auto_rescan_hours">
> & { youtube_api_key?: string; tikhub_api_key?: string };

export type ExpandResult = {
  youtube: string[];
  douyin: string[];
  synonyms_vi: string[];
  source: "llm" | "fallback";
  error?: string;
  message?: string;
};

export type VideoQuery = {
  topic_id?: string;
  scan_id?: string;
  platform?: ViralPlatform;
  status?: string;
  sort?: ViralSort;
  window?: ViralWindow;
  min_score?: number;
  min_views?: number;
  duration?: ViralDuration;
  q?: string;
  source_language?: SourceLanguage;
  offset?: number;
  limit?: number;
};

export class ViralApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

const BASE = "/api/viral";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let code = "http_error";
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail?.message) {
        detail = body.detail.message;
        code = body.detail.code ?? code;
      } else if (Array.isArray(body?.detail)) detail = body.detail.map((d: { msg?: string }) => d?.msg ?? "").join("; ");
    } catch {
      /* keep status text */
    }
    throw new ViralApiError(code, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const getViralSettings = () => request<ViralSettings>("/settings");
export const updateViralSettings = (patch: ViralSettingsPatch) =>
  request<ViralSettings>("/settings", { method: "PUT", body: JSON.stringify(patch) });
export const getDouyinHot = (limit = 30) => request<{ words: HotWord[]; fetched_at: string }>(`/douyin/hot${qs({ limit })}`);

export const expandKeywords = (topic: string) =>
  request<ExpandResult>("/expand-keywords", { method: "POST", body: JSON.stringify({ topic }) });
export const listTopics = () => request<ViralTopic[]>("/topics");
export const createTopic = (input: { name: string; keywords: Keywords; filters?: ViralFilters; watched?: boolean }) =>
  request<ViralTopic>("/topics", { method: "POST", body: JSON.stringify(input) });
export const updateTopic = (id: string, input: Partial<{ name: string; keywords: Keywords; filters: ViralFilters; watched: boolean }>) =>
  request<ViralTopic>(`/topics/${id}`, { method: "PATCH", body: JSON.stringify(input) });
export const deleteTopic = (id: string) => request<void>(`/topics/${id}`, { method: "DELETE" });

export const startScan = (input: ViralFilters & { topic_id?: string; query?: string; keywords?: Keywords }) =>
  request<ViralScan>("/scan", { method: "POST", body: JSON.stringify(input) });
export const getScan = (id: string) => request<ViralScan>(`/scans/${id}`);
export const listScans = (limit = 10) => request<ViralScan[]>(`/scans${qs({ limit })}`);
export const analyzeUrls = (urls: string[], watch = true) =>
  request<{ videos: ViralVideo[]; creators: ViralTopic[]; scans: { id: string; topic_id: string }[]; errors: { url: string; code: string; message: string }[] }>(
    "/analyze",
    { method: "POST", body: JSON.stringify({ urls, watch }) },
  );

export const listVideos = (query: VideoQuery = {}) =>
  request<{ total: number; offset: number; limit: number; items: ViralVideo[] }>(`/videos${qs(query)}`);
export const topVideos = (limit = 3, hours = 72) => request<{ items: ViralVideo[]; topics: number }>(`/top${qs({ limit, hours })}`);
export const getVideo = (id: string) => request<ViralVideoDetail>(`/videos/${id}`);
export const updateVideo = (id: string, input: Partial<{ status: ViralStatus; watched: boolean; media_job_id: string | null; title_vi: string }>) =>
  request<ViralVideoDetail>(`/videos/${id}`, { method: "PATCH", body: JSON.stringify(input) });
export const refreshVideos = (video_ids?: string[]) =>
  request<{ updated: number; errors: Record<string, { code: string; message: string }>; items: ViralVideo[] }>("/videos/refresh", {
    method: "POST",
    body: JSON.stringify(video_ids ? { video_ids } : {}),
  });
export const translateVideo = (id: string) => request<ViralVideoDetail>(`/videos/${id}/translate`, { method: "POST" });
export const analyzeVideo = (id: string, refresh = false) =>
  request<{ cached: boolean; analysis: ViralAnalysis; analysis_at: string }>(`/videos/${id}/analysis${qs({ refresh })}`, { method: "POST" });
export const saveIdea = (id: string, input: { title?: string; notes?: string } = {}) =>
  request<{ post_id: string; title: string; video: ViralVideo }>(`/videos/${id}/idea`, { method: "POST", body: JSON.stringify(input) });
