// Dubbing-job helpers shared by the Dashboard studio strip and the Channels job picker.
import clsx from "clsx";
import { Film } from "lucide-react";
import { useState } from "react";
import { Badge } from "../ui";
import type { BadgeTone } from "../ui";
import type { Job } from "../../types";
import type { T } from "../../i18n";

export const ACTIVE_JOB_STATUSES = new Set(["queued", "downloading", "transcribing", "translating", "tts_generating", "rendering"]);

export function jobTitle(job: Job): string {
  return job.source_title || job.video_url || job.id.slice(0, 8);
}

function youtubeThumb(url: string): string | null {
  const m = url.match(/(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  return m ? `https://img.youtube.com/vi/${m[1]}/mqdefault.jpg` : null;
}

export function JobThumb({ job, className }: { job: Job; className?: string }) {
  const src = job.thumbnail_url || (job.video_url ? youtubeThumb(job.video_url) : null);
  const [failed, setFailed] = useState(false);
  return (
    <div className={clsx("relative aspect-video overflow-hidden bg-[var(--navy-900)]", className)}>
      {src && !failed ? (
        <img src={src} alt="" className="h-full w-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-[radial-gradient(circle_at_50%_30%,var(--navy-700),var(--navy-950)_80%)]">
          <Film size={20} className="text-white/35" />
        </div>
      )}
    </div>
  );
}

export function jobStatusInfo(t: T, status: string): { tone: BadgeTone; label: string } {
  if (status === "ready") return { tone: "success", label: t.dsh_job_ready };
  if (status === "failed") return { tone: "danger", label: t.dsh_job_failed };
  if (status === "cancelled") return { tone: "neutral", label: t.dsh_job_cancelled };
  if (ACTIVE_JOB_STATUSES.has(status)) return { tone: "primary", label: status === "queued" ? t.dsh_job_queued : t.dsh_job_running };
  return { tone: "neutral", label: t.dsh_job_draft };
}

export function JobStatusBadge({ t, status }: { t: T; status: string }) {
  const info = jobStatusInfo(t, status);
  return (
    <Badge tone={info.tone} dot size="sm">
      {info.label}
    </Badge>
  );
}
