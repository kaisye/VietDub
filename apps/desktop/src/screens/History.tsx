import { FolderKanban, Film, FolderOpen, Grid3X3, List, Pencil, Trash2 } from "lucide-react";
import { Page, PageHeader } from "../components/layout/PageHeader";
import { Card } from "../components/ui/card";
import { EmptyState, InlineNotice, Skeleton } from "../components/ui/feedback";
import { Tabs } from "../components/ui/navigation";
import { Tooltip } from "../components/ui/tooltip";
import { useEffect, useRef, useState } from "react";
import { deleteJob, listJobs, outputUrl, revealJob } from "../api";
import { JobStatusBadge } from "../components/channels/jobs";
import { ReviewBadge, useReviewSummaries } from "../components/review/ReviewBadge";
import type { Job } from "../types";
import { useT } from "../i18n";

const NO_IDS: string[] = [];

function timeAgo(iso: string, isVI: boolean): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return isVI ? "vừa xong" : "just now";
  if (diff < 3600) {
    const m = Math.floor(diff / 60);
    return isVI ? `${m} phút trước` : `${m}m ago`;
  }
  if (diff < 86400) {
    const h = Math.floor(diff / 3600);
    return isVI ? `${h} giờ trước` : `${h}h ago`;
  }
  const d = Math.floor(diff / 86400);
  return isVI ? `${d} ngày trước` : `${d}d ago`;
}

function extractYouTubeId(url: string): string | null {
  const m = url.match(
    /(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/,
  );
  return m ? m[1] : null;
}

function VideoThumbnail({ job }: { job: Job }) {
  const [src, setSrc] = useState<string | null>(job.thumbnail_url ?? null);
  const [failed, setFailed] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    if (src || !job.video_url) return;

    const ytId = extractYouTubeId(job.video_url);
    if (ytId) {
      setSrc(`https://img.youtube.com/vi/${ytId}/mqdefault.jpg`);
      return;
    }

    // Canvas frame capture for non-YouTube sources. Page URLs (Douyin, TikTok…) are not
    // playable media, so grab the frame from the rendered output instead; fall back to the
    // source URL only when it points at a media file.
    const mediaSrc =
      outputUrl(job.output_url) ||
      (/\.(mp4|webm|mov|m4v|mkv)(\?|$)/i.test(job.video_url) ? job.video_url : null);
    if (!mediaSrc) {
      setFailed(true);
      return;
    }
    let cancelled = false;
    const video = document.createElement("video");
    videoRef.current = video;
    video.crossOrigin = "anonymous";
    video.preload = "metadata";
    video.muted = true;
    video.playsInline = true;

    video.addEventListener("loadedmetadata", () => {
      video.currentTime = Math.min(1, video.duration * 0.1 || 1);
    });

    video.addEventListener("seeked", () => {
      if (cancelled) return;
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth || 320;
      canvas.height = video.videoHeight || 180;
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        setSrc(canvas.toDataURL("image/jpeg", 0.8));
      }
      video.src = "";
    });

    video.addEventListener("error", () => {
      if (!cancelled) setFailed(true);
    });

    video.src = mediaSrc;

    return () => {
      cancelled = true;
      video.src = "";
      videoRef.current = null;
    };
  }, [job.video_url, job.output_url, src]);

  if (src && !failed) {
    return (
      <img
        src={src}
        alt=""
        className="block aspect-video w-full bg-black object-cover transition duration-300 group-hover:scale-[1.03]"
        onError={() => setFailed(true)}
      />
    );
  }

  return (
    <div className="flex aspect-video w-full items-center justify-center bg-[radial-gradient(circle_at_50%_30%,var(--navy-700),var(--navy-950)_80%)]">
      <Film size={22} className="text-white/35" />
    </div>
  );
}

export default function HistoryScreen({ onOpen, onEdit }: { onOpen: (jobId: string) => void; onEdit: (job: Job) => void }) {
  const { t, lang } = useT();
  const isVI = lang === "vi";
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState("");
  const [viewMode, setViewMode] = useState<"list" | "grid">("grid");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [revealing, setRevealing] = useState<string | null>(null);
  const [revealError, setRevealError] = useState("");

  const reviewSummaries = useReviewSummaries(jobs?.filter((job) => job.status === "ready").map((job) => job.id) ?? NO_IDS);

  useEffect(() => {
    listJobs()
      .then(setJobs)
      .catch((e) => setError(e instanceof Error ? e.message : t.history_load_error));
  }, [t.history_load_error]);

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (!window.confirm(t.history_delete_confirm)) return;

    setDeleting(id);
    try {
      await deleteJob(id);
      setJobs((prev) => prev?.filter((j) => j.id !== id) ?? null);
    } catch {
      // ignore — job stays in list
    } finally {
      setDeleting(null);
    }
  }

  async function handleReveal(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    setRevealError("");
    setRevealing(id);
    try {
      await revealJob(id);
    } catch (err) {
      setRevealError(err instanceof Error ? err.message : isVI ? "Không thể mở thư mục." : "Could not open folder.");
    } finally {
      setRevealing(null);
    }
  }

  const iconBtn =
    "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--text-tertiary)] transition hover:bg-[var(--surface-hover)] disabled:opacity-40";

  return (
    <Page>
      <PageHeader
        icon={<FolderKanban size={20} />}
        title={t.history_title}
        description={t.history_sub}
        actions={
          <Tabs
            variant="pills"
            value={viewMode}
            onChange={(id) => setViewMode(id as "list" | "grid")}
            items={[
              { id: "grid", label: <Grid3X3 size={15} aria-label="Grid" /> },
              { id: "list", label: <List size={15} aria-label="List" /> },
            ]}
          />
        }
      />

      {error ? <InlineNotice tone="danger" title={error} className="mb-4" /> : null}
      {revealError ? <InlineNotice tone="danger" title={revealError} className="mb-4" /> : null}
      {jobs && jobs.length === 0 ? (
        <Card padding="lg">
          <EmptyState icon={<FolderKanban size={22} />} title={t.history_empty} />
        </Card>
      ) : null}
      {!jobs && !error ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4" aria-label={t.loading}>
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)]">
              <Skeleton className="aspect-video w-full" rounded="sm" />
              <div className="grid gap-2 p-3">
                <Skeleton className="h-3.5 w-4/5" />
                <Skeleton className="h-3 w-1/3" />
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {viewMode === "list" ? (
        jobs && jobs.length ? (
          <div className="overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-panel)]">
            {jobs.map((job) => (
              <div
                key={job.id}
                className="group flex items-center gap-3 border-b border-[var(--border-subtle)] px-4 py-2.5 transition-colors last:border-b-0 hover:bg-[var(--surface-hover)]"
              >
                <button
                  className="flex min-w-0 flex-1 items-center gap-4 bg-transparent p-0 text-left"
                  onClick={() => onOpen(job.id)}
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--primary-soft)] text-[var(--primary)]">
                    <Film size={16} />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-[var(--text-primary)]">
                    {job.source_title || job.video_url}
                  </span>
                  {job.created_at && (
                    <span className="w-28 shrink-0 text-right text-xs text-[var(--text-tertiary)]">{timeAgo(job.created_at, isVI)}</span>
                  )}
                  <span className="w-10 shrink-0 text-center font-mono text-xs text-[var(--text-secondary)]">{job.target_language}</span>
                  <span className="w-28 shrink-0">
                    <JobStatusBadge t={t} status={job.status} />
                  </span>
                </button>
                <ReviewBadge summary={reviewSummaries[job.id]} onClick={() => onOpen(job.id)} />
                <Tooltip content={t.history_edit}>
                  <button
                    onClick={(e) => { e.stopPropagation(); onEdit(job); }}
                    className={`${iconBtn} hover:text-[var(--primary)]`}
                    aria-label={t.history_edit}
                  >
                    <Pencil size={14} />
                  </button>
                </Tooltip>
                <button
                  disabled={deleting === job.id}
                  onClick={(e) => void handleDelete(e, job.id)}
                  className={`${iconBtn} hover:text-[var(--danger)]`}
                  aria-label={t.delete}
                  title={t.delete}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        ) : null
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
          {jobs?.map((job) => (
            <div
              key={job.id}
              className="group flex flex-col overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-panel)] transition hover:-translate-y-px hover:border-[color-mix(in_srgb,var(--primary)_45%,var(--border))] hover:shadow-[var(--shadow-float)]"
            >
              <button onClick={() => onOpen(job.id)} className="block bg-transparent p-0 text-left">
                <div className="relative overflow-hidden">
                  <VideoThumbnail job={job} />
                  <span className="absolute left-2 top-2 rounded-md bg-black/55 px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-white backdrop-blur-sm">
                    {job.target_language}
                  </span>
                </div>
                <div className="px-3.5 pb-1 pt-3">
                  <span className="block truncate text-[13.5px] font-semibold text-[var(--text-primary)]">
                    {job.source_title || job.video_url}
                  </span>
                  <div className="mt-1.5 flex items-center justify-between gap-2">
                    <JobStatusBadge t={t} status={job.status} />
                    {job.created_at && (
                      <span className="text-[11.5px] text-[var(--text-tertiary)]">{timeAgo(job.created_at, isVI)}</span>
                    )}
                  </div>
                </div>
              </button>
              {reviewSummaries[job.id] && reviewSummaries[job.id].status !== "none" ? (
                <div className="px-3.5 pt-1.5">
                  <ReviewBadge summary={reviewSummaries[job.id]} onClick={() => onOpen(job.id)} />
                </div>
              ) : null}
              <div className="mt-auto flex items-center justify-between gap-2 px-3.5 pb-3 pt-2">
                {job.output_url ? (
                  <button
                    disabled={revealing === job.id}
                    onClick={(e) => void handleReveal(e, job.id)}
                    className="inline-flex h-7 items-center gap-1.5 rounded-md bg-[var(--primary-soft)] px-2.5 text-xs font-semibold text-[var(--primary-hover)] transition hover:bg-[var(--primary-soft-strong)] disabled:opacity-60"
                  >
                    <FolderOpen size={13} />
                    {revealing === job.id ? (isVI ? "Đang mở…" : "Opening…") : t.history_open}
                  </button>
                ) : (
                  <span />
                )}
                <div className="flex gap-0.5">
                  <Tooltip content={t.history_edit}>
                    <button
                      onClick={(e) => { e.stopPropagation(); onEdit(job); }}
                      className={`${iconBtn} h-7 w-7 hover:text-[var(--primary)]`}
                      aria-label={t.history_edit}
                    >
                      <Pencil size={13} />
                    </button>
                  </Tooltip>
                  <button
                    disabled={deleting === job.id}
                    onClick={(e) => void handleDelete(e, job.id)}
                    className={`${iconBtn} h-7 w-7 hover:text-[var(--danger)]`}
                    aria-label={t.delete}
                    title={t.delete}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Page>
  );
}
