import clsx from "clsx";
import { RefreshCw, Settings2, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useT } from "../../i18n";
import { generateJobContent, toAssistantError } from "../../lib/assistant-api";
import type { JobContentResponse, ProviderId } from "../../lib/assistant-api";
import { Badge, Button, InlineNotice, Select, Skeleton, SkeletonText, TextField } from "../ui";
import { CopyButton } from "./CopyButton";
import { describeError, useQuickActions } from "./shared";

export type JobContentGeneratorProps = {
  /** Dubbing job (MediaJob) id — same id as `Job.id` from `listJobs()`. */
  jobId: string;
  /** Initial platform: "youtube" | "facebook" | "tiktok" | "shorts" | "reels". Default "youtube". */
  defaultPlatform?: string;
  /** Initial tone id from /quick-actions (e.g. "friendly", "dramatic", "funny"). Default: server default. */
  defaultTone?: string;
  /** Output language label sent to the model. Default "Tiếng Việt". */
  language?: string;
  /** Override the active provider for this request. */
  provider?: ProviderId | null;
  /** Start generating as soon as the component mounts. Default false. */
  autoGenerate?: boolean;
  /** Narrow single-column layout for side panels (e.g. the editor inspector). Default false. */
  compact?: boolean;
  /** Called when the provider is not configured / key rejected; show the assistant settings. */
  onOpenSettings?: () => void;
  /** Called with the full response after each successful generation. */
  onGenerated?: (result: JobContentResponse) => void;
  className?: string;
};

function Section({
  title,
  copyText,
  children,
  copyLabel,
  copiedLabel,
}: {
  title: string;
  copyText: string;
  children: ReactNode;
  copyLabel: string;
  copiedLabel: string;
}) {
  return (
    <section className="rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-xs)]">
      <header className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-3.5 py-2">
        <h4 className="text-[12.5px] font-semibold text-[var(--text-secondary)]">{title}</h4>
        {copyText ? <CopyButton text={copyText} label={copyLabel} copiedLabel={copiedLabel} size="xs" /> : null}
      </header>
      <div className="px-3.5 py-3 text-[13.5px] leading-6 text-[var(--text-primary)]">{children}</div>
    </section>
  );
}

/**
 * Generates publish-ready metadata (title, description, hashtags, TikTok caption,
 * Facebook post) for a dubbing job via POST /api/assistant/jobs/{id}/generate-content.
 * Self-contained: renders its own controls, loading skeleton, errors and copy buttons.
 */
export function JobContentGenerator({
  jobId,
  defaultPlatform = "youtube",
  defaultTone = "",
  language,
  provider = null,
  autoGenerate = false,
  compact = false,
  onOpenSettings,
  onGenerated,
  className,
}: JobContentGeneratorProps) {
  const { t } = useT();
  const lists = useQuickActions();
  const [platform, setPlatform] = useState(defaultPlatform);
  const [tone, setTone] = useState(defaultTone);
  const [extra, setExtra] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<JobContentResponse | null>(null);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);


  const generate = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const response = await generateJobContent(
        jobId,
        {
          platform: platform || undefined,
          tone: tone || undefined,
          language: language || undefined,
          extra_instructions: extra.trim() || undefined,
          provider,
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setResult(response);
      onGenerated?.(response);
    } catch (err) {
      const e = toAssistantError(err);
      if (e.code !== "cancelled") setError({ code: e.code, message: e.message });
    } finally {
      if (abortRef.current === controller) {
        setLoading(false);
        abortRef.current = null;
      }
    }
  };

  // Reset (and optionally auto-generate) whenever the host switches to a different job.
  useEffect(() => {
    setResult(null);
    setError(null);
    setLoading(false);
    if (autoGenerate) void generate();
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  const content = result?.content;
  const err = error ? describeError(t, error.code, error.message) : null;
  const errAction =
    err?.action === "settings" && onOpenSettings ? (
      <Button size="sm" leadingIcon={<Settings2 size={14} />} onClick={onOpenSettings}>
        {t.as_open_settings}
      </Button>
    ) : err?.action === "retry" ? (
      <Button size="sm" leadingIcon={<RefreshCw size={14} />} onClick={generate}>
        {t.retry}
      </Button>
    ) : undefined;
  const copy = { copyLabel: t.as_copy, copiedLabel: t.as_copied };
  const platforms = lists?.platforms ?? [
    { id: "youtube", label: "YouTube" },
    { id: "facebook", label: "Facebook" },
    { id: "tiktok", label: "TikTok" },
  ];

  return (
    <div className={clsx("grid grid-cols-1 gap-4", className)}>
      <div className={clsx("grid gap-2.5", compact ? "grid-cols-1" : "grid-cols-[150px_170px_minmax(0,1fr)_auto] items-end")}>
        <label className="grid gap-1">
          <span className="text-[12px] font-semibold text-[var(--text-secondary)]">{t.as_platform}</span>
          <Select value={platform} onChange={(e) => setPlatform(e.target.value)} disabled={loading}>
            {platforms.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="grid gap-1">
          <span className="text-[12px] font-semibold text-[var(--text-secondary)]">{t.as_tone}</span>
          <Select value={tone} onChange={(e) => setTone(e.target.value)} disabled={loading}>
            <option value="">{t.as_tone_auto}</option>
            {(lists?.tones ?? []).map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="grid gap-1">
          <span className="text-[12px] font-semibold text-[var(--text-secondary)]">{t.as_gen_extra}</span>
          <TextField
            value={extra}
            onChange={(e) => setExtra(e.target.value)}
            placeholder={t.as_gen_extra_ph}
            disabled={loading}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !loading) void generate();
            }}
          />
        </label>
        <Button
          variant="primary"
          onClick={generate}
          loading={loading}
          leadingIcon={result ? <RefreshCw size={15} /> : <Wand2 size={15} />}
          block={compact}
        >
          {loading ? t.as_gen_running : result ? t.as_gen_again : t.as_gen_button}
        </Button>
      </div>

      {err ? (
        <InlineNotice
          tone="danger"
          title={err.title}
          // Narrow panels (editor AI tab): stack the action under the text instead of beside it.
          action={compact ? undefined : errAction}
        >
          {err.body}
          {compact && errAction ? <div className="mt-2">{errAction}</div> : null}
        </InlineNotice>
      ) : null}

      {loading && !content ? (
        <div className={clsx("grid gap-3", !compact && "grid-cols-2")} aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] p-3.5">
              <Skeleton className="mb-3 h-3 w-24" />
              <SkeletonText lines={3} />
            </div>
          ))}
          <p className="col-span-full text-center text-xs text-[var(--text-tertiary)]">{t.as_gen_wait}</p>
        </div>
      ) : null}

      {!content && !loading && !err ? (
        <div className="flex items-center gap-3 rounded-[var(--radius-panel-sm)] border border-dashed border-[var(--border-strong)] bg-[var(--surface-muted)] px-4 py-3.5 text-[13px] text-[var(--text-secondary)]">
          <Sparkles size={16} className="shrink-0 text-[var(--primary)]" />
          {t.as_gen_hint}
        </div>
      ) : null}

      {content ? (
        <div className={clsx("grid gap-3", loading && "opacity-60")}>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="primary" size="sm">
              {result?.model}
            </Badge>
            {result?.transcript_used ? (
              <Badge tone="success" size="sm" dot>
                {result.transcript_truncated ? t.as_gen_transcript_truncated : t.as_gen_transcript_used}
              </Badge>
            ) : (
              <Badge tone="warning" size="sm" dot>
                {t.as_gen_no_transcript}
              </Badge>
            )}
          </div>
          <div className={clsx("grid gap-3", !compact && "grid-cols-2")}>
            <Section title={t.as_gen_title} copyText={content.title} {...copy}>
              <p className="font-semibold">{content.title || "—"}</p>
              {content.alt_titles.length ? (
                <ul className="mt-2 grid gap-1 border-t border-[var(--border-subtle)] pt-2">
                  {content.alt_titles.map((alt) => (
                    <li key={alt} className="flex items-start justify-between gap-2 text-[13px] text-[var(--text-secondary)]">
                      <span>{alt}</span>
                      <CopyButton text={alt} label={t.as_copy} copiedLabel={t.as_copied} size="xs" iconOnly />
                    </li>
                  ))}
                </ul>
              ) : null}
            </Section>
            <Section title={t.as_gen_hashtags} copyText={[...content.hashtags].join(" ")} {...copy}>
              <div className="flex flex-wrap gap-1.5">
                {content.hashtags.length
                  ? content.hashtags.map((tag) => (
                      <Badge key={tag} tone="primary">
                        {tag}
                      </Badge>
                    ))
                  : "—"}
              </div>
              {content.tags.length ? (
                <div className="mt-3 border-t border-[var(--border-subtle)] pt-2">
                  <div className="mb-1.5 flex items-center justify-between">
                    <span className="text-[12px] font-semibold text-[var(--text-tertiary)]">{t.as_gen_tags}</span>
                    <CopyButton text={content.tags.join(", ")} label={t.as_copy} copiedLabel={t.as_copied} size="xs" iconOnly />
                  </div>
                  <p className="text-[12.5px] text-[var(--text-secondary)]">{content.tags.join(", ")}</p>
                </div>
              ) : null}
            </Section>
          </div>
          <Section title={t.as_gen_description} copyText={content.description} {...copy}>
            <p className="whitespace-pre-wrap">{content.description || "—"}</p>
          </Section>
          <div className={clsx("grid gap-3", !compact && "grid-cols-2")}>
            <Section title={t.as_gen_tiktok} copyText={content.tiktok_caption} {...copy}>
              <p className="whitespace-pre-wrap">{content.tiktok_caption || "—"}</p>
            </Section>
            <Section title={t.as_gen_facebook} copyText={content.facebook_post} {...copy}>
              <p className="whitespace-pre-wrap">{content.facebook_post || "—"}</p>
            </Section>
          </div>
          {content.summary ? (
            <p className="text-[12.5px] leading-5 text-[var(--text-tertiary)]">
              <span className="font-semibold">{t.as_gen_summary}: </span>
              {content.summary}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
