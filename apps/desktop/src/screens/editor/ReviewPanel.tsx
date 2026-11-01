import clsx from "clsx";
import { ArrowRight, Check, Crosshair, Pause, Play, RotateCcw, ShieldCheck, Sparkles, Wand2, X } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, InlineNotice, ProgressBar } from "../../components/ui";
import { tt, useT, type T } from "../../i18n";
import {
  isSafeProposal,
  previewAutoReview,
  storageUrl,
  wordDiff,
  type CueSnapshot,
  type ReviewIssue,
  type ReviewIssueType,
  type ReviewMode,
  type ReviewProposal,
  type ReviewSeverity,
} from "../../lib/auto-review";
import type { AutoReviewController } from "./useAutoReview";
import { formatTime } from "./utils";

const SEVERITY_DOT: Record<ReviewSeverity, string> = {
  error: "bg-[var(--danger)]",
  warning: "bg-[var(--warning)]",
  info: "bg-[var(--text-tertiary)]",
};
const PAGE = 30;

const issueLabel = (t: T, type: ReviewIssueType) => (t as Record<string, string>)[`ar_issue_${type}`] ?? type;
const kindLabel = (t: T, proposal: ReviewProposal) =>
  proposal.kind === "rewrite" && proposal.rewrite?.mode === "expand"
    ? t.ar_kind_expand
    : ((t as Record<string, string>)[`ar_kind_${proposal.kind}`] ?? proposal.kind);

/** "Rà soát" tab: run the auto review, inspect issues, accept / dismiss proposed fixes. */
export function ReviewPanel({
  jobId,
  review: controller,
  onJump,
  onFocus,
}: {
  jobId: string;
  review: AutoReviewController;
  /** Select the cue (or seek to `time` when the cue no longer exists). */
  onJump: (cueId: string, time: number) => void;
  /** Highlight a time range on the timeline (null clears it). */
  onFocus: (range: { start: number; end: number } | null) => void;
}) {
  const { t } = useT();
  const { review, busy, error } = controller;
  const [mode, setMode] = useState<ReviewMode>("propose");
  const [filter, setFilter] = useState<ReviewIssueType | "all">("all");
  const [shown, setShown] = useState(PAGE);
  const audio = usePreviewAudio();

  useEffect(() => {
    if (review?.mode) setMode(review.mode);
  }, [review?.mode]);

  const proposals = review?.proposals ?? [];
  const visible = useMemo(
    () => proposals.filter((proposal) => filter === "all" || proposal.issue_types.includes(filter)),
    [proposals, filter],
  );
  const pending = visible.filter((proposal) => proposal.status === "pending");
  const handled = visible.filter((proposal) => proposal.status !== "pending");
  const safeCount = proposals.filter((proposal) => proposal.status === "pending" && isSafeProposal(proposal)).length;
  const allPending = proposals.filter((proposal) => proposal.status === "pending").length;
  const manual = useMemo(() => manualIssues(review?.issues ?? [], proposals, filter), [review?.issues, proposals, filter]);

  const before = review?.summary_before;
  const current = review?.summary_current ?? before;
  const predicted = review?.summary_predicted;
  const running = controller.running;
  const applying = controller.applying;
  const idle = !review || review.status === "idle";
  const typeCounts = Object.entries(current?.by_type ?? {}).sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0)) as [ReviewIssueType, number][];
  const severityOf = (type: ReviewIssueType): ReviewSeverity => {
    const items = (review?.issues ?? []).filter((issue) => issue.type === type);
    return items.some((issue) => issue.severity === "error") ? "error" : items.some((issue) => issue.severity === "warning") ? "warning" : "info";
  };

  const modes: { id: ReviewMode; label: string; hint: string }[] = [
    { id: "report", label: t.ar_mode_report, hint: t.ar_mode_report_hint },
    { id: "propose", label: t.ar_mode_propose, hint: t.ar_mode_propose_hint },
    { id: "safe", label: t.ar_mode_safe, hint: t.ar_mode_safe_hint },
  ];

  return (
    <div className="flex min-h-full min-w-0 flex-col overflow-x-hidden">
      {/* Run controls */}
      <div className="grid gap-2.5 border-b border-[var(--border-subtle)] p-3">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-control)] bg-[var(--primary-soft)] text-[var(--primary)]">
            <ShieldCheck size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-semibold text-[var(--text-primary)]">{t.ar_title}</div>
          </div>
        </div>
        {idle ? <p className="text-[11.5px] leading-[1.5] text-[var(--text-secondary)]">{t.ar_intro}</p> : null}
        <div role="radiogroup" aria-label={t.ar_title} className="grid grid-cols-3 gap-1 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-sunken)] p-1">
          {modes.map((item) => (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={mode === item.id}
              title={item.hint}
              onClick={() => setMode(item.id)}
              className={clsx(
                "min-h-7 rounded-md px-1 text-[11.5px] font-semibold leading-tight transition",
                mode === item.id
                  ? "bg-[var(--surface)] text-[var(--primary-hover)] shadow-[var(--shadow-sm)]"
                  : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
        <p className="text-[11px] leading-[1.45] text-[var(--text-tertiary)]">{modes.find((item) => item.id === mode)?.hint}</p>
        <Button
          variant="primary"
          size="sm"
          block
          loading={busy === "start" || running}
          disabled={running || applying || !!busy}
          leadingIcon={<Sparkles size={14} />}
          onClick={() => void controller.start(mode)}
        >
          {idle ? t.ar_run : t.ar_rerun}
        </Button>
        {running ? (
          <div className="grid gap-1.5">
            <ProgressBar
              value={review?.progress?.total ? review.progress.done : 0}
              max={review?.progress?.total || 1}
              size="sm"
            />
            <span className="text-[11.5px] text-[var(--text-secondary)]">
              {(t as Record<string, string>)[`ar_phase_${review?.phase}`] ?? t.ar_phase_plan}
            </span>
          </div>
        ) : null}
        {applying ? (
          <div className="grid gap-1.5">
            <ProgressBar value={review?.applying?.done ?? 0} max={review?.applying?.total || 1} size="sm" tone="success" />
            <span className="text-[11.5px] text-[var(--text-secondary)]">
              {tt(t.ar_applying, { done: review?.applying?.done ?? 0, total: review?.applying?.total ?? 0 })}
            </span>
          </div>
        ) : null}
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
        {review?.status === "failed" && review.error ? (
          <InlineNotice tone="danger" title={t.ar_failed}>
            {review.error}
          </InlineNotice>
        ) : null}
        {review?.applying?.status === "failed" && review.applying.error ? (
          <InlineNotice tone="danger" title={t.ar_apply_failed}>
            {review.applying.error}
          </InlineNotice>
        ) : null}
      </div>

      {idle ? (
        <p className="p-4 text-center text-[12.5px] text-[var(--text-tertiary)]">{t.ar_empty_title}</p>
      ) : null}

      {/* Score + issue groups */}
      {current ? (
        <div className="grid gap-3 border-b border-[var(--border-subtle)] p-3">
          <div className="grid grid-cols-3 gap-1.5">
            <Score label={t.ar_score_start} value={before?.score} />
            <Score label={t.ar_score_now} value={current.score} tone={scoreTone(current.score)} />
            <Score label={t.ar_score_predicted} value={predicted?.score} muted />
          </div>
          <div className="text-[11.5px] text-[var(--text-secondary)]">
            {tt(t.ar_cues_count, { n: current.cues_with_issues, total: current.cues })} · {tt(t.ar_issues_count, { n: current.issues })}
          </div>
          {typeCounts.length ? (
            <div className="flex flex-wrap gap-1.5">
              <Chip active={filter === "all"} onClick={() => setFilter("all")}>
                {t.ar_filter_all}
              </Chip>
              {typeCounts.map(([type, count]) => (
                <Chip key={type} active={filter === type} onClick={() => setFilter(filter === type ? "all" : type)}>
                  <span className={clsx("h-1.5 w-1.5 rounded-full", SEVERITY_DOT[severityOf(type)])} />
                  {issueLabel(t, type)}
                  <span className="tabular-nums text-[var(--text-tertiary)]">{count}</span>
                </Chip>
              ))}
            </div>
          ) : review?.status === "done" ? (
            <InlineNotice tone="success">{t.ar_clean}</InlineNotice>
          ) : null}
          {review?.llm && review.llm.available === false && review.mode !== "report" ? (
            <InlineNotice tone="info">{t.ar_no_llm}</InlineNotice>
          ) : null}
        </div>
      ) : null}

      {/* Bulk actions */}
      {allPending ? (
        <div className="grid gap-1.5 border-b border-[var(--border-subtle)] p-3">
          <Button
            size="sm"
            variant="outline"
            block
            leadingIcon={<ShieldCheck size={14} />}
            disabled={!safeCount || applying || running || !!busy}
            loading={busy === "apply:safe"}
            onClick={() => void controller.apply("safe")}
          >
            {tt(t.ar_accept_safe, { n: safeCount })}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            block
            disabled={applying || running || !!busy}
            loading={busy === "apply:all"}
            onClick={() => void controller.apply("all")}
          >
            {tt(t.ar_accept_all, { n: allPending })}
          </Button>
        </div>
      ) : null}

      {/* Proposals */}
      {pending.length ? (
        <Section title={`${t.ar_pending} (${pending.length})`}>
          <div className="grid gap-2">
            {pending.slice(0, shown).map((proposal) => (
              <ProposalCard
                key={proposal.id}
                jobId={jobId}
                proposal={proposal}
                busy={!!busy || applying || running}
                audio={audio}
                onAccept={() => void controller.apply("selected", [proposal.id])}
                onReject={() => void controller.reject([proposal.id])}
                onJump={onJump}
                onFocus={onFocus}
              />
            ))}
            {pending.length > shown ? (
              <Button size="xs" variant="ghost" onClick={() => setShown((value) => value + PAGE)}>
                {tt(t.ar_show_more, { n: Math.min(PAGE, pending.length - shown) })}
              </Button>
            ) : null}
          </div>
        </Section>
      ) : null}

      {manual.length ? (
        <Section title={`${t.ar_manual} (${manual.length})`} hint={t.ar_manual_hint}>
          <div className="grid">
            {manual.slice(0, 60).map((issue) => (
              <button
                key={`${issue.cue_id}-${issue.type}`}
                type="button"
                onClick={() => onJump(issue.cue_id, issue.start)}
                onMouseEnter={() => onFocus({ start: issue.start, end: issue.end })}
                onMouseLeave={() => onFocus(null)}
                className="flex items-center gap-2 rounded-[var(--radius-sm)] px-1.5 py-1 text-left text-[12px] hover:bg-[var(--surface-hover)]"
              >
                <span className={clsx("h-2 w-2 shrink-0 rounded-full", SEVERITY_DOT[issue.severity])} />
                <span className="w-12 shrink-0 font-mono text-[10.5px] text-[var(--text-tertiary)]">{formatTime(issue.start, false)}</span>
                <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">{issueLabel(t, issue.type)}</span>
                <span className="shrink-0 font-mono text-[10.5px] text-[var(--text-tertiary)]">{issueValue(issue)}</span>
              </button>
            ))}
          </div>
        </Section>
      ) : null}

      {handled.length ? (
        <Section title={`${t.ar_done_section} (${handled.length})`}>
          <div className="grid">
            {handled.slice(0, 80).map((proposal) => (
              <button
                key={proposal.id}
                type="button"
                onClick={() => onJump(proposal.cue_ids[0], proposal.before[0]?.start ?? 0)}
                className="flex items-center gap-2 rounded-[var(--radius-sm)] px-1.5 py-1 text-left text-[12px] hover:bg-[var(--surface-hover)]"
                title={proposal.error}
              >
                <StatusIcon status={proposal.status} />
                <span className="w-12 shrink-0 font-mono text-[10.5px] text-[var(--text-tertiary)]">
                  {formatTime(proposal.before[0]?.start ?? 0, false)}
                </span>
                <span className="min-w-0 flex-1 truncate">{kindLabel(t, proposal)}</span>
                <span className="shrink-0 text-[10.5px] text-[var(--text-tertiary)]">
                  {(t as Record<string, string>)[`ar_status_${proposal.status}`]}
                </span>
              </button>
            ))}
          </div>
        </Section>
      ) : null}

      {review?.batches?.length ? (
        <Section title={t.ar_batches}>
          <div className="grid gap-1.5">
            {[...review.batches].reverse().map((batch) => (
              <div key={batch.id} className="flex items-center gap-2 rounded-[var(--radius-control)] border border-[var(--border-subtle)] px-2.5 py-2 text-[12px]">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-[var(--text-primary)]">{batch.label}</div>
                  <div className="text-[11px] text-[var(--text-tertiary)]">
                    {tt(t.ar_batch_line, { applied: batch.counts.applied, before: batch.score_before, after: batch.score_after })}
                  </div>
                </div>
                {batch.reverted ? (
                  <Badge size="sm">{t.ar_reverted}</Badge>
                ) : (
                  <Button
                    size="xs"
                    variant="ghost"
                    leadingIcon={<RotateCcw size={12} />}
                    disabled={applying || running || !!busy}
                    loading={busy === `revert:${batch.id}`}
                    onClick={() => void controller.revert(batch.id)}
                  >
                    {t.ar_revert}
                  </Button>
                )}
              </div>
            ))}
            {review.batches.some((batch) => !batch.reverted) ? (
              <p className="text-[11px] leading-[1.45] text-[var(--text-tertiary)]">{t.ar_render_hint}</p>
            ) : null}
          </div>
        </Section>
      ) : null}
    </div>
  );
}

function manualIssues(issues: ReviewIssue[], proposals: ReviewProposal[], filter: ReviewIssueType | "all"): ReviewIssue[] {
  const covered = new Set<string>();
  for (const proposal of proposals) {
    if (proposal.status === "pending" || proposal.status === "applied") {
      for (const id of proposal.cue_ids) for (const type of proposal.issue_types) covered.add(`${id}:${type}`);
    }
  }
  return issues.filter(
    (issue) => issue.severity !== "info" && (filter === "all" || issue.type === filter) && !covered.has(`${issue.cue_id}:${issue.type}`),
  );
}

function issueValue(issue: ReviewIssue): string {
  const v = issue.values;
  if (typeof v.speed === "number") return `${v.speed.toFixed(2)}×`;
  if (typeof v.overlap === "number") return `+${v.overlap.toFixed(2)}s`;
  if (typeof v.cps === "number") return `${Math.round(v.cps)} cps`;
  if (typeof v.duration === "number") return `${v.duration.toFixed(1)}s`;
  if (typeof v.at === "string") return v.at;
  return "";
}

const scoreTone = (score: number) => (score >= 85 ? "good" : score >= 70 ? "ok" : "bad");

function Score({ label, value, tone, muted }: { label: string; value?: number | null; tone?: "good" | "ok" | "bad"; muted?: boolean }) {
  return (
    <div className="rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-2 py-1.5">
      <div className="truncate text-[10.5px] text-[var(--text-secondary)]" title={label}>
        {label}
      </div>
      <div
        className={clsx(
          "text-[17px] font-semibold tabular-nums leading-6",
          muted
            ? "text-[var(--text-secondary)]"
            : tone === "good"
              ? "text-[var(--success)]"
              : tone === "bad"
                ? "text-[var(--danger)]"
                : tone === "ok"
                  ? "text-[var(--warning)]"
                  : "text-[var(--text-primary)]",
        )}
      >
        {value ?? "—"}
      </div>
    </div>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-[3px] text-[11px] font-medium transition",
        active
          ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary-hover)]"
          : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]",
      )}
    >
      {children}
    </button>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-[var(--border-subtle)] p-3">
      <h3 className="mb-2 text-[11.5px] font-semibold uppercase tracking-[0.02em] text-[var(--text-secondary)]">{title}</h3>
      {hint ? <p className="-mt-1 mb-2 text-[11px] text-[var(--text-tertiary)]">{hint}</p> : null}
      {children}
    </section>
  );
}

function StatusIcon({ status }: { status: ReviewProposal["status"] }) {
  if (status === "applied") return <Check size={13} className="shrink-0 text-[var(--success)]" />;
  if (status === "rejected") return <X size={13} className="shrink-0 text-[var(--text-tertiary)]" />;
  return <RotateCcw size={12} className="shrink-0 text-[var(--warning)]" />;
}

// ---- proposal card ---------------------------------------------------------------

type PreviewAudio = ReturnType<typeof usePreviewAudio>;

const ProposalCard = memo(function ProposalCard({
  jobId,
  proposal,
  busy,
  audio,
  onAccept,
  onReject,
  onJump,
  onFocus,
}: {
  jobId: string;
  proposal: ReviewProposal;
  busy: boolean;
  audio: PreviewAudio;
  onAccept: () => void;
  onReject: () => void;
  onJump: (cueId: string, time: number) => void;
  onFocus: (range: { start: number; end: number } | null) => void;
}) {
  const { t } = useT();
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [synth, setSynth] = useState(false);
  const range = useMemo(() => spanOf([...proposal.before, ...proposal.after]), [proposal]);
  const target = proposal.before[0];
  const after = proposal.after.find((cue) => cue.id === target?.id) ?? proposal.after[0];
  const textOps = proposal.ops.filter((op) => op.op === "text");
  const speedBefore = proposal.predicted.speed_before ?? target?.speed;
  const speedAfter = proposal.predicted.speed_after ?? after?.speed;
  const playingOld = audio.playing === `${proposal.id}:old`;
  const playingNew = audio.playing === `${proposal.id}:new`;

  const playOld = () => {
    if (playingOld) return audio.stop();
    const url = storageUrl(target?.audio_url);
    if (url) audio.play(`${proposal.id}:old`, url, 1);
  };
  const playNew = async () => {
    if (playingNew) return audio.stop();
    setPreviewError(null);
    if (proposal.kind === "rewrite" || textOps.length) {
      setSynth(true);
      try {
        const result = await previewAutoReview(jobId, proposal.id);
        const url = storageUrl(result.url);
        if (url) audio.play(`${proposal.id}:new`, url, result.speed && result.speed > 1 ? Math.min(2, result.speed) : 1);
      } catch (e) {
        setPreviewError(e instanceof Error ? e.message : String(e));
      } finally {
        setSynth(false);
      }
      return;
    }
    // Timing / speed fixes: the unfitted take played at the new speed is exactly what will be heard.
    const url = storageUrl(target?.source_url ?? target?.audio_url);
    const rate = after?.natural && after.voice_len ? after.natural / after.voice_len : 1;
    if (url) audio.play(`${proposal.id}:new`, url, rate);
  };
  const canListen = !!target?.voiced && !!target?.audio_url;
  const timingOnly = !textOps.length && proposal.kind !== "merge" && proposal.kind !== "delete" && proposal.kind !== "generate_voice";
  const hasSpeed = typeof speedBefore === "number" && typeof speedAfter === "number" && Math.abs(speedBefore - speedAfter) > 0.005;

  return (
    <article
      className="min-w-0 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] p-2.5 shadow-[var(--shadow-xs)] transition hover:border-[color-mix(in_srgb,var(--primary)_35%,var(--border))]"
      onMouseEnter={() => onFocus(range)}
      onMouseLeave={() => onFocus(null)}
    >
      <header className="flex items-start gap-2">
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--primary-soft)] text-[var(--primary)]">
          {proposal.needs_llm ? <Sparkles size={13} /> : <Wand2 size={13} />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-[12.5px] font-semibold text-[var(--text-primary)]">{kindLabel(t, proposal)}</span>
            <Badge size="sm" tone={proposal.risk === "low" ? "success" : "warning"}>
              {proposal.risk === "low" ? t.ar_risk_low : t.ar_risk_medium}
            </Badge>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[10.5px] text-[var(--text-tertiary)]">
            <button
              type="button"
              className="inline-flex items-center gap-1 font-mono hover:text-[var(--primary)]"
              title={t.ar_jump}
              onClick={() => onJump(proposal.cue_ids[0], range.start)}
            >
              <Crosshair size={10} />
              {formatTime(target?.start ?? range.start, false)}
            </button>
            <span>·</span>
            <span className="truncate">{proposal.issue_types.map((type) => issueLabel(t, type)).join(", ")}</span>
            <span>·</span>
            <span>{tt(t.ar_conf, { n: Math.round(proposal.confidence * 100) })}</span>
          </div>
        </div>
      </header>

      {/* Text diff for rewrites / moved words / merges */}
      {textOps.length || proposal.kind === "merge" ? (
        <div className="mt-2 grid gap-1">
          {(textOps.length ? textOps : [{ id: target?.id ?? "", text: after?.text ?? "" }]).map((op) => {
            const old = proposal.before.find((cue) => cue.id === op.id)?.text ?? "";
            return <TextDiff key={op.id} before={proposal.kind === "merge" ? proposal.before.map((cue) => cue.text).join(" / ") : old} after={op.text ?? ""} />;
          })}
          {proposal.predicted.syllables_before && proposal.predicted.syllables_after ? (
            <span className="text-[10.5px] text-[var(--text-tertiary)]">
              {tt(t.ar_syllables, { a: proposal.predicted.syllables_before, b: proposal.predicted.syllables_after })}
            </span>
          ) : null}
        </div>
      ) : proposal.kind === "split" ? (
        <div className="mt-2 grid gap-1 text-[11.5px] leading-[1.45]">
          {proposal.after.map((cue) => (
            <div key={cue.id} className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-2 py-1 text-[var(--text-primary)]">
              {cue.text}
            </div>
          ))}
        </div>
      ) : target ? (
        <p className="mt-2 line-clamp-2 text-[11.5px] leading-[1.45] text-[var(--text-secondary)]">{target.text}</p>
      ) : null}

      {/* Timing bars */}
      {timingOnly || proposal.kind === "split" ? <TimingBars proposal={proposal} range={range} /> : null}

      {hasSpeed ? (
        <div className="mt-2 flex items-center gap-1.5 text-[11.5px]">
          <span className="text-[var(--text-secondary)]">{t.ar_speed}</span>
          <SpeedValue value={speedBefore!} />
          <ArrowRight size={11} className="text-[var(--text-tertiary)]" />
          <SpeedValue value={speedAfter!} />
        </div>
      ) : null}
      {previewError ? <p className="mt-1.5 text-[11px] text-[var(--danger)]">{previewError}</p> : null}

      <footer className="mt-2.5 flex flex-wrap items-center gap-0.5">
        {canListen ? (
          <>
            <Button size="xs" variant="ghost" className="!gap-1 !px-1.5" leadingIcon={playingOld ? <Pause size={12} /> : <Play size={12} />} onClick={playOld}>
              {playingOld ? t.ar_listen_stop : t.ar_listen_before}
            </Button>
            {proposal.kind !== "delete" ? (
              <Button
                size="xs"
                variant="ghost"
                className="!gap-1 !px-1.5"
                loading={synth}
                leadingIcon={playingNew ? <Pause size={12} /> : <Play size={12} />}
                onClick={() => void playNew()}
              >
                {playingNew ? t.ar_listen_stop : t.ar_listen_after}
              </Button>
            ) : null}
          </>
        ) : null}
        <span className="ml-auto" />
        <Button size="xs" variant="ghost" className="!px-2" disabled={busy} onClick={onReject}>
          {t.ar_reject}
        </Button>
        <Button size="xs" variant="primary" disabled={busy} onClick={onAccept}>
          {t.ar_accept}
        </Button>
      </footer>
    </article>
  );
});

function SpeedValue({ value }: { value: number }) {
  return (
    <span
      className={clsx(
        "rounded-[var(--radius-sm)] px-1.5 py-px font-mono text-[11px] font-semibold tabular-nums",
        value > 1.4 ? "bg-[var(--danger-soft)] text-[var(--danger)]" : value > 1.15 ? "bg-[var(--warning-soft)] text-[var(--warning)]" : "bg-[var(--success-soft)] text-[var(--success)]",
      )}
    >
      {value.toFixed(2)}×
    </span>
  );
}

function TextDiff({ before, after }: { before: string; after: string }) {
  const parts = useMemo(() => wordDiff(before, after), [before, after]);
  return (
    <p className="rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-2 py-1.5 text-[11.5px] leading-[1.55] text-[var(--text-primary)]">
      {parts.map((part, index) => (
        <span
          key={index}
          className={clsx(
            part.kind === "del" && "bg-[var(--danger-soft)] text-[var(--danger)] line-through decoration-[1px]",
            part.kind === "add" && "bg-[var(--success-soft)] text-[var(--success)]",
          )}
        >
          {part.text}{" "}
        </span>
      ))}
    </p>
  );
}

function spanOf(cues: CueSnapshot[]): { start: number; end: number } {
  let start = Infinity;
  let end = -Infinity;
  for (const cue of cues) {
    start = Math.min(start, cue.start, cue.voice_start ?? cue.start);
    end = Math.max(end, cue.end, (cue.voice_start ?? cue.start) + (cue.voice_len ?? 0));
  }
  if (!Number.isFinite(start)) return { start: 0, end: 1 };
  return { start: Math.max(0, start - 0.3), end: end + 0.3 };
}

/** Before / after rows: subtitle (blue) and voice (violet) spans of the cues the fix touches. */
function TimingBars({ proposal, range }: { proposal: ReviewProposal; range: { start: number; end: number } }) {
  const { t } = useT();
  const span = Math.max(0.1, range.end - range.start);
  const pos = (value: number) => `${((value - range.start) / span) * 100}%`;
  const width = (from: number, to: number) => `${Math.max(0.6, ((to - from) / span) * 100)}%`;
  const row = (cues: CueSnapshot[]) => (
    <div className="relative h-[22px] rounded-[var(--radius-sm)] bg-[var(--surface-sunken)]">
      {cues.map((cue) => (
        <span key={`s-${cue.id}`} className="absolute top-[3px] h-[7px] rounded-[2px] bg-[var(--primary)] opacity-80" style={{ left: pos(cue.start), width: width(cue.start, cue.end) }} />
      ))}
      {cues.map((cue) =>
        cue.voiced && cue.voice_start !== null && cue.voice_len ? (
          <span
            key={`v-${cue.id}`}
            className="absolute bottom-[3px] h-[7px] rounded-[2px] bg-[var(--accent-violet)] opacity-85"
            style={{ left: pos(cue.voice_start), width: width(cue.voice_start, cue.voice_start + cue.voice_len) }}
          />
        ) : null,
      )}
    </div>
  );
  return (
    <div className="mt-2 grid grid-cols-[34px_1fr] items-center gap-x-1.5 gap-y-1 text-[10px] text-[var(--text-tertiary)]">
      <span>{t.ar_before}</span>
      {row(proposal.before)}
      <span>{t.ar_after}</span>
      {row(proposal.after)}
    </div>
  );
}

/** One shared <audio> for "Nghe thử"; the new speed is applied with playbackRate (pitch preserved). */
function usePreviewAudio() {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  useEffect(() => () => ref.current?.pause(), []);
  const stop = () => {
    ref.current?.pause();
    setPlaying(null);
  };
  const play = (key: string, url: string, rate: number) => {
    ref.current?.pause();
    const element = new Audio(url);
    element.preservesPitch = true;
    element.playbackRate = Math.max(0.5, Math.min(2, rate || 1));
    element.onended = () => setPlaying((current) => (current === key ? null : current));
    ref.current = element;
    setPlaying(key);
    void element.play().catch(() => setPlaying(null));
  };
  return { playing, play, stop };
}
