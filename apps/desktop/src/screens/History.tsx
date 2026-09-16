import { Grid3X3, List, Pencil, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { deleteJob, listJobs, revealJob } from "../api";
import type { Job } from "../types";
import { useT } from "../i18n";

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

    // Canvas frame capture for non-YouTube sources
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

    video.src = job.video_url;

    return () => {
      cancelled = true;
      video.src = "";
      videoRef.current = null;
    };
  }, [job.video_url, src]);

  const imgStyle: React.CSSProperties = {
    width: "100%",
    height: 120,
    objectFit: "cover",
    background: "#000",
    display: "block",
  };

  if (src && !failed) {
    return (
      <img
        src={src}
        alt=""
        style={imgStyle}
        onError={() => setFailed(true)}
      />
    );
  }

  return (
    <div
      style={{
        width: "100%",
        height: 120,
        background: "var(--surface-muted)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {!failed && !src && (
        <span style={{ fontSize: 18, color: "var(--text-secondary)", opacity: 0.4 }}>▶</span>
      )}
    </div>
  );
}

const ACTIVE = new Set([
  "queued",
  "downloading",
  "transcribing",
  "translating",
  "tts_generating",
  "rendering",
]);

function pillClass(status: string): string {
  if (status === "ready") return "pill ready";
  if (status === "failed" || status === "cancelled") return "pill failed";
  if (ACTIVE.has(status)) return "pill run";
  return "pill idle";
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

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h1>{t.history_title}</h1>
          <p className="sub">{t.history_sub}</p>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => setViewMode("list")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: viewMode === "list" ? "var(--primary)" : "var(--surface)",
              color: viewMode === "list" ? "#fff" : "var(--text-primary)",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: viewMode === "list" ? 600 : 400,
            }}
          >
            <List size={16} />
          </button>
          <button
            onClick={() => setViewMode("grid")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: viewMode === "grid" ? "var(--primary)" : "var(--surface)",
              color: viewMode === "grid" ? "#fff" : "var(--text-primary)",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: viewMode === "grid" ? 600 : 400,
            }}
          >
            <Grid3X3 size={16} />
          </button>
        </div>
      </div>

      {error ? <div className="banner err">{error}</div> : null}
      {revealError ? <div className="banner err" style={{ marginBottom: 12 }}>{revealError}</div> : null}
      {jobs && jobs.length === 0 ? <p className="muted">{t.history_empty}</p> : null}
      {!jobs && !error ? <p className="muted">{t.loading}</p> : null}

      {viewMode === "list" ? (
        <div className="joblist">
          {jobs?.map((job) => (
            <div key={job.id} className="jobrow" style={{ display: "flex", alignItems: "center" }}>
              <button style={{ flex: 1, display: "flex", alignItems: "center", gap: 12, background: "none", border: "none", cursor: "pointer", padding: 0, textAlign: "left" }} onClick={() => onOpen(job.id)}>
                <span className="title">{job.source_title || job.video_url}</span>
                {job.created_at && (
                  <span className="muted" style={{ fontSize: 12, flexShrink: 0 }}>{timeAgo(job.created_at, isVI)}</span>
                )}
                <span className="muted">{job.target_language}</span>
                <span className={pillClass(job.status)}>{job.status}</span>
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onEdit(job); }}
                style={{ padding: "4px 8px", background: "none", border: "none", cursor: "pointer", color: "var(--text-secondary)", borderRadius: 6, flexShrink: 0 }}
                onMouseEnter={(e) => { e.currentTarget.style.color = "var(--primary)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-secondary)"; }}
                title={t.history_edit}
              >
                <Pencil size={14} />
              </button>
              <button
                disabled={deleting === job.id}
                onClick={(e) => void handleDelete(e, job.id)}
                style={{ padding: "4px 8px", background: "none", border: "none", cursor: "pointer", color: "var(--text-secondary)", borderRadius: 6, flexShrink: 0 }}
                onMouseEnter={(e) => { e.currentTarget.style.color = "var(--err)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-secondary)"; }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
          {jobs?.map((job) => (
            <div
              key={job.id}
              style={{
                display: "flex",
                flexDirection: "column",
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "var(--surface)",
                overflow: "hidden",
                transition: "border-color 200ms, box-shadow 200ms",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = "var(--primary)";
                (e.currentTarget as HTMLDivElement).style.boxShadow = "0 4px 12px rgba(230, 120, 49, 0.15)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = "var(--border)";
                (e.currentTarget as HTMLDivElement).style.boxShadow = "none";
              }}
            >
              <button
                onClick={() => onOpen(job.id)}
                style={{ display: "block", background: "none", border: "none", padding: 0, cursor: "pointer", textAlign: "left" }}
              >
                <VideoThumbnail job={job} />
                <div style={{ padding: "8px 10px 4px" }}>
                  <span
                    style={{
                      fontWeight: 600,
                      fontSize: 13,
                      display: "block",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      color: "var(--text-primary)",
                    }}
                  >
                    {job.source_title || job.video_url}
                  </span>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginTop: 4 }}>
                    <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{job.target_language}</span>
                    <span className={pillClass(job.status)} style={{ fontSize: 11 }}>{job.status}</span>
                  </div>
                  {job.created_at && (
                    <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 3 }}>
                      {timeAgo(job.created_at, isVI)}
                    </div>
                  )}
                </div>
              </button>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, padding: "6px 10px 8px" }}>
                {job.output_url ? (
                  <button
                    disabled={revealing === job.id}
                    onClick={(e) => void handleReveal(e, job.id)}
                    style={{ padding: "3px 10px", background: "var(--primary)", border: "none", borderRadius: 5, cursor: "pointer", color: "#fff", fontSize: 11, fontWeight: 600, opacity: revealing === job.id ? 0.65 : 1 }}
                  >
                    {revealing === job.id ? (isVI ? "Đang mở…" : "Opening…") : t.history_open}
                  </button>
                ) : (
                  <span />
                )}
                <div style={{ display: "flex", gap: 4 }}>
                  <button
                    onClick={(e) => { e.stopPropagation(); onEdit(job); }}
                    style={{ padding: "4px 7px", background: "none", border: "1px solid var(--border)", borderRadius: 5, cursor: "pointer", color: "var(--text-secondary)", display: "flex", alignItems: "center" }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = "var(--primary)"; e.currentTarget.style.borderColor = "var(--primary)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-secondary)"; e.currentTarget.style.borderColor = "var(--border)"; }}
                    title={t.history_edit}
                  >
                    <Pencil size={12} />
                  </button>
                  <button
                    disabled={deleting === job.id}
                    onClick={(e) => void handleDelete(e, job.id)}
                    style={{ padding: "4px 7px", background: "none", border: "1px solid var(--border)", borderRadius: 5, cursor: "pointer", color: "var(--text-secondary)", display: "flex", alignItems: "center" }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = "var(--err)"; e.currentTarget.style.borderColor = "var(--err)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-secondary)"; e.currentTarget.style.borderColor = "var(--border)"; }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
