// Client for the content calendar API (apps/api/app/calendar_api.py, prefix /api/calendar).
// Datetimes are naive UTC ISO strings (parse with `parseUtc` from channels-api); send UTC ISO
// with a `Z` (Date#toISOString) and the backend converts.
import { API_BASE_URL } from "../api";
import type { ChannelStatus, Platform } from "./channels-api";

export type PostStatus = "idea" | "draft" | "scheduled" | "published" | "failed" | "cancelled";
export const POST_STATUSES: PostStatus[] = ["idea", "draft", "scheduled", "published", "failed", "cancelled"];

export type PostColor = "" | "blue" | "violet" | "pink" | "cyan" | "green" | "amber" | "slate";
export const POST_COLORS: PostColor[] = ["blue", "violet", "pink", "cyan", "green", "amber", "slate"];

export type CalendarPost = {
  id: string;
  title: string;
  caption: string;
  hashtags: string[];
  platform: Platform | null;
  channel_id: string | null;
  channel: {
    id: string;
    name: string;
    handle: string | null;
    platform: Platform;
    avatar_url: string | null;
    status: ChannelStatus;
  } | null;
  media_job_id: string | null;
  media_job: { id: string; title: string; thumbnail_url: string | null; status: string } | null;
  scheduled_at: string | null;
  status: PostStatus;
  published_at: string | null;
  published_url: string;
  channel_video_id: string | null;
  channel_video: {
    id: string;
    title: string;
    url: string;
    views: number | null;
    likes: number | null;
    comments: number | null;
    thumbnail_url: string;
    published_at: string | null;
  } | null;
  notes: string;
  color: PostColor;
  label: string;
  created_at: string;
  updated_at: string;
};

export type PostInput = {
  title?: string;
  caption?: string;
  hashtags?: string[] | string;
  platform?: Platform | null;
  channel_id?: string | null;
  media_job_id?: string | null;
  scheduled_at?: string | null;
  status?: PostStatus;
  published_url?: string;
  notes?: string;
  color?: PostColor;
  label?: string;
};

export type PostQuery = {
  start?: string;
  end?: string;
  platform?: Platform | "";
  channel_id?: string;
  status?: PostStatus | "";
  unscheduled?: boolean;
  q?: string;
};

export type VideoCandidate = {
  id: string;
  channel_id: string;
  channel_name: string;
  platform: Platform;
  title: string;
  url: string;
  thumbnail_url: string;
  published_at: string | null;
  views: number | null;
  score: number;
};

export type SlotSuggestions = {
  basis: "history" | "default";
  hours: number[];
  sample_size: number;
  slots: { scheduled_at: string; local_hour: number; rank: number; avg_views: number | null }[];
};

const BASE = "/api/calendar";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = body.detail.map((d: { msg?: string }) => d?.msg ?? "").join("; ");
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
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

export function listPosts(query: PostQuery = {}) {
  return request<CalendarPost[]>(`/posts${qs(query)}`);
}

export function getPost(id: string) {
  return request<CalendarPost>(`/posts/${id}`);
}

export function upcomingPosts(limit = 5) {
  return request<CalendarPost[]>(`/upcoming${qs({ limit })}`);
}

export function createPost(input: PostInput & { title: string }) {
  return request<CalendarPost>("/posts", { method: "POST", body: JSON.stringify(input) });
}

export function updatePost(id: string, input: PostInput) {
  return request<CalendarPost>(`/posts/${id}`, { method: "PATCH", body: JSON.stringify(input) });
}

export function deletePost(id: string) {
  return request<void>(`/posts/${id}`, { method: "DELETE" });
}

export function duplicatePost(id: string, scheduledAt?: string | null) {
  return request<CalendarPost>(`/posts/${id}/duplicate`, {
    method: "POST",
    body: JSON.stringify(scheduledAt === undefined ? {} : { scheduled_at: scheduledAt }),
  });
}

export function publishPost(id: string, input: { published_url?: string; channel_video_id?: string | null; published_at?: string | null }) {
  return request<CalendarPost>(`/posts/${id}/publish`, { method: "POST", body: JSON.stringify(input) });
}

export function videoCandidates(id: string) {
  return request<VideoCandidate[]>(`/posts/${id}/video-candidates`);
}

export function slotSuggestions(params: { platform?: Platform | null; channel_id?: string | null; days?: number; limit?: number }) {
  return request<SlotSuggestions>(
    `/suggestions${qs({ ...params, tz_offset_minutes: -new Date().getTimezoneOffset() })}`,
  );
}

// Dashboard -> Calendar hand-off ("open this post").
const INTENT_KEY = "vd.calendar.intent";
export function setCalendarIntent(postId: string) {
  try {
    sessionStorage.setItem(INTENT_KEY, postId);
  } catch {
    /* ignore */
  }
}
export function takeCalendarIntent(): string | null {
  try {
    const v = sessionStorage.getItem(INTENT_KEY);
    sessionStorage.removeItem(INTENT_KEY);
    return v;
  } catch {
    return null;
  }
}
