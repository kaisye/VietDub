import clsx from "clsx";
import { Check, Film } from "lucide-react";
import { useEffect, useState } from "react";
import { tt } from "../../i18n";
import type { T } from "../../i18n";
import { publishPost, videoCandidates } from "../../lib/calendar-api";
import type { CalendarPost, VideoCandidate } from "../../lib/calendar-api";
import { PlatformIcon, fmtCompact, fmtDateTime } from "../channels/platform";
import { Button, Field, InlineNotice, Modal, Skeleton, TextField } from "../ui";

/** Mark a post published: optional public URL and/or the synced channel video it became. */
export function PublishModal({
  open,
  post,
  t,
  lang,
  onClose,
  onPublished,
}: {
  open: boolean;
  post: CalendarPost;
  t: T;
  lang: string;
  onClose: () => void;
  onPublished: (post: CalendarPost) => void;
}) {
  const [url, setUrl] = useState("");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<VideoCandidate[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setUrl(post.published_url || "");
    setVideoId(null);
    setError("");
    setCandidates(null);
    let cancelled = false;
    videoCandidates(post.id)
      .then((list) => {
        if (cancelled) return;
        setCandidates(list);
        // Pre-select an obvious match (same title).
        if (list[0] && list[0].score >= 0.95) setVideoId(list[0].id);
      })
      .catch(() => !cancelled && setCandidates([]));
    return () => {
      cancelled = true;
    };
  }, [open, post]);

  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      onPublished(await publishPost(post.id, { published_url: url.trim(), channel_video_id: videoId }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={t.cal_publish_title}
      description={tt(t.cal_publish_desc, { title: post.title })}
      footer={
        <>
          <Button onClick={onClose}>{t.cal_cancel}</Button>
          <Button variant="primary" loading={saving} onClick={() => void submit()}>
            {t.cal_mark_published}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label={t.cal_publish_url} help={t.cal_publish_url_help}>
          <TextField autoFocus type="url" value={url} placeholder="https://" onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <div className="grid gap-1.5">
          <span className="text-[13px] font-semibold text-[var(--text-primary)]">{t.cal_publish_video}</span>
          <span className="text-xs text-[var(--text-secondary)]">{t.cal_publish_video_help}</span>
          <div className="mt-1 grid max-h-[260px] gap-1 overflow-y-auto">
            {candidates === null ? (
              [0, 1].map((i) => <Skeleton key={i} className="h-12" />)
            ) : candidates.length ? (
              candidates.map((video) => {
                const selected = video.id === videoId;
                return (
                  <button
                    key={video.id}
                    type="button"
                    onClick={() => setVideoId(selected ? null : video.id)}
                    className={clsx(
                      "flex items-center gap-2.5 rounded-[var(--radius-control)] border px-2.5 py-2 text-left transition",
                      selected
                        ? "border-[var(--primary)] bg-[var(--primary-soft)]"
                        : "border-[var(--border)] hover:border-[var(--primary-soft-strong)] hover:bg-[var(--surface-hover)]",
                    )}
                  >
                    <span className="flex h-9 w-14 shrink-0 items-center justify-center overflow-hidden rounded-md bg-[var(--navy-900)] text-white/70">
                      {video.thumbnail_url ? <img src={video.thumbnail_url} alt="" className="h-full w-full object-cover" /> : <Film size={15} />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium text-[var(--text-primary)]">{video.title || "—"}</span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-[var(--text-tertiary)]">
                        <PlatformIcon platform={video.platform} size={11} />
                        <span className="truncate">
                          {video.channel_name} · {fmtDateTime(video.published_at, lang)} · {fmtCompact(video.views, lang)} {t.cal_views}
                        </span>
                      </span>
                    </span>
                    {selected ? <Check size={16} className="shrink-0 text-[var(--primary)]" /> : null}
                  </button>
                );
              })
            ) : (
              <p className="rounded-[var(--radius-control)] border border-dashed border-[var(--border)] px-3 py-4 text-center text-[12.5px] text-[var(--text-tertiary)]">
                {t.cal_publish_no_videos}
              </p>
            )}
          </div>
        </div>
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      </div>
    </Modal>
  );
}
