import { API_BASE_URL } from "../api";

/** Auto review (Rà soát tự động) — types and API client for /jobs/{id}/auto-review. */

export type ReviewMode = "report" | "propose" | "safe";
export type ReviewSeverity = "error" | "warning" | "info";
export type ReviewIssueType =
  | "voice_fast"
  | "voice_slow"
  | "voice_overlap"
  | "voice_offset"
  | "dead_air"
  | "voice_missing"
  | "sub_empty"
  | "sub_fast"
  | "sub_short"
  | "sub_long"
  | "sub_lines"
  | "sub_bad_break"
  | "sub_orphan"
  | "sub_fragment";

export type ProposalKind =
  | "borrow_silence"
  | "shift_neighbors"
  | "redistribute"
  | "speed"
  | "best_effort"
  | "extend_subtitle"
  | "split"
  | "merge"
  | "move_words"
  | "rewrite"
  | "delete"
  | "generate_voice";

export type ProposalStatus = "pending" | "applied" | "rejected" | "stale" | "failed" | "reverted_worse";

export type ReviewIssue = {
  cue_id: string;
  type: ReviewIssueType;
  severity: ReviewSeverity;
  category: "voice" | "subtitle";
  start: number;
  end: number;
  values: Record<string, number | string | boolean | null>;
};

export type CueSnapshot = {
  id: string;
  start: number;
  end: number;
  text: string;
  voiced: boolean;
  voice_start: number | null;
  voice_len: number | null;
  natural: number | null;
  speed: number | null;
  cps: number | null;
  /** Take as heard now / the unfitted take (played at the new speed for "Nghe thử"). */
  audio_url?: string | null;
  source_url?: string | null;
};

export type ReviewProposal = {
  id: string;
  kind: ProposalKind;
  cue_ids: string[];
  issue_types: ReviewIssueType[];
  risk: "low" | "medium";
  needs_tts: boolean;
  needs_llm: boolean;
  ops: { op: string; id: string; text?: string; [key: string]: unknown }[];
  before: CueSnapshot[];
  after: CueSnapshot[];
  confidence: number;
  predicted: {
    speed_before?: number | null;
    speed_after?: number | null;
    penalty_before?: number;
    penalty_after?: number;
    issues_before?: ReviewIssueType[];
    issues_after?: ReviewIssueType[];
    syllables_before?: number;
    syllables_after?: number;
  };
  rewrite?: { mode: "condense" | "expand"; syllables: number; target_syllables: number } | null;
  status: ProposalStatus;
  depends_on: string[];
  error?: string;
  batch_id?: string;
};

export type ReviewSummary = {
  score: number;
  cues: number;
  cues_with_issues: number;
  issues: number;
  by_type: Partial<Record<ReviewIssueType, number>>;
  by_severity: Record<ReviewSeverity, number>;
  by_category: { voice: number; subtitle: number };
  median_speed: number | null;
  natural_rate: number;
};

export type ReviewBatch = {
  id: string;
  label: string;
  auto: boolean;
  created_at: string;
  proposal_ids: string[];
  counts: Record<"applied" | "stale" | "failed" | "reverted_worse", number>;
  score_before: number;
  score_after: number;
  cues_improved?: number;
  cues_worse?: number;
  reverted: boolean;
};

export type AutoReview = {
  job_id: string;
  status: "idle" | "running" | "done" | "failed";
  running?: boolean;
  phase?: "measure" | "plan" | "rewrite" | "ready" | "done" | "failed";
  progress?: { done: number; total: number };
  mode?: ReviewMode;
  trigger?: "manual" | "pipeline";
  error?: string | null;
  llm?: { available: boolean; provider?: string | null; requested?: number; accepted?: number; errors?: string[] } | null;
  summary_before?: ReviewSummary | null;
  summary_current?: ReviewSummary | null;
  summary_predicted?: ReviewSummary | null;
  issues: ReviewIssue[];
  proposals: ReviewProposal[];
  batches: ReviewBatch[];
  applying?: { status: "running" | "done" | "failed"; done?: number; total?: number; error?: string; batch_id?: string } | null;
  updated_at?: string;
};

export type ReviewBadgeSummary = {
  status: "none" | "running" | "done" | "failed";
  score?: number | null;
  score_before?: number | null;
  score_predicted?: number | null;
  issues?: number;
  errors?: number;
  pending?: number;
  applied?: number;
  applying?: boolean;
  mode?: ReviewMode;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { headers: { "Content-Type": "application/json" }, ...init });
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
  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const getAutoReview = (jobId: string) => call<AutoReview>(`/jobs/${jobId}/auto-review`, { cache: "no-store" });
export const startAutoReview = (jobId: string, mode: ReviewMode) => call<AutoReview>(`/jobs/${jobId}/auto-review`, json({ mode }));
export const applyAutoReview = (jobId: string, scope: "selected" | "all" | "safe", ids: string[] = []) =>
  call<AutoReview>(`/jobs/${jobId}/auto-review/apply`, json({ scope, ids }));
export const rejectAutoReview = (jobId: string, ids: string[]) => call<AutoReview>(`/jobs/${jobId}/auto-review/reject`, json({ ids }));
export const revertAutoReviewBatch = <W,>(jobId: string, batchId: string) =>
  call<{ review: AutoReview; workspace: W }>(`/jobs/${jobId}/auto-review/batches/${batchId}/revert`, { method: "POST" });
export const previewAutoReview = (jobId: string, proposalId: string) =>
  call<{ url: string; natural: number; slot: number; speed: number | null }>(
    `/jobs/${jobId}/auto-review/proposals/${proposalId}/preview`,
    { method: "POST" },
  );
export const getAutoReviewSummaries = (ids: string[]) =>
  ids.length
    ? call<Record<string, ReviewBadgeSummary>>(`/jobs/auto-review/summaries?ids=${encodeURIComponent(ids.join(","))}`, { cache: "no-store" })
    : Promise.resolve({} as Record<string, ReviewBadgeSummary>);

/** Absolute URL for a /storage/... path (the dev proxy forwards it; packaged builds need the host). */
export const storageUrl = (path: string | null | undefined) => (path ? `${API_BASE_URL}${path}` : null);

export const isSafeProposal = (proposal: ReviewProposal) => proposal.risk === "low" && !proposal.needs_tts && !proposal.needs_llm;

/** Word-level diff (LCS) for before/after text. */
export function wordDiff(before: string, after: string): { text: string; kind: "same" | "del" | "add" }[] {
  const a = before.split(/\s+/).filter(Boolean);
  const b = after.split(/\s+/).filter(Boolean);
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { text: string; kind: "same" | "del" | "add" }[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ text: a[i], kind: "same" });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ text: a[i++], kind: "del" });
    else out.push({ text: b[j++], kind: "add" });
  }
  while (i < a.length) out.push({ text: a[i++], kind: "del" });
  while (j < b.length) out.push({ text: b[j++], kind: "add" });
  return out;
}

/** Remember to open the editor on the review tab (read by the editor's saved layout). */
export function preferReviewTab(): void {
  try {
    const key = "vd.editor.layout";
    const current = JSON.parse(localStorage.getItem(key) || "{}");
    localStorage.setItem(key, JSON.stringify({ ...current, tab: "review" }));
  } catch {
    /* per-viewer convenience only */
  }
}
