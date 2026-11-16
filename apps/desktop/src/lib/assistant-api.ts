// Client for the AI assistant backend (`/api/assistant/*`, see apps/api/app/assistant_api.py).
// Uses the same base URL logic as api.ts so dev goes through the Vite proxy.
import { API_BASE_URL } from "../api";

const BASE = `${API_BASE_URL}/api/assistant`;

// --- Types --------------------------------------------------------------------

export type AssistantMode = "chat" | "write" | "strategy";
export type ProviderId = "anthropic" | "openai_compatible";
export type Effort = "auto" | "low" | "medium" | "high";
export type ToolsMode = "auto" | "on" | "off";

export type ProviderPreset = {
  id: string;
  label: string;
  base_url: string;
  requires_key: boolean;
  default_model: string;
};

export type ProviderInfo = {
  id: ProviderId;
  label: string;
  requires_key: boolean;
  supports_tools: boolean | string;
  supports_model_list: boolean;
  default_model?: string;
  suggested_models?: string[];
  presets?: ProviderPreset[];
};

export type AnthropicSettings = {
  model: string;
  base_url: string;
  effort: Effort;
  refusal_fallback: boolean;
  api_key_set: boolean;
  api_key_masked: string;
  configured: boolean;
  configured_reason: string;
};

export type OpenAICompatibleSettings = {
  preset: string;
  base_url: string;
  model: string;
  /** OpenRouter only: model for writing content (write mode, post drafts); "" = use `model`. */
  content_model?: string;
  tools_mode: ToolsMode;
  api_key_set: boolean;
  api_key_masked: string;
  configured: boolean;
  configured_reason: string;
};

export type AssistantSettings = {
  active_provider: ProviderId;
  max_tokens: number;
  anthropic: AnthropicSettings;
  openai_compatible: OpenAICompatibleSettings;
};

/** Partial update. `api_key`: omit/null = keep, "" = clear, other string = replace. */
export type AssistantSettingsPatch = {
  active_provider?: ProviderId;
  max_tokens?: number;
  anthropic?: Partial<Pick<AnthropicSettings, "model" | "base_url" | "effort" | "refusal_fallback">> & { api_key?: string | null };
  openai_compatible?: Partial<Pick<OpenAICompatibleSettings, "preset" | "base_url" | "model" | "content_model" | "tools_mode">> & {
    api_key?: string | null;
  };
};

export type ConnectionTestResult = {
  provider?: ProviderId;
  ok: boolean;
  latency_ms?: number;
  model?: string;
  message: string;
  error_code?: string;
  models?: string[];
};

export type ModelInfo = { id: string; name: string };

export type Option = { id: string; label: string };

export type QuickAction = {
  id: string;
  mode: AssistantMode;
  label: string;
  options: ChatOptions;
  prompt: string;
  needs_job: boolean;
};

export type QuickActionsResponse = {
  modes: Option[];
  platforms: Option[];
  tones: Option[];
  lengths: Option[];
  actions: QuickAction[];
};

export type ChatOptions = {
  platform?: string;
  tone?: string;
  length?: string;
  language?: string;
  content_type?: string;
};

export type Conversation = {
  id: string;
  title: string;
  mode: AssistantMode;
  job_id: string | null;
  provider: string | null;
  model: string | null;
  options: ChatOptions | null;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type ToolCall = { id: string; name: string; input: Record<string, unknown> };

export type Message = {
  id: string;
  seq: number;
  role: "user" | "assistant" | "tool";
  content: string;
  mode: AssistantMode | null;
  tool_calls: ToolCall[] | null;
  tool_call_id: string | null;
  tool_name: string | null;
  is_error: boolean;
  provider: string | null;
  model: string | null;
  stop_reason: string | null;
  error: string | null;
  usage: Record<string, number> | null;
  created_at: string;
};

export type ConversationDetail = Conversation & { messages: Message[] };

export type ChatRequest = {
  message: string;
  conversation_id?: string | null;
  mode?: AssistantMode;
  options?: ChatOptions;
  job_id?: string | null;
  provider?: ProviderId | null;
  /** Use this model instead of the provider's saved one. */
  model?: string | null;
};

export type JobContent = {
  title: string;
  alt_titles: string[];
  description: string;
  hashtags: string[];
  tags: string[];
  tiktok_caption: string;
  facebook_post: string;
  summary: string;
};

export type JobContentResponse = {
  job_id: string;
  provider: string;
  model: string;
  transcript_used: boolean;
  transcript_truncated: boolean;
  content: JobContent;
};

export type JobContentRequest = {
  platform?: string;
  tone?: string;
  language?: string;
  extra_instructions?: string;
  provider?: ProviderId | null;
  model?: string | null;
};

// --- Errors -------------------------------------------------------------------

/** Error with the backend's machine-readable `code` (see CONTRACT_assistant.md). */
export class AssistantError extends Error {
  code: string;
  status?: number;
  httpStatus?: number;
  constructor(code: string, message: string, status?: number, httpStatus?: number) {
    super(message);
    this.name = "AssistantError";
    this.code = code;
    this.status = status;
    this.httpStatus = httpStatus;
  }
}

export function toAssistantError(error: unknown): AssistantError {
  if (error instanceof AssistantError) return error;
  if (error instanceof DOMException && error.name === "AbortError") return new AssistantError("cancelled", "");
  // fetch() itself failing means the local backend is unreachable.
  if (error instanceof TypeError) return new AssistantError("backend_offline", error.message);
  return new AssistantError("unknown", error instanceof Error ? error.message : String(error));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      ...init,
    });
  } catch (error) {
    throw toAssistantError(error);
  }
  if (!response.ok) {
    let code = "http_error";
    let message = `${response.status} ${response.statusText}`;
    let status: number | undefined;
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (detail && typeof detail === "object" && !Array.isArray(detail)) {
        code = detail.code || code;
        message = detail.message || message;
        status = detail.status;
      } else if (typeof detail === "string") {
        message = detail;
      } else if (Array.isArray(detail) && response.status === 422) {
        code = "validation";
        message = detail.map((d: { msg?: string }) => d?.msg).filter(Boolean).join("; ") || message;
      }
    } catch {
      /* keep status text */
    }
    throw new AssistantError(code, message, status, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

// --- Providers & settings -------------------------------------------------------

export const getAssistantProviders = () => request<{ providers: ProviderInfo[] }>("/providers");
export const getAssistantSettings = () => request<AssistantSettings>("/settings");
export const updateAssistantSettings = (patch: AssistantSettingsPatch) =>
  request<AssistantSettings>("/settings", json("PUT", patch));
export const testAssistantConnection = (body: AssistantSettingsPatch & { provider?: ProviderId }) =>
  request<ConnectionTestResult>("/settings/test", json("POST", body));
export const listAssistantModels = (provider?: ProviderId) =>
  request<{ provider: ProviderId; models: ModelInfo[] }>(`/models${provider ? `?provider=${provider}` : ""}`);
export const getQuickActions = () => request<QuickActionsResponse>("/quick-actions");

// --- Conversations ----------------------------------------------------------------

export const listConversations = (params: { limit?: number; offset?: number; job_id?: string } = {}) => {
  const query = new URLSearchParams();
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset != null) query.set("offset", String(params.offset));
  if (params.job_id) query.set("job_id", params.job_id);
  const qs = query.toString();
  return request<{ total: number; items: Conversation[] }>(`/conversations${qs ? `?${qs}` : ""}`);
};
export const getConversation = (id: string, includeTools = true) =>
  request<ConversationDetail>(`/conversations/${encodeURIComponent(id)}?include_tools=${includeTools}`);
export const updateConversation = (
  id: string,
  patch: { title?: string; mode?: AssistantMode; job_id?: string | null; options?: ChatOptions },
) => request<Conversation>(`/conversations/${encodeURIComponent(id)}`, json("PATCH", patch));
export const deleteConversation = (id: string) =>
  request<void>(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });

// --- Structured content for a dubbing job ----------------------------------------

export const generateJobContent = (jobId: string, body: JobContentRequest = {}, signal?: AbortSignal) =>
  request<JobContentResponse>(`/jobs/${encodeURIComponent(jobId)}/generate-content`, { ...json("POST", body), signal });

export type PostDraft = { title: string; caption: string; hashtags: string[] };

export type PostDraftsRequest = {
  platform?: string | null;
  tone?: string;
  count?: number;
  extra_instructions?: string;
  model?: string | null;
};

export type PostDraftsResponse = {
  job_id: string;
  provider: string;
  model: string;
  transcript_used: boolean;
  drafts: PostDraft[];
};

/** Ready-to-post versions (title, caption, hashtags) of a dubbed video for one platform. */
export const generatePostDrafts = (jobId: string, body: PostDraftsRequest = {}, signal?: AbortSignal) =>
  request<PostDraftsResponse>(`/jobs/${encodeURIComponent(jobId)}/post-drafts`, { ...json("POST", body), signal });

// --- Streaming chat (SSE over fetch) ---------------------------------------------

export type ChatStreamEvent =
  | { event: "conversation"; data: { conversation_id: string; title: string; created: boolean; user_message_id: string } }
  | { event: "text"; data: { delta: string } }
  | { event: "tool_call"; data: ToolCall }
  | { event: "tool_result"; data: { id: string; name: string; ok: boolean; summary: string } }
  | { event: "notice"; data: { code: string; message: string } }
  | { event: "error"; data: { code: string; message: string; status?: number; detail?: unknown } }
  | {
      event: "done";
      data: {
        conversation_id: string;
        message_id: string;
        stop_reason: string;
        provider: string;
        model: string;
        usage?: Record<string, number>;
        text: string;
      };
    };

/** Parses one SSE frame ("event: x\ndata: {...}") into an event, or null for comments/unknown frames. */
function parseFrame(frame: string): ChatStreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const idx = line.indexOf(":");
    const field = idx === -1 ? line : line.slice(0, idx);
    let value = idx === -1 ? "" : line.slice(idx + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) } as ChatStreamEvent;
  } catch {
    return null;
  }
}

/**
 * POST /chat and dispatch each SSE event to `onEvent`. Resolves when the stream ends.
 * Abort via `signal` (the backend then saves the partial answer as `cancelled`).
 * Throws AssistantError for HTTP errors before streaming starts (e.g. 422) and network failures.
 */
export async function streamChat(body: ChatRequest, onEvent: (event: ChatStreamEvent) => void, signal?: AbortSignal) {
  let response: Response;
  try {
    response = await fetch(`${BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    throw toAssistantError(error);
  }
  if (!response.ok || !response.body) {
    let message = `${response.status} ${response.statusText}`;
    let code = "http_error";
    try {
      const payload = await response.json();
      if (payload?.detail?.code) code = payload.detail.code;
      if (payload?.detail?.message) message = payload.detail.message;
      else if (response.status === 422) code = "validation";
    } catch {
      /* ignore */
    }
    throw new AssistantError(code, message, undefined, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, "\n");
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const parsed = parseFrame(buffer.slice(0, sep));
        buffer = buffer.slice(sep + 2);
        if (parsed) onEvent(parsed);
        sep = buffer.indexOf("\n\n");
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) {
      const parsed = parseFrame(buffer.trim());
      if (parsed) onEvent(parsed);
    }
  } catch (error) {
    throw toAssistantError(error);
  } finally {
    reader.releaseLock();
  }
}

/** Backend timestamps are UTC ISO strings without a zone suffix. */
export function parseServerDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(value);
  const date = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}
