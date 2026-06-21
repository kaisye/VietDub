import { useEffect, useRef, useState } from "react";
import { cancelJob, getJob, getRuntimeOptions, getRuntimeSettings, getVoiceOptions, outputUrl, retryJob, revealJob } from "../api";
import type { Job, RuntimeOptions, RuntimeSettings, VoiceProfile } from "../types";
import { useT } from "../i18n";

const ACTIVE = new Set([
  "queued",
  "downloading",
  "transcribing",
  "translating",
  "tts_generating",
  "rendering",
]);

type StepState = "done" | "active" | "error" | "pending";

interface PipelineStep {
  key: string;
  labelVI: string;
  labelEN: string;
  icon: string;
  progressMin: number;
  progressMax: number;
}

const STEPS: PipelineStep[] = [
  { key: "downloading",    labelVI: "Tải xuống",   labelEN: "Download",   icon: "⬇", progressMin: 0,  progressMax: 30 },
  { key: "transcribing",   labelVI: "Nhận dạng",   labelEN: "Transcribe", icon: "🎤", progressMin: 30, progressMax: 50 },
  { key: "translating",    labelVI: "Dịch thuật",  labelEN: "Translate",  icon: "🌐", progressMin: 50, progressMax: 70 },
  { key: "tts_generating", labelVI: "Tạo giọng",   labelEN: "Voice",      icon: "🔊", progressMin: 70, progressMax: 82 },
  { key: "rendering",      labelVI: "Render",       labelEN: "Render",     icon: "🎬", progressMin: 82, progressMax: 100 },
];

/**
 * The engine/model actually serving each pipeline step, derived from the live
 * runtime config. STT prefers NVIDIA Riva when an NVIDIA key is configured,
 * else Groq; TTS reflects the effective runtime (local GPU / Colab / Edge).
 */
// A recovery detail means the runtime is reconnecting/restarting — NOT actually
// producing output. Surfaced in a distinct (amber) style so the user can tell
// "synthesising for real" from "waiting on the runtime to come back".
function isRecoveryDetail(detail: string): boolean {
  const d = (detail || "").toLowerCase();
  return [
    "nối lại",
    "mất kết nối",
    "khôi phục",
    "khởi động",
    "cài đặt",
    "tải model",
    "chờ",
    "đăng nhập",
    "tạo lại",
    "reconnect",
    "restart",
    "waiting",
  ].some((kw) => d.includes(kw));
}

function voiceNeedsOmniVoice(voiceId: string, profiles: VoiceProfile[]): boolean {
  if (!voiceId || voiceId === "none") return false;
  const profile = profiles.find((p) => p.id === voiceId);
  if (!profile) return false;
  if (profile.omnivoice_mode === "clone" || profile.omnivoice_mode === "design") return true;
  return !!(profile.reference_audio_url || profile.reference_audio_path);
}

function engineLabel(
  stepKey: string,
  job: Job,
  settings: RuntimeSettings | null,
  options: RuntimeOptions | null,
  isVI: boolean,
  voiceProfiles: VoiceProfile[],
): string {
  switch (stepKey) {
    case "downloading":
      return job.download_quality === "best"
        ? (isVI ? "Chất lượng cao" : "Best quality")
        : (isVI ? "Ổn định" : "Reliable");
    case "transcribing": {
      // Reflect the subtitle source the job actually uses, not just whichever
      // STT key happens to be configured. OCR/embedded never touch Riva/Groq.
      const subtitleStrategy = job.configuration_snapshot?.source?.subtitle_strategy;
      if (subtitleStrategy === "ocr") return isVI ? "OCR phụ đề cứng" : "Hard-sub OCR";
      if (subtitleStrategy === "embedded") return isVI ? "Phụ đề có sẵn" : "Existing subs";
      if (!settings) return "";
      if (settings.nvidia_api_key_configured) return "NVIDIA Riva";
      if (settings.groq_api_key_configured) return "Groq Whisper";
      return isVI ? "Phụ đề có sẵn" : "Existing subs";
    }
    case "translating": {
      if (!settings) return "";
      const model =
        settings.translation_provider === "nvidia"
          ? settings.translation_nvidia_model
          : settings.local_translation_model;
      const provider = settings.translation_provider === "nvidia" ? "NVIDIA" : "Local";
      const short = (model || "").split("/").pop() || provider;
      return `${provider} · ${short}`;
    }
    case "tts_generating": {
      if (job.voice === "none") return isVI ? "Tắt giọng" : "No voice";
      // VieNeu preset voices are routed by the voice itself (offline CPU engine),
      // independent of the global runtime — detect them first.
      const profile = voiceProfiles.find((p) => p.id === job.voice);
      if (profile?.engine === "vieneu") return isVI ? "VieNeu · CPU offline" : "VieNeu · CPU offline";
      const rt = options?.effective_tts_runtime;
      // Clone/design voices are auto-promoted to OmniVoice by the backend even when
      // the global tts_provider is "edge". Detect this case so the label stays honest.
      const cloneVoice = voiceNeedsOmniVoice(job.voice, voiceProfiles);
      if (rt === "omnivoice_local") return "OmniVoice GPU";
      if (rt === "omnivoice_colab") return "OmniVoice Colab";
      if (cloneVoice) return "OmniVoice";
      if (rt === "edge") return "Edge TTS";
      return settings?.tts_provider === "omnivoice" ? "OmniVoice" : "Edge TTS";
    }
    case "rendering":
      return job.render_quality
        ? `FFmpeg · ${job.render_quality}`
        : "FFmpeg";
    default:
      return "";
  }
}

function getStepState(step: PipelineStep, job: Job): StepState {
  if (job.status === "ready") return "done";
  if (job.status === "cancelled") {
    // Halted pipeline: steps already passed show done, the rest just stop —
    // no spinning "active" box and no error icon.
    const stopIdx = STEPS.findIndex(
      (s) => job.progress >= s.progressMin && job.progress < s.progressMax,
    );
    const resolvedIdx = stopIdx < 0 ? STEPS.length - 1 : stopIdx;
    return STEPS.indexOf(step) < resolvedIdx ? "done" : "pending";
  }
  if (job.status === "failed") {
    const failedIdx = STEPS.findIndex(
      (s) => job.progress >= s.progressMin && job.progress < s.progressMax,
    ) ?? STEPS.findIndex((s) => s.key === "rendering");
    const thisIdx = STEPS.indexOf(step);
    const resolvedIdx = failedIdx < 0 ? STEPS.length - 1 : failedIdx;
    if (thisIdx < resolvedIdx) return "done";
    if (thisIdx === resolvedIdx) return "error";
    return "pending";
  }
  const activeIdx = STEPS.findIndex((s) => s.key === job.status);
  const thisIdx = STEPS.indexOf(step);
  if (activeIdx < 0) return thisIdx === 0 ? "active" : "pending";
  if (thisIdx < activeIdx) return "done";
  if (thisIdx === activeIdx) return "active";
  return "pending";
}

function stepProgressPct(step: PipelineStep, job: Job): number {
  const range = step.progressMax - step.progressMin;
  if (range <= 0) return 0;
  const within = Math.max(0, Math.min(range, job.progress - step.progressMin));
  return Math.round((within / range) * 100);
}

// Backend timestamps are naive UTC ISO strings (datetime.utcnow().isoformat()).
// Append "Z" when no timezone is present so they parse as UTC, not local time —
// otherwise a live counter would be off by the local UTC offset.
function parseServerTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const hasZone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value);
  const ms = Date.parse(hasZone ? value : `${value}Z`);
  return Number.isNaN(ms) ? null : ms;
}

function formatDuration(secs: number, isVI: boolean): string {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (isVI) {
    if (h > 0) return `${h} giờ ${m} phút ${s} giây`;
    if (m > 0) return `${m} phút ${s} giây`;
    return `${s} giây`;
  } else {
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }
}

function StepBox({
  step,
  state,
  job,
  isVI,
  isLast,
  engine,
}: {
  step: PipelineStep;
  state: StepState;
  job: Job;
  isVI: boolean;
  isLast: boolean;
  engine: string;
}) {
  const label = isVI ? step.labelVI : step.labelEN;
  const pct = state === "active" ? stepProgressPct(step, job) : 0;
  const indeterminate = state === "active" && pct < 5;

  const borderColor =
    state === "done"   ? "var(--success-soft)" :
    state === "active" ? "var(--primary)"       :
    state === "error"  ? "var(--danger-soft)"   :
                         "var(--border-subtle)";

  const bgColor =
    state === "done"   ? "var(--success-soft)" :
    state === "active" ? "var(--primary-soft)"  :
    state === "error"  ? "var(--danger-soft)"   :
                         "var(--surface-muted)";

  const iconColor =
    state === "done"   ? "var(--ok)"      :
    state === "active" ? "var(--primary)" :
    state === "error"  ? "var(--err)"     :
                         "var(--muted)";

  const textColor = state === "pending" ? "var(--muted)" : "var(--text)";

  return (
    <div style={{ display: "flex", alignItems: "center", flex: state === "active" ? "1.6 1 0" : "1 1 0", minWidth: 0 }}>
      <div
        style={{
          flex: 1,
          minWidth: 0,
          padding: state === "active" ? "14px 16px" : "10px 14px",
          borderRadius: 10,
          border: `2px solid ${borderColor}`,
          background: bgColor,
          transition: "all 0.25s ease",
        }}
      >
        {/* Icon + Label row */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: state === "active" ? 10 : 0 }}>
          <span
            style={{
              fontSize: state === "active" ? 20 : 16,
              lineHeight: 1,
              color: iconColor,
              display: "flex",
              alignItems: "center",
            }}
          >
            {state === "done"   ? <CheckIcon /> :
             state === "error"  ? <ErrorIcon /> :
             state === "active" ? <>{step.icon}<SpinnerRing /></> :
                                   step.icon}
          </span>
          <span
            style={{
              fontSize: 12,
              fontWeight: state === "active" ? 700 : 500,
              color: textColor,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {label}
          </span>
        </div>

        {/* Engine / model serving this step */}
        {engine ? (
          <div
            title={engine}
            style={{
              marginTop: 4,
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: 0.2,
              color: state === "pending" ? "var(--muted)" : "var(--primary)",
              opacity: state === "pending" ? 0.7 : 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {engine}
          </div>
        ) : null}

        {/* Active: step description + progress bar */}
        {state === "active" && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <p
                style={{
                  margin: 0,
                  fontSize: 11,
                  color: "var(--muted)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  flex: 1,
                }}
              >
                {job.current_step || (isVI ? "Đang xử lý…" : "Processing…")}
              </p>
              {!indeterminate && (
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: "var(--primary)",
                    flexShrink: 0,
                    marginLeft: 8,
                  }}
                >
                  {pct}%
                </span>
              )}
            </div>
            <div
              style={{
                height: 6,
                borderRadius: 9999,
                background: "var(--border-subtle)",
                overflow: "hidden",
                position: "relative",
              }}
            >
              {indeterminate ? (
                <div
                  style={{
                    position: "absolute",
                    top: 0,
                    height: "100%",
                    width: "35%",
                    background: "var(--primary)",
                    borderRadius: 9999,
                    animation: "indeterminate-slide 1.4s ease-in-out infinite",
                  }}
                />
              ) : (
                <div
                  style={{
                    height: "100%",
                    width: `${Math.max(4, pct)}%`,
                    background: "var(--primary)",
                    borderRadius: 9999,
                    transition: "width 0.5s ease",
                  }}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* Connector arrow */}
      {!isLast && (
        <div
          style={{
            flexShrink: 0,
            width: 20,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: state === "done" ? "var(--ok)" : "var(--border-subtle)",
            fontSize: 14,
            fontWeight: 700,
          }}
        >
          →
        </div>
      )}
    </div>
  );
}

function CheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="7" fill="var(--ok)" opacity="0.15" />
      <path d="M4.5 8L7 10.5L11.5 6" stroke="var(--ok)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ErrorIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="7" fill="var(--err)" opacity="0.15" />
      <path d="M5.5 5.5L10.5 10.5M10.5 5.5L5.5 10.5" stroke="var(--err)" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function SpinnerRing() {
  return (
    <span
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        border: "2px solid var(--primary)",
        borderTopColor: "transparent",
        borderRadius: "50%",
        animation: "spin 0.8s linear infinite",
        marginLeft: 4,
        flexShrink: 0,
      }}
    />
  );
}

function pillClass(status: string): string {
  if (status === "ready") return "pill ready";
  if (status === "failed" || status === "cancelled") return "pill failed";
  if (ACTIVE.has(status)) return "pill run";
  return "pill idle";
}

export default function ProgressScreen({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const { t } = useT();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [pollKey, setPollKey] = useState(0);
  const timer = useRef<number | null>(null);
  const prevJobRef = useRef<Job | null>(null);
  // Re-renders the live elapsed counter while the job is active. The final
  // "completed in" duration is derived from persisted job timestamps, not this.
  const [nowTick, setNowTick] = useState<number>(Date.now());
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [options, setOptions] = useState<RuntimeOptions | null>(null);
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([]);

  useEffect(() => {
    getRuntimeSettings().then(setSettings).catch(() => undefined);
    getRuntimeOptions().then(setOptions).catch(() => undefined);
  }, [pollKey]);

  useEffect(() => {
    getVoiceOptions().then(setVoiceProfiles).catch(() => undefined);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setJob(null);
    prevJobRef.current = null;

    async function tick() {
      try {
        const next = await getJob(jobId);
        if (cancelled) return;
        prevJobRef.current = next;
        setJob(next);
        if (ACTIVE.has(next.status) || next.status === "draft") {
          timer.current = window.setTimeout(tick, 2000);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : t.progress_lost);
      }
    }
    void tick();
    return () => {
      cancelled = true;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [jobId, t.progress_lost, pollKey]);

  // Tick the live elapsed counter every second while the job is processing.
  useEffect(() => {
    if (!job || !ACTIVE.has(job.status)) return;
    const id = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [job?.status]);

  async function retry() {
    setRetrying(true);
    setError("");
    try {
      await retryJob(jobId);
      setPollKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : t.progress_retry_fail);
    } finally {
      setRetrying(false);
    }
  }

  async function reveal() {
    const vi = t.nav_create === "Tạo video";
    setRevealing(true);
    setError("");
    try {
      await revealJob(jobId);
    } catch (e) {
      setError(e instanceof Error ? e.message : vi ? "Không thể mở thư mục." : "Could not open folder.");
    } finally {
      setRevealing(false);
    }
  }

  async function cancel() {
    const isVI = t.nav_create === "Tạo video";
    const confirmMsg = isVI
      ? "Hủy job đang chạy? Worker sẽ dừng ở checkpoint gần nhất."
      : "Cancel the running job? The worker will stop at the next checkpoint.";
    if (!window.confirm(confirmMsg)) return;
    setCancelling(true);
    setError("");
    try {
      const updated = await cancelJob(jobId);
      prevJobRef.current = updated;
      setJob(updated);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : isVI ? "Hủy thất bại." : "Cancel failed.",
      );
    } finally {
      setCancelling(false);
    }
  }

  if (!job) {
    return (
      <div className="page">
        {error ? <div className="banner err">{error}</div> : <p className="muted">{t.progress_loading}</p>}
      </div>
    );
  }

  const isVI = t.nav_create === "Tạo video";
  const out = outputUrl(job.output_url);
  const logsTail = (job.logs ?? "").split("\n").slice(-12).join("\n");

  // Processing duration from server-persisted timestamps: completed_at - started_at
  // once finished (stable on every revisit), or a live count while still active.
  const startedMs = parseServerTime(job.started_at);
  const completedMs = parseServerTime(job.completed_at);
  const elapsed =
    startedMs == null
      ? null
      : completedMs != null
        ? Math.max(0, Math.round((completedMs - startedMs) / 1000))
        : ACTIVE.has(job.status)
          ? Math.max(0, Math.round((nowTick - startedMs) / 1000))
          : null;

  return (
    <div className="page">
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 4 }}>
        <h1
          style={{
            flex: 1,
            margin: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontSize: 18,
          }}
        >
          {job.source_title || job.video_url}
        </h1>
        <span className={pillClass(job.status)} style={{ flexShrink: 0, marginTop: 3 }}>
          {job.status}
        </span>
      </div>
      <p className="sub" style={{ marginBottom: 20 }}>
        {job.source_language} → {job.target_language} ·{" "}
        {job.voice === "none" ? t.progress_no_voice : job.voice}
      </p>

      {error ? <div className="banner err" style={{ marginBottom: 16 }}>{error}</div> : null}

      {/* Pipeline flow */}
      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          gap: 0,
          marginBottom: 8,
          padding: "16px",
          background: "var(--surface)",
          borderRadius: 12,
          border: "1px solid var(--border-subtle)",
        }}
      >
        {STEPS.map((step, i) => (
          <StepBox
            key={step.key}
            step={step}
            state={getStepState(step, job)}
            job={job}
            isVI={isVI}
            isLast={i === STEPS.length - 1}
            engine={engineLabel(step.key, job, settings, options, isVI, voiceProfiles)}
          />
        ))}
      </div>

      {/* Live sub-status: what's happening right now within the active step */}
      {ACTIVE.has(job.status) && (job.step_detail || "").trim() !== "" && (() => {
        const recovering = isRecoveryDetail(job.step_detail || "");
        return (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 14px",
              marginBottom: 12,
              borderRadius: 10,
              background: recovering ? "var(--warning-soft)" : "var(--surface-muted)",
              border: `1px solid ${recovering ? "var(--warn)" : "var(--border-subtle)"}`,
            }}
          >
            <span
              style={{
                width: 14,
                height: 14,
                borderRadius: "50%",
                border: "2px solid var(--border)",
                borderTopColor: recovering ? "var(--warn)" : "var(--primary)",
                animation: "spin 0.8s linear infinite",
                flexShrink: 0,
              }}
            />
            <span
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: recovering ? "var(--warn)" : "var(--text)",
              }}
            >
              {job.step_detail}
            </span>
            {recovering && (
              <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
                {isVI ? "đang khôi phục — chưa mất tiến trình" : "recovering — progress kept"}
              </span>
            )}
          </div>
        );
      })()}

      {/* Overall % + cancel while running */}
      {ACTIVE.has(job.status) && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            marginBottom: 20,
          }}
        >
          <button
            className="btn"
            disabled={cancelling}
            onClick={() => void cancel()}
            style={{ color: "var(--err)", borderColor: "var(--danger-soft)" }}
          >
            {cancelling
              ? isVI
                ? "Đang hủy…"
                : "Cancelling…"
              : isVI
                ? "Hủy job"
                : "Cancel job"}
          </button>
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            {job.progress}% {isVI ? "hoàn thành" : "complete"}
          </p>
        </div>
      )}

      {/* Spacer when done */}
      {!ACTIVE.has(job.status) && <div style={{ marginBottom: 20 }} />}

      {/* Result */}
      {job.status === "ready" && out ? (
        <div className="card">
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
            <h2 style={{ margin: 0 }}>{t.progress_result}</h2>
            {elapsed !== null && (
              <span className="muted" style={{ fontSize: 12 }}>
                {isVI
                  ? `Hoàn thành trong ${formatDuration(elapsed, true)}`
                  : `Completed in ${formatDuration(elapsed, false)}`}
              </span>
            )}
          </div>
          <video src={out} controls style={{ width: "100%", borderRadius: 6 }} />
          <div className="actions" style={{ marginTop: 12 }}>
            <button className="btn primary" disabled={revealing} onClick={() => void reveal()}>
              {revealing
                ? (isVI ? "Đang mở…" : "Opening…")
                : t.progress_open}
            </button>
          </div>
        </div>
      ) : null}

      {/* Failed */}
      {job.status === "failed" ? (
        <div className="card">
          <div className="banner err">{job.error_message || t.progress_failed}</div>
          {logsTail ? <pre className="logbox" style={{ marginTop: 10 }}>{logsTail}</pre> : null}
          <div className="actions" style={{ marginTop: 12 }}>
            <button className="btn primary" disabled={retrying} onClick={() => void retry()}>
              {retrying ? t.progress_retrying : t.progress_retry_btn}
            </button>
          </div>
        </div>
      ) : null}

      {/* Cancelled */}
      {job.status === "cancelled" ? (
        <div className="card">
          <div className="banner">
            {isVI ? "Job đã bị hủy." : "Job was cancelled."}
          </div>
          {logsTail ? <pre className="logbox" style={{ marginTop: 10 }}>{logsTail}</pre> : null}
          <div className="actions" style={{ marginTop: 12 }}>
            <button className="btn primary" disabled={retrying} onClick={() => void retry()}>
              {retrying ? t.progress_retrying : isVI ? "Chạy lại" : "Run again"}
            </button>
          </div>
        </div>
      ) : null}

      <div className="actions" style={{ marginTop: 8 }}>
        <button className="btn" onClick={onBack}>
          {t.progress_back}
        </button>
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes indeterminate-slide {
          0%   { left: -40%; }
          60%  { left: 100%; }
          100% { left: 100%; }
        }
      `}</style>
    </div>
  );
}
