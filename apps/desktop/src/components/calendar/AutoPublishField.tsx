import { AlertTriangle, CheckCircle2, Clock, ExternalLink, Loader2, Send } from "lucide-react";
import type { ReactNode } from "react";
import { tt } from "../../i18n";
import type { T } from "../../i18n";
import type { CalendarPost } from "../../lib/calendar-api";
import { openUrl } from "../../lib/open-url";
import { fmtDateTime } from "../channels/platform";
import { Switch } from "../ui";

/**
 * "Publish automatically" for a Facebook Page post, with where the post stands
 * (waiting, handed to Facebook, published, failed).
 */
export function AutoPublishField({
  t,
  lang,
  post,
  checked,
  onChange,
}: {
  t: T;
  lang: string;
  /** The saved post (null while creating). */
  post: CalendarPost | null;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  const state = post?.publish_state ?? "";
  const when = post?.scheduled_at ? fmtDateTime(post.scheduled_at, lang) : "";
  const locked = state === "uploading" || state === "published";

  let status: { icon: ReactNode; text: string; tone: string } | null = null;
  if (state === "pending" && checked) {
    status = { icon: <Clock size={14} />, text: tt(t.cal_auto_pending, { when }), tone: "text-[var(--text-secondary)]" };
  } else if (state === "uploading") {
    status = { icon: <Loader2 size={14} className="animate-spin" />, text: t.cal_auto_uploading, tone: "text-[var(--primary)]" };
  } else if (state === "remote_scheduled") {
    status = { icon: <Send size={14} />, text: tt(t.cal_auto_remote, { when }), tone: "text-[var(--primary)]" };
  } else if (state === "published") {
    status = { icon: <CheckCircle2 size={14} />, text: t.cal_auto_published, tone: "text-[var(--success)]" };
  } else if (state === "failed") {
    status = { icon: <AlertTriangle size={14} />, text: post?.publish_error || t.cal_auto_failed, tone: "text-[var(--danger)]" };
  }

  return (
    <div className="grid gap-2 rounded-[var(--radius-panel-sm)] border border-[var(--border)] p-3">
      <Switch
        checked={checked}
        onChange={onChange}
        disabled={locked}
        label={t.cal_auto_label}
        description={t.cal_auto_desc}
      />
      {status ? (
        <div className={`flex items-start gap-1.5 text-[12.5px] leading-[1.45] ${status.tone}`}>
          <span className="mt-0.5 shrink-0">{status.icon}</span>
          <span className="min-w-0 flex-1 break-words">{status.text}</span>
        </div>
      ) : null}
      {state === "failed" && checked ? <p className="text-[12px] text-[var(--text-tertiary)]">{t.cal_auto_retry_hint}</p> : null}
      {state === "published" && post?.published_url ? (
        <button
          type="button"
          onClick={() => void openUrl(post.published_url)}
          className="flex max-w-full items-center gap-1.5 truncate text-[12.5px] font-medium text-[var(--primary)] hover:underline"
        >
          <ExternalLink size={13} className="shrink-0" />
          <span className="truncate">{post.published_url}</span>
        </button>
      ) : null}
    </div>
  );
}
