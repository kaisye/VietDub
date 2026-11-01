// Client for the multi-channel API (apps/api/app/channels_api.py, prefix /api/channels).
// Contract: datetimes are naive UTC ISO strings (parse with `parseUtc`), metrics are `number | null`.
import { API_BASE_URL } from "../api";

export type Platform = "youtube" | "facebook" | "tiktok";
export const PLATFORMS: Platform[] = ["youtube", "facebook", "tiktok"];

export type ChannelSource = "oauth" | "manual";
export type ChannelStatus = "connected" | "needs_reauth" | "error" | "disconnected" | "manual";
export type SnapshotSource = "sync" | "manual" | "csv";

export type Snapshot = {
  date: string;
  followers: number | null;
  views: number | null;
  video_count: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  source: SnapshotSource;
};

export type Channel = {
  id: string;
  platform: Platform;
  source: ChannelSource;
  external_id: string | null;
  name: string;
  handle: string | null;
  url: string | null;
  avatar_url: string | null;
  status: ChannelStatus;
  last_error: string | null;
  scopes: string[];
  token_expires_at: string | null;
  connected_at: string | null;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
  latest: Snapshot | null;
  video_total: number;
};

export type ChannelVideo = {
  id: string;
  channel_id: string;
  platform: Platform;
  channel_name: string;
  external_id: string | null;
  kind: "video" | "short" | "post";
  title: string;
  published_at: string | null;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  duration_seconds: number | null;
  thumbnail_url: string | null;
  url: string | null;
  media_job_id: string | null;
  source: SnapshotSource;
  updated_at: string;
};

export type PlatformSettings = {
  platform: Platform;
  label: string;
  client_id: string;
  client_id_source: "settings" | "env" | "none";
  client_secret_set: boolean;
  client_secret_masked: string;
  client_secret_source: "settings" | "env" | "none";
  scopes_override: string;
  configured: boolean;
  redirect_uri: string;
  default_scopes: string[];
  scopes: string[];
  docs_url: string;
};

export type ChannelSettings = { redirect_base_url: string; platforms: PlatformSettings[] };

export type OAuthStart = { platform: Platform; state: string; authorize_url: string; redirect_uri: string; expires_at: string };

export type OAuthStatus = {
  state: string;
  platform: Platform;
  status: "pending" | "connected" | "error" | "expired";
  error: string | null;
  channel_ids: string[];
  created_at: string;
  expires_at: string;
  completed_at: string | null;
  channels: Channel[];
};

export type SyncResult = {
  channel_id: string;
  ok: boolean;
  videos?: number;
  error: string | null;
  needs_reauth?: boolean;
  finished_at?: string;
};

export type SyncStatus = {
  running: boolean;
  current_channel_id: string | null;
  queued: number;
  started_at: string | null;
  finished_at: string | null;
  results: SyncResult[];
};

export type SyncStarted = { queued_channel_ids: string[]; started: boolean; running: boolean };

export type DashboardTotals = {
  channels: number;
  connected: number;
  needs_attention: number;
  followers: number;
  views: number;
  video_count: number;
  likes: number;
  comments: number;
  shares: number;
  followers_change: number | null;
  views_change: number | null;
  videos_in_period: number;
  period_video_views: number;
  engagement_rate: number | null;
};

export type DashboardChannel = {
  id: string;
  platform: Platform;
  source: ChannelSource;
  name: string;
  handle: string | null;
  avatar_url: string | null;
  status: ChannelStatus;
  last_synced_at: string | null;
  followers: number | null;
  views: number | null;
  video_count: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  followers_change: number | null;
  views_change: number | null;
  videos_in_period: number;
  engagement_rate: number | null;
};

export type SeriesPoint = {
  date: string;
  followers: number;
  views: number;
  by_platform: Partial<Record<Platform, { followers: number; views: number }>>;
  has_data: boolean;
};

export type ActivityItem = {
  type: "video_published" | "channel_connected" | "channel_added" | "channel_needs_reauth" | "sync_error";
  at: string;
  channel_id: string;
  platform: Platform;
  channel_name: string;
  title: string;
  video_id?: string;
  url?: string | null;
};

export type Dashboard = {
  days: number;
  start_date: string;
  end_date: string;
  generated_at: string;
  totals: DashboardTotals;
  per_platform: Array<DashboardTotals & { platform: Platform }>;
  channels: DashboardChannel[];
  series: SeriesPoint[];
  top_videos: ChannelVideo[];
  top_videos_scope: "period" | "all_time";
  recent_activity: ActivityItem[];
};

export type MetricsInput = {
  date?: string;
  followers?: string | number | null;
  views?: string | number | null;
  video_count?: string | number | null;
  likes?: string | number | null;
  comments?: string | number | null;
  shares?: string | number | null;
};

export type ImportResult = {
  imported: number;
  skipped: number;
  errors: Array<{ row: number; error: string }>;
  channel: Channel;
};

const BASE = "/api/channels";

async function readError(response: Response): Promise<string> {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const body = await response.json();
    if (body?.detail) {
      detail =
        typeof body.detail === "string"
          ? body.detail
          : Array.isArray(body.detail)
            ? body.detail.map((d: { msg?: string }) => d?.msg ?? JSON.stringify(d)).join("; ")
            : JSON.stringify(body.detail);
    }
  } catch {
    /* keep status text */
  }
  return detail;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: init?.body instanceof FormData ? init?.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(await readError(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

/** Parse the API's naive-UTC timestamps. */
export function parseUtc(value?: string | null): Date | null {
  if (!value) return null;
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

// --- Settings & credentials ------------------------------------------------
export const getChannelSettings = () => request<ChannelSettings>("/settings");
export const updateChannelSettings = (redirect_base_url: string) =>
  request<ChannelSettings>("/settings", json("PUT", { redirect_base_url }));
export const updatePlatformCredentials = (
  platform: Platform,
  body: { client_id?: string; client_secret?: string; clear_secret?: boolean; scopes?: string | null },
) => request<PlatformSettings>(`/credentials/${platform}`, json("PUT", body));

// --- OAuth -----------------------------------------------------------------
export const startOAuth = (platform: Platform) => request<OAuthStart>(`/oauth/${platform}/start`, json("POST"));
export const getOAuthStatus = (state: string) => request<OAuthStatus>(`/oauth/status/${encodeURIComponent(state)}`);

// --- Channels --------------------------------------------------------------
export const listChannels = (platform?: Platform) =>
  request<Channel[]>(platform ? `?platform=${platform}` : "");
export const getChannel = (id: string) => request<Channel>(`/${id}`);
export const createManualChannel = (body: {
  platform: Platform;
  name: string;
  url?: string;
  handle?: string;
  avatar_url?: string;
  metrics?: MetricsInput;
}) => request<Channel>("/manual", json("POST", body));
export const updateChannel = (id: string, body: { name?: string; handle?: string; url?: string; avatar_url?: string }) =>
  request<Channel>(`/${id}`, json("PATCH", body));
export const disconnectChannel = (id: string, revoke = true) =>
  request<{ revoked: boolean; revoke_error: string | null; channel: Channel }>(`/${id}/disconnect?revoke=${revoke}`, json("POST"));
export const deleteChannel = (id: string, revoke = true) =>
  request<{ deleted: boolean; revoked: boolean; revoke_error: string | null }>(`/${id}?revoke=${revoke}`, json("DELETE"));

// --- Metrics, videos, CSV --------------------------------------------------
export const getSnapshots = (id: string, days = 90) => request<Snapshot[]>(`/${id}/snapshots?days=${days}`);
export const addSnapshot = (id: string, body: MetricsInput) => request<Channel>(`/${id}/snapshots`, json("POST", body));
export type VideoSort = "published_at" | "views" | "likes" | "comments";
export const listChannelVideos = (id: string, sort: VideoSort = "published_at", limit = 100) =>
  request<ChannelVideo[]>(`/${id}/videos?limit=${limit}&sort=${sort}`);
export const addChannelVideo = (id: string, body: Partial<ChannelVideo> & { title: string }) =>
  request<ChannelVideo>(`/${id}/videos`, json("POST", body));
export function importChannelCsv(id: string, file: File, kind: "snapshots" | "videos") {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  return request<ImportResult>(`/${id}/import`, { method: "POST", body: form });
}
export const linkVideoToJob = (videoId: string, mediaJobId: string | null) =>
  request<ChannelVideo>(`/videos/${videoId}`, json("PATCH", { media_job_id: mediaJobId }));
export const listJobVideos = (mediaJobId: string) => request<ChannelVideo[]>(`/jobs/${mediaJobId}/videos`);

// --- Sync ------------------------------------------------------------------
export const syncChannels = (body: { channel_ids?: string[]; only_stale_hours?: number } = {}) =>
  request<SyncStarted>("/sync", json("POST", body));
export const syncChannel = (id: string) => request<SyncStarted>(`/${id}/sync`, json("POST"));
export const getSyncStatus = () => request<SyncStatus>("/sync/status");

// --- Dashboard -------------------------------------------------------------
export const getDashboard = (days: number, platform?: Platform | "", topLimit = 8) =>
  request<Dashboard>(`/dashboard?days=${days}&platform=${platform ?? ""}&top_limit=${topLimit}`);
