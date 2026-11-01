import { useEffect, useMemo, useState } from "react";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { getQuickActions } from "../../lib/assistant-api";
import type { AssistantError, QuickActionsResponse } from "../../lib/assistant-api";

// Quick actions / option lists rarely change: fetch once per session and share.
let quickActionsPromise: Promise<QuickActionsResponse> | null = null;

export function loadQuickActions(): Promise<QuickActionsResponse> {
  if (!quickActionsPromise) {
    quickActionsPromise = getQuickActions().catch((error) => {
      quickActionsPromise = null;
      throw error;
    });
  }
  return quickActionsPromise;
}

/**
 * The server sends Vietnamese labels/prompts; swap in the UI language when i18n has a key
 * for that id (`as_qa_<id>_label|prompt`, `as_tone_<id>`, `as_len_<id>`), else keep the server text.
 */
function localizeQuickActions(data: QuickActionsResponse, t: T): QuickActionsResponse {
  const dict = t as Record<string, string>;
  const pick = (key: string, fallback: string) => dict[key] || fallback;
  return {
    ...data,
    tones: data.tones.map((o) => ({ ...o, label: pick(`as_tone_${o.id}`, o.label) })),
    lengths: data.lengths.map((o) => ({ ...o, label: pick(`as_len_${o.id}`, o.label) })),
    actions: data.actions.map((a) => ({
      ...a,
      label: pick(`as_qa_${a.id}_label`, a.label),
      prompt: pick(`as_qa_${a.id}_prompt`, a.prompt),
    })),
  };
}

export function useQuickActions(): QuickActionsResponse | null {
  const { t } = useT();
  const [data, setData] = useState<QuickActionsResponse | null>(null);
  useEffect(() => {
    let alive = true;
    loadQuickActions()
      .then((value) => alive && setData(value))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return useMemo(() => (data ? localizeQuickActions(data, t) : null), [data, t]);
}

/**
 * Why an unconfigured provider can't chat yet, in the UI language (mirrors
 * `provider_configured()` on the server, whose reason text is Vietnamese only).
 */
export function notConfiguredReason(
  t: T,
  provider: "anthropic" | "openai_compatible",
  section: { base_url?: string; model?: string },
): string {
  if (provider === "anthropic") return t.as_reason_anthropic_key;
  if (!section.base_url) return t.as_reason_base_url;
  if (!section.model) return t.as_reason_model;
  return t.as_reason_api_key;
}

export type ErrorAction = "settings" | "retry" | null;

/** Maps backend error codes to a Vietnamese/English title + the most useful next action. */
export function describeError(t: T, code: string, message?: string): { title: string; body: string; action: ErrorAction } {
  const body = message || "";
  switch (code) {
    case "not_configured":
      return { title: t.as_err_not_configured, body, action: "settings" };
    case "auth":
      return { title: t.as_err_auth, body, action: "settings" };
    case "not_found":
      return { title: t.as_err_not_found, body, action: "settings" };
    case "model_missing":
      return { title: t.as_err_model_missing, body, action: "settings" };
    case "rate_limited":
      return { title: t.as_err_rate_limited, body, action: "retry" };
    case "provider_unavailable":
      return { title: t.as_err_unavailable, body, action: "retry" };
    case "network":
      return { title: t.as_err_network, body, action: "settings" };
    case "timeout":
      return { title: t.as_err_timeout, body, action: "retry" };
    case "refusal":
      return { title: t.as_err_refusal, body: body || t.as_err_refusal_body, action: null };
    case "invalid_output":
      return { title: t.as_err_invalid_output, body, action: "retry" };
    case "bad_request":
      return { title: t.as_err_bad_request, body, action: "settings" };
    case "job_not_found":
      return { title: t.as_err_job_not_found, body, action: null };
    case "backend_offline":
      return { title: t.as_err_backend_offline, body: t.as_err_backend_offline_body, action: "retry" };
    case "cancelled":
      return { title: t.as_stopped, body: "", action: null };
    default:
      return { title: t.as_err_generic, body, action: "retry" };
  }
}

export function errorFrom(t: T, error: AssistantError) {
  return describeError(t, error.code, error.message);
}

/** Running label for a tool call chip, e.g. "Đang đọc transcript…". */
export function toolRunningLabel(t: T, name: string): string {
  switch (name) {
    case "list_dubbing_jobs":
      return t.as_tool_list_jobs;
    case "get_job_transcript":
      return t.as_tool_transcript;
    case "list_projects":
      return t.as_tool_projects;
    case "get_channels_overview":
      return t.as_tool_channels;
    case "get_top_videos":
      return t.as_tool_top_videos;
    default:
      return t.as_tool_generic;
  }
}

/** Finished label for a tool call chip, e.g. "Đã đọc transcript". */
export function toolDoneLabel(t: T, name: string): string {
  switch (name) {
    case "list_dubbing_jobs":
      return t.as_tool_list_jobs_done;
    case "get_job_transcript":
      return t.as_tool_transcript_done;
    case "list_projects":
      return t.as_tool_projects_done;
    case "get_channels_overview":
      return t.as_tool_channels_done;
    case "get_top_videos":
      return t.as_tool_top_videos_done;
    default:
      return t.as_tool_generic_done;
  }
}
