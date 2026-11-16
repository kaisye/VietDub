import clsx from "clsx";
import { Check, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { tt } from "../../i18n";
import type { T } from "../../i18n";
import { generatePostDrafts, toAssistantError } from "../../lib/assistant-api";
import type { PostDraft } from "../../lib/assistant-api";
import type { Platform } from "../../lib/channels-api";
import { contentModelFor } from "../assistant/ModelPicker";
import { PLATFORM_LABEL } from "../channels/platform";
import { Button, InlineNotice, Skeleton } from "../ui";

type Result = { key: string; drafts: PostDraft[]; model: string };

/**
 * AI-written versions of a post for the attached dubbing project. Two drafts are written as
 * soon as a project is attached (when `autoStart`); the user picks one or asks for new ones.
 */
export function PostDrafts({
  t,
  jobId,
  platform,
  autoStart,
  onPick,
}: {
  t: T;
  jobId: string;
  platform: Platform | null;
  autoStart: boolean;
  onPick: (draft: PostDraft) => void;
}) {
  const target = platform ?? "facebook";
  const key = `${jobId}:${target}`;
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const startedFor = useRef<string | null>(null);

  const generate = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError("");
    setPicked(null);
    try {
      const res = await generatePostDrafts(
        jobId,
        { platform: target, count: 2, model: contentModelFor(null) || null },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setResult({ key, drafts: res.drafts, model: res.model });
    } catch (err) {
      const e = toAssistantError(err);
      if (e.code !== "cancelled" && !controller.signal.aborted) setError(e.message);
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setLoading(false);
      }
    }
  }, [jobId, key, target]);

  // Write drafts once when a project gets attached.
  useEffect(() => {
    if (!autoStart || startedFor.current === jobId) return;
    startedFor.current = jobId;
    void generate();
  }, [autoStart, generate, jobId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const stale = result !== null && result.key !== key;

  return (
    <div className="grid gap-2 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-3">
      <div className="flex items-center gap-2">
        <Sparkles size={14} className="shrink-0 text-[var(--accent-violet)]" />
        <span className="min-w-0 flex-1 text-[13px] font-semibold text-[var(--text-primary)]">
          {tt(t.cal_ai_title, { p: PLATFORM_LABEL[target] })}
        </span>
        <Button
          size="xs"
          variant={result || error ? "ghost" : "outline"}
          leadingIcon={<RefreshCw size={12} className={clsx(loading && "animate-spin")} />}
          disabled={loading}
          onClick={() => void generate()}
        >
          {result || error ? (stale ? tt(t.cal_ai_for, { p: PLATFORM_LABEL[target] }) : t.cal_ai_regenerate) : t.cal_ai_generate}
        </Button>
      </div>

      {loading ? (
        <div className="grid gap-2">
          <p className="text-[12px] text-[var(--text-tertiary)]">{t.cal_ai_wait}</p>
          {[0, 1].map((i) => (
            <div key={i} className="grid gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] p-2.5">
              <Skeleton className="h-3 w-2/3" />
              <Skeleton className="h-2.5 w-full" />
              <Skeleton className="h-2.5 w-5/6" />
            </div>
          ))}
        </div>
      ) : error ? (
        <InlineNotice tone="danger">{error}</InlineNotice>
      ) : result ? (
        <div className="grid gap-2">
          {result.drafts.map((draft, index) => (
            <div
              key={index}
              className={clsx(
                "grid gap-1.5 rounded-md border bg-[var(--surface)] p-2.5",
                picked === index ? "border-[var(--primary)]" : "border-[var(--border)]",
              )}
            >
              <div className="text-[13px] font-semibold text-[var(--text-primary)]">{draft.title}</div>
              <p className="line-clamp-4 whitespace-pre-line text-[12.5px] leading-[1.5] text-[var(--text-secondary)]">{draft.caption}</p>
              {draft.hashtags.length ? <div className="text-[12px] text-[var(--primary-hover)]">{draft.hashtags.join(" ")}</div> : null}
              <div className="flex justify-end">
                <Button
                  size="xs"
                  variant={picked === index ? "primary" : "outline"}
                  leadingIcon={picked === index ? <Check size={12} /> : undefined}
                  onClick={() => {
                    setPicked(index);
                    onPick(draft);
                  }}
                >
                  {picked === index ? t.cal_ai_picked : t.cal_ai_use}
                </Button>
              </div>
            </div>
          ))}
          <p className="text-[11px] text-[var(--text-tertiary)]">{tt(t.cal_ai_model, { m: result.model })}</p>
        </div>
      ) : (
        <p className="text-[12px] text-[var(--text-tertiary)]">{t.cal_ai_hint}</p>
      )}
    </div>
  );
}
