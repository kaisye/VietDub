import { ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { tt, useT } from "../../i18n";
import { getAutoReviewSummaries, preferReviewTab, type ReviewBadgeSummary } from "../../lib/auto-review";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { InlineNotice, Spinner } from "../ui/feedback";

/** Auto review summaries for a list of jobs (project cards). Refreshes while any review runs. */
export function useReviewSummaries(ids: string[]): Record<string, ReviewBadgeSummary> {
  const key = useMemo(() => [...ids].sort().join(","), [ids]);
  const [data, setData] = useState<Record<string, ReviewBadgeSummary>>({});
  const running = Object.values(data).some((item) => item.status === "running" || item.applying);
  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    const fetchAll = () =>
      getAutoReviewSummaries(key.split(","))
        .then((result) => !cancelled && setData(result))
        .catch(() => undefined); // Badges are optional decoration.
    void fetchAll();
    const timer = running ? window.setInterval(fetchAll, 4000) : null;
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [key, running]);
  return data;
}

/** "12 vấn đề cần xem" / "Đang rà soát…" / "Đạt chuẩn" — nothing when the job was never reviewed. */
export function ReviewBadge({ summary, onClick }: { summary?: ReviewBadgeSummary; onClick?: () => void }) {
  const { t } = useT();
  if (!summary || summary.status === "none" || summary.status === "failed") return null;
  const content =
    summary.status === "running" || summary.applying ? (
      <Badge size="sm" tone="info" dot>
        {t.ar_badge_running}
      </Badge>
    ) : summary.issues ? (
      <Badge size="sm" tone={summary.errors ? "warning" : "neutral"} icon={<ShieldCheck size={11} />} title={t.ar_title}>
        {tt(t.ar_badge_issues, { n: summary.issues })}
      </Badge>
    ) : (
      <Badge size="sm" tone="success" icon={<ShieldCheck size={11} />}>
        {t.ar_badge_clean}
      </Badge>
    );
  if (!onClick) return content;
  return (
    <button
      type="button"
      className="inline-flex rounded-full focus-visible:outline-none focus-visible:shadow-[var(--shadow-focus)]"
      onClick={(event) => {
        event.stopPropagation();
        preferReviewTab();
        onClick();
      }}
    >
      {content}
    </button>
  );
}

/** Result card block on the Progress screen once the dub is ready. */
export function ReviewProgressNotice({ jobId, onOpen }: { jobId: string; onOpen: () => void }) {
  const { t } = useT();
  const [summary, setSummary] = useState<ReviewBadgeSummary | null>(null);
  const [polls, setPolls] = useState(0);
  useEffect(() => {
    let cancelled = false;
    getAutoReviewSummaries([jobId])
      .then((result) => !cancelled && setSummary(result[jobId] ?? null))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [jobId, polls]);
  // The review starts right after the dub: keep checking while it runs (or has not started yet, briefly).
  const waiting = !summary || summary.status === "running" || summary.applying || (summary.status === "none" && polls < 10);
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(() => setPolls((value) => value + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [waiting, polls]);

  if (!summary || summary.status === "none" || summary.status === "failed") return null;
  const open = () => {
    preferReviewTab();
    onOpen();
  };
  if (summary.status === "running" || summary.applying) {
    return (
      <InlineNotice tone="info" title={t.ar_title}>
        <span className="inline-flex items-center gap-2">
          <Spinner />
          {t.ar_progress_running}
        </span>
      </InlineNotice>
    );
  }
  return (
    <InlineNotice
      tone={summary.issues ? "warning" : "success"}
      title={t.ar_title}
      action={
        <Button size="sm" variant="secondary" leadingIcon={<ShieldCheck size={14} />} onClick={open}>
          {t.ar_progress_open}
        </Button>
      }
    >
      {summary.issues
        ? tt(t.ar_progress_body, { issues: summary.issues ?? 0, pending: summary.pending ?? 0, score: summary.score ?? "—" })
        : t.ar_badge_clean}
    </InlineNotice>
  );
}
