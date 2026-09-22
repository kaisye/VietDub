import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { cancelJob, getJob, getRuntimeOptions, getRuntimeSettings, getVoiceOptions, outputUrl, rerenderJobVoice, retryJob, revealJob, voicePreviewUrl } from "../api";
import type { Job, RuntimeOptions, RuntimeSettings, VoiceProfile } from "../types";
import { useT } from "../i18n";
import { openUrl } from "../lib/open-url";

const ROUTER_DASHBOARD = "http://127.0.0.1:20128/dashboard";

// Translation runs only through the local 9router endpoint, so backend
// translate errors carry the literal "9router" marker (see translator.py).
function isRouterProblem(message: string | null | undefined): boolean {
  return /9router/i.test(message || "");
}

// Make sure 9router is up: reuse the running instance, start it if installed but
// stopped, otherwise npm-install it (1-2 min) then start. Throws on real failure.
async function ensureRouter(): Promise<void> {
  try {
    if (await invoke<boolean>("status_9router")) return; // already running
  } catch {
    /* status probe failed — fall through to start */
  }
  try {
    await invoke("start_9router");
    return;
  } catch {
    /* not installed yet — install then start */
  }
  await invoke("install_9router");
  await invoke("start_9router");
}

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
  if (profile.engine === "zerotts" || profile.engine === "edge") return false;
  if (profile.engine === "omnivoice") return true;
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
      // Translation always runs through the local 9router endpoint.
      if (!settings) return "9router";
      const model = settings.local_translation_model || "translate";
      const short = (model || "").split("/").pop() || "translate";
      return `9router · ${short}`;
    }
    case "tts_generating": {
      if (job.voice === "none") return isVI ? "Tắt giọng" : "No voice";
      // Local preset voices are routed by the voice itself, independent of the
      // global runtime, so detect them first.
      const profile = voiceProfiles.find((p) => p.id === job.voice);
      if (profile?.engine === "zerotts") return "ZeroTTS · CPU";
      const rt = options?.effective_tts_runtime;
      // Clone/design voices are auto-promoted to OmniVoice by the backend even when
      // the global tts_provider is "edge". Detect this case so the label stays honest.
      const cloneVoice = voiceNeedsOmniVoice(job.voice, voiceProfiles);
      if (rt === "omnivoice_local") return "OmniVoice GPU";
      if (rt === "omnivoice_colab") return "OmniVoice Colab";
      if (cloneVoice) return "OmniVoice";
      if (rt === "zerotts") return "ZeroTTS · CPU";
      if (rt === "edge") return "Edge TTS";
      return settings?.tts_provider === "zerotts"
        ? "ZeroTTS · CPU"
        : settings?.tts_provider === "omnivoice"
        ? "OmniVoice"
        : "Edge TTS";
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
  const [recovering, setRecovering] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [pollKey, setPollKey] = useState(0);
  const timer = useRef<number | null>(null);
  // Auto-recovery (start 9router + open dashboard) runs at most once per failure.
  const recoverAttempted = useRef(false);
  const prevJobRef = useRef<Job | null>(null);
  // Re-renders the live elapsed counter while the job is active. The final
  // "completed in" duration is derived from persisted job timestamps, not this.
  const [nowTick, setNowTick] = useState<number>(Date.now());
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [options, setOptions] = useState<RuntimeOptions | null>(null);
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([]);
  const [showVoiceEditor, setShowVoiceEditor] = useState(false);
  const [selectedVoice, setSelectedVoice] = useState("");
  const [rerenderingVoice, setRerenderingVoice] = useState(false);
  const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);
  const voicePreviewRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    getRuntimeSettings().then(setSettings).catch(() => undefined);
    getRuntimeOptions().then(setOptions).catch(() => undefined);
  }, [pollKey]);

  useEffect(() => {
    getVoiceOptions().then(setVoiceProfiles).catch(() => undefined);
    return () => voicePreviewRef.current?.pause();
  }, []);

  useEffect(() => {
    if (job && !showVoiceEditor) setSelectedVoice(job.voice);
  }, [job?.voice, showVoiceEditor]);

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

  // When a job fails because the local router is down/unconfigured, auto-start
  // 9router and open its dashboard once so the user can configure it right away.
  useEffect(() => {
    if (!job || job.status !== "failed") {
      recoverAttempted.current = false;
      return;
    }
    if (recoverAttempted.current || !isRouterProblem(job.error_message)) return;
    recoverAttempted.current = true;
    void prepareRouter();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status, job?.error_message]);

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

  // Bring 9router up and open its dashboard so the user can pick a provider/model.
  // Does NOT auto-retry — translation needs a configured model first.
  async function prepareRouter() {
    const isVI = t.nav_create === "Tạo video";
    setRecovering(true);
    setError("");
    try {
      await ensureRouter();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : isVI
            ? "Không thể khởi động 9router."
            : "Could not start 9router.",
      );
    } finally {
      // Open the dashboard even if start failed, so the user can act on it.
      await openUrl(ROUTER_DASHBOARD).catch(() => undefined);
      setRecovering(false);
    }
  }

  // Manual recovery button: ensure the router is up, open the dashboard, retry.
  async function recoverAndRetry() {
    const isVI = t.nav_create === "Tạo video";
    setRecovering(true);
    setError("");
    try {
      await ensureRouter();
      await openUrl(ROUTER_DASHBOARD).catch(() => undefined);
      await retryJob(jobId);
      setPollKey((k) => k + 1);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : isVI ? "Không thể khởi động 9router." : "Could not start 9router.",
      );
    } finally {
      setRecovering(false);
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

  function previewVoice(voiceId: string) {
    if (!voiceId || voiceId === "none") return;
    if (previewingVoiceId === voiceId) {
      voicePreviewRef.current?.pause();
      voicePreviewRef.current = null;
      setPreviewingVoiceId(null);
      return;
    }
    voicePreviewRef.current?.pause();
    const audio = new Audio(voicePreviewUrl(voiceId));
    voicePreviewRef.current = audio;
    setPreviewingVoiceId(voiceId);
    audio.onended = () => setPreviewingVoiceId(null);
    audio.onerror = () => setPreviewingVoiceId(null);
    void audio.play().catch(() => setPreviewingVoiceId(null));
  }

  function selectPreviewVoice(voiceId: string) {
    voicePreviewRef.current?.pause();
    voicePreviewRef.current = null;
    setPreviewingVoiceId(null);
    setSelectedVoice(voiceId);
  }

  async function changeVoiceAndRerender() {
    if (!selectedVoice || rerenderingVoice) return;
    setRerenderingVoice(true);
    setError("");
    voicePreviewRef.current?.pause();
    setPreviewingVoiceId(null);
    try {
      const updated = await rerenderJobVoice(jobId, selectedVoice);
      prevJobRef.current = updated;
      setJob(updated);
      setShowVoiceEditor(false);
      setPollKey((key) => key + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : t.progress_voice_rerender_error);
    } finally {
      setRerenderingVoice(false);
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
            <button
              className="btn"
              onClick={() => setShowVoiceEditor((value) => !value)}
            >
              {t.progress_change_voice}
            </button>
          </div>
          {showVoiceEditor ? (
            <div
              style={{
                marginTop: 14,
                padding: 14,
                border: "1px solid var(--border)",
                borderRadius: 10,
                background: "var(--surface-muted)",
              }}
            >
              <strong style={{ display: "block", marginBottom: 4 }}>
                {t.progress_change_voice_title}
              </strong>
              <p className="muted" style={{ fontSize: 12, margin: "0 0 10px" }}>
                {t.progress_change_voice_desc}
              </p>
              <div
                role="listbox"
                aria-label={t.progress_change_voice_title}
                style={{
                  maxHeight: 320,
                  overflowY: "auto",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  background: "var(--surface)",
                  marginBottom: 12,
                }}
              >
                {voiceProfiles.map((voice, index) => {
                  const selected = voice.id === selectedVoice;
                  const playing = voice.id === previewingVoiceId;
                  return (
                    <div
                      key={voice.id}
                      role="option"
                      aria-selected={selected}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "7px 8px 7px 12px",
                        background: selected ? "var(--primary-soft)" : "transparent",
                        borderBottom: index < voiceProfiles.length - 1 ? "1px solid var(--border-subtle)" : "none",
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => selectPreviewVoice(voice.id)}
                        style={{
                          flex: 1,
                          minWidth: 0,
                          padding: "3px 0",
                          border: "none",
                          background: "transparent",
                          color: "var(--text-primary)",
                          cursor: "pointer",
                          textAlign: "left",
                        }}
                      >
                        <span style={{ fontWeight: selected ? 700 : 500 }}>
                          {selected ? "✓ " : ""}{voice.name}
                        </span>
                        <span className="muted"> · {voice.type}</span>
                      </button>
                      <button
                        className="btn"
                        type="button"
                        disabled={voice.id === "none"}
                        onClick={() => previewVoice(voice.id)}
                        aria-label={`${playing ? t.progress_voice_stop : t.listen}: ${voice.name}`}
                        title={playing ? t.progress_voice_stop : t.listen}
                        style={{
                          width: 34,
                          height: 34,
                          padding: 0,
                          borderRadius: "50%",
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          flexShrink: 0,
                          fontSize: playing ? 12 : 15,
                          lineHeight: 1,
                        }}
                      >
                        <span aria-hidden="true" style={{ marginLeft: playing ? 0 : 2 }}>
                          {playing ? "■" : "▶"}
                        </span>
                      </button>
                    </div>
                  );
                })}
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button
                  className="btn primary"
                  disabled={!selectedVoice || rerenderingVoice}
                  onClick={() => void changeVoiceAndRerender()}
                >
                  {rerenderingVoice ? t.progress_voice_rerendering : t.progress_voice_rerender}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Failed */}
      {job.status === "failed" ? (
        <div className="card">
          <div className="banner err">{job.error_message || t.progress_failed}</div>
          {logsTail ? <pre className="logbox" style={{ marginTop: 10 }}>{logsTail}</pre> : null}
          {isRouterProblem(job.error_message) ? (
            <>
              <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                {isVI
                  ? "Dịch thuật chạy qua 9router (cục bộ). VietDub đang tự khởi động 9router và mở dashboard — hãy chọn provider/model rồi bấm Thử lại."
                  : "Translation runs through 9router (local). VietDub is starting 9router and opening its dashboard — pick a provider/model, then retry."}
              </p>
              <div className="actions" style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="btn" disabled={recovering} onClick={() => void prepareRouter()}>
                  {recovering
                    ? isVI ? "Đang khởi động 9router…" : "Starting 9router…"
                    : isVI ? "Mở dashboard 9router" : "Open 9router dashboard"}
                </button>
                <button
                  className="btn primary"
                  disabled={retrying || recovering}
                  onClick={() => void recoverAndRetry()}
                >
                  {retrying || recovering
                    ? t.progress_retrying
                    : isVI ? "Khởi động 9router & Thử lại" : "Start 9router & retry"}
                </button>
              </div>
            </>
          ) : (
            <div className="actions" style={{ marginTop: 12 }}>
              <button className="btn primary" disabled={retrying} onClick={() => void retry()}>
                {retrying ? t.progress_retrying : t.progress_retry_btn}
              </button>
            </div>
          )}
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
