import type {
  ColabAccounts,
  CreatedMediaJob,
  Job,
  JobOutput,
  NewJobInput,
  OmniVoiceColabStatus,
  OmniVoiceLocalSetup,
  QuickVideoJobInput,
  RuntimeOptions,
  RuntimeSettings,
  TranslationRouterStatus,
  StorageUsage,
  SourceVideoUpload,
  SubtitleStyle,
  SubtitleStylePreset,
  SubtitleStyles,
  VoiceProfile,
  WorkspaceSettings,
  ZeroTTSCommunityVoice,
} from "./types";

// In dev mode (Vite), use a relative base URL so requests go through the
// Vite proxy (vite.config.ts) — no CORS involved. In production (Tauri
// bundle), fall back to the absolute localhost address.
export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ||
  (import.meta.env.DEV ? "" : "http://127.0.0.1:18386");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function ping(): Promise<boolean> {
  try {
    await request("/health");
    return true;
  } catch {
    return false;
  }
}

/** Retry ping until the backend answers or we exhaust attempts. */
export async function pingWithRetry(
  attempts = 20,
  intervalMs = 1000,
  onAttempt?: (remaining: number) => void,
): Promise<boolean> {
  for (let i = 0; i < attempts; i++) {
    if (i > 0) await new Promise((r) => setTimeout(r, intervalMs));
    onAttempt?.(attempts - i);
    if (await ping()) return true;
  }
  return false;
}

// --- Settings ---------------------------------------------------------------
export const getRuntimeSettings = () =>
  request<RuntimeSettings>("/settings/runtime", { cache: "no-store" });

export const getTranslationRouterStatus = () =>
  request<TranslationRouterStatus>("/settings/runtime/translation/status", { cache: "no-store" });

export const updateRuntimeSettings = (payload: Partial<RuntimeSettings>) =>
  request<RuntimeSettings>("/settings/runtime", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const getRuntimeOptions = () =>
  request<RuntimeOptions>("/settings/runtime/options", { cache: "no-store" });

export const getStorageUsage = () =>
  request<StorageUsage>("/storage-management/usage", { cache: "no-store" });

export const cleanupStorage = (target: "temporary" | "zerotts_cache") =>
  request<{ removed_bytes: number; usage: StorageUsage }>("/storage-management/cleanup", {
    method: "POST",
    body: JSON.stringify({ target }),
  });

export const revealStorage = () =>
  request<{ status: string; folder: string }>("/storage-management/reveal", {
    method: "POST",
  });

export const getVoiceOptions = () =>
  request<VoiceProfile[]>("/voice-options", { cache: "no-store" });

export const createVoiceOption = (profile: Partial<VoiceProfile>) =>
  request<VoiceProfile>("/voice-options", {
    method: "POST",
    body: JSON.stringify(profile),
  });

export const deleteVoiceOption = (id: string) =>
  request<void>(`/voice-options/${encodeURIComponent(id)}`, { method: "DELETE" });

export async function importZeroTtsVoice(file: File): Promise<VoiceProfile[]> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE_URL}/voice-options/zerotts/import`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    let detail = `Import failed: ${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return response.json();
}

export const getZeroTtsCommunityVoices = () =>
  request<ZeroTTSCommunityVoice[]>("/voice-options/zerotts/community", {
    cache: "no-store",
  });

export const installZeroTtsCommunityVoice = (id: string) =>
  request<VoiceProfile>(
    `/voice-options/zerotts/community/${encodeURIComponent(id)}/install`,
    { method: "POST" },
  );

// --- Colab CLI runtime -------------------------------------------------------
const COLAB_BASE = "/settings/runtime/omnivoice/colab";

export const getColabStatus = () =>
  request<OmniVoiceColabStatus>(COLAB_BASE, { cache: "no-store" });

export const launchColab = () =>
  request<OmniVoiceColabStatus>(`${COLAB_BASE}/launch`, { method: "POST" });

export const stopColab = () =>
  request<OmniVoiceColabStatus>(COLAB_BASE, { method: "DELETE" });

export const setupColabCli = () =>
  request<OmniVoiceColabStatus>(`${COLAB_BASE}/setup`, { method: "POST" });

export const switchColabAccount = () =>
  request<OmniVoiceColabStatus>(`${COLAB_BASE}/switch-account`, { method: "POST" });

/** List Google accounts saved from previous Colab logins. */
export const getColabAccounts = () =>
  request<ColabAccounts>(`${COLAB_BASE}/accounts`, { cache: "no-store" });

/** Switch to a previously used Google account without a fresh browser login. */
export const switchColabSavedAccount = (slug: string) =>
  request<OmniVoiceColabStatus>(`${COLAB_BASE}/accounts/switch`, {
    method: "POST",
    body: JSON.stringify({ slug }),
  });

/** Forget a saved Google account (removes its archived token). */
export const removeColabAccount = (slug: string) =>
  request<ColabAccounts>(`${COLAB_BASE}/accounts/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });

export const submitColabAuthCode = (code: string) =>
  request<OmniVoiceColabStatus>(`${COLAB_BASE}/auth-code`, {
    method: "POST",
    body: JSON.stringify({ code }),
  });

// --- Local OmniVoice setup (auto-provision .venv-omnivoice) ------------------
const LOCAL_SETUP_BASE = "/settings/runtime/omnivoice/local/setup";

export const getLocalOmniVoiceSetup = () =>
  request<OmniVoiceLocalSetup>(LOCAL_SETUP_BASE, { cache: "no-store" });

export const startLocalOmniVoiceSetup = () =>
  request<OmniVoiceLocalSetup>(LOCAL_SETUP_BASE, { method: "POST" });

export const getWorkspaceSettings = () =>
  request<WorkspaceSettings>("/settings/workspace", { cache: "no-store" });

export const updateWorkspaceSettings = (body: Partial<WorkspaceSettings>) =>
  request<WorkspaceSettings>("/settings/workspace", {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const getSubtitleStyles = () =>
  request<SubtitleStyles>("/subtitle-styles", { cache: "no-store" });

/** Save the current subtitle styling as a reusable named preset. */
export const createSubtitleStyle = (payload: {
  name: string;
  style: Partial<SubtitleStyle>;
  id?: string;
  make_active?: boolean;
}) =>
  request<SubtitleStylePreset>("/subtitle-styles", {
    method: "POST",
    body: JSON.stringify({ make_active: false, ...payload }),
  });

export function voicePreviewUrl(voiceId: string) {
  return `${API_BASE_URL}/voice-options/${encodeURIComponent(voiceId)}/preview`;
}

export const previewSourceVideo = (
  video_url: string,
  download_quality: "reliable" | "best" = "reliable",
) =>
  request<SourceVideoUpload>("/source-videos/preview", {
    method: "POST",
    body: JSON.stringify({ video_url, download_quality }),
  });

// --- Quick Video (resolved-configuration job) ------------------------------
export const createQuickVideoJob = (payload: QuickVideoJobInput) =>
  request<CreatedMediaJob>("/jobs/from-quick-video", {
    method: "POST",
    body: JSON.stringify(payload),
  });

// --- Jobs -------------------------------------------------------------------
export const listJobs = () => request<Job[]>("/jobs", { cache: "no-store" });

export const getJob = (id: string) =>
  request<Job>(`/jobs/${id}`, { cache: "no-store" });

export const getJobOutput = (id: string) =>
  request<JobOutput>(`/jobs/${id}/output`, { cache: "no-store" });

export function createJob(input: NewJobInput) {
  return request<Job>("/jobs", {
    method: "POST",
    body: JSON.stringify({
      video_url: input.video_url,
      source_language: "auto",
      target_language: input.target_language,
      voice: input.voice,
      burn_subtitles: input.burn_subtitles,
      download_quality: input.download_quality,
      render_quality: input.render_quality,
    }),
  });
}

export const runJob = (id: string) =>
  request<Job>(`/jobs/${id}/run`, { method: "POST" });

export const retryJob = (id: string) =>
  request<Job>(`/jobs/${id}/retry`, { method: "POST" });

export const rerenderJobVoice = (id: string, voiceId: string) =>
  request<Job>(`/jobs/${id}/rerender-voice`, {
    method: "POST",
    body: JSON.stringify({ voice_id: voiceId }),
  });

export const cancelJob = (id: string) =>
  request<Job>(`/jobs/${id}/cancel`, { method: "POST" });

export const deleteJob = (id: string) =>
  request<void>(`/jobs/${id}`, { method: "DELETE" });

export const revealJob = (id: string) =>
  request<{ status: string; folder: string }>(`/jobs/${id}/reveal`, { method: "POST" });

export async function uploadSourceVideo(file: File): Promise<SourceVideoUpload> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE_URL}/source-videos/upload`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(`Upload failed: ${await response.text()}`);
  return response.json();
}

export async function uploadVoiceReference(file: File): Promise<VoiceReferenceMedia> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE_URL}/voice-references/upload`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(`Upload failed: ${await response.text()}`);
  return response.json();
}

export type VoiceReferenceMedia = {
  filename: string;
  path: string;
  url: string;
  duration: number;
};

export const downloadVoiceReference = (
  video_url: string,
  audio_format: "wav" | "mp3",
) =>
  request<VoiceReferenceMedia>("/voice-references/download", {
    method: "POST",
    body: JSON.stringify({ video_url, audio_format }),
  });

export const trimVoiceReference = (
  path: string,
  start_seconds: number,
  end_seconds: number,
  audio_format: "wav" | "mp3" = "wav",
) =>
  request<VoiceReferenceMedia>("/voice-references/trim", {
    method: "POST",
    body: JSON.stringify({ path, start_seconds, end_seconds, audio_format }),
  });

export const getVoiceReferenceWaveform = (path: string, points = 600) =>
  request<{ duration: number; peaks: number[] }>("/voice-references/waveform", {
    method: "POST",
    body: JSON.stringify({ path, points }),
  });

export function outputUrl(path?: string | null): string | null {
  if (!path) return null;
  if (path.startsWith("http")) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`;
}
