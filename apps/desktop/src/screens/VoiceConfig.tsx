import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Download, Info, Pause, Play, Scissors } from "lucide-react";
import {
  createVoiceOption,
  deleteVoiceOption,
  getRuntimeOptions,
  getRuntimeSettings,
  getVoiceOptions,
  getWorkspaceSettings,
  getColabStatus,
  launchColab,
  stopColab,
  setupColabCli,
  switchColabAccount,
  submitColabAuthCode,
  getLocalOmniVoiceSetup,
  getVoiceReferenceWaveform,
  startLocalOmniVoiceSetup,
  updateRuntimeSettings,
  updateWorkspaceSettings,
  downloadVoiceReference,
  outputUrl,
  trimVoiceReference,
  uploadVoiceReference,
  voicePreviewUrl,
} from "../api";
import type {
  OmniVoiceColabStatus,
  OmniVoiceLocalSetup,
  RuntimeOptions,
  RuntimeSettings,
  VoiceProfile,
} from "../types";
import { useT, tt } from "../i18n";
import { openUrl } from "../lib/open-url";
import {
  INSTRUCT_GENDER,
  INSTRUCT_AGE,
  INSTRUCT_PITCH,
  INSTRUCT_STYLE,
  parseInstruct,
  buildInstruct,
} from "../lib/voice-design";
import type { VoiceReferenceMedia } from "../api";

// ─── helpers ────────────────────────────────────────────────────────────────

function emptyProfile(): VoiceProfile {
  return {
    id: "",
    name: "",
    locale: "vi-VN",
    language: "Vietnamese",
    type: "Custom",
    description: "",
    omnivoice_mode: "clone",
    reference_audio_url: "",
    reference_audio_path: "",
    reference_text: "",
    reference_text_path: "",
    instruction: "",
  };
}

function formatDuration(seconds: number, h: string, m: string, s: string): string {
  if (seconds < 60) return `${seconds}${s}`;
  const hv = Math.floor(seconds / 3600);
  const mv = Math.floor((seconds % 3600) / 60);
  const sv = seconds % 60;
  if (hv > 0) return `${hv}${h} ${mv}${m} ${sv}${s}`;
  return `${mv}${m} ${sv}${s}`;
}

const COLAB_WARN_AGE_SECONDS = 10 * 3600;
const COLAB_MAX_AGE_SECONDS = 12 * 3600;

const POLLING_STATES = new Set(["starting", "installing", "loading", "waiting_for_gpu"]);
const LIVE_STATES = new Set(["starting", "waiting_for_login", "waiting_for_gpu", "loading", "ready", "disconnected"]);

// Ordered bootstrap phases (after login, before ready). Each maps to a target
// fill % so the bar advances phase-by-phase; the shimmer conveys ongoing work
// within a phase (the model-load phase is long and otherwise reads as a hang).
const BOOTSTRAP_STATES = ["waiting_for_gpu", "starting", "installing", "loading"];
const BOOTSTRAP_PCT: Record<string, number> = {
  waiting_for_gpu: 12,
  starting: 32,
  installing: 58,
  loading: 84,
};

const STATE_COLOR: Record<string, string> = {
  stopped: "var(--text-secondary)",
  setup_required: "var(--warn)",
  installing: "var(--warn)",
  starting: "var(--warn)",
  waiting_for_login: "var(--warn)",
  waiting_for_gpu: "var(--warn)",
  loading: "var(--warn)",
  ready: "var(--ok)",
  disconnected: "var(--err)",
  error: "var(--err)",
};

// ─── sub-components ─────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <h2>{title}</h2>
      {children}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}

// ─── StateDot ────────────────────────────────────────────────────────────────

function StateDot({ state }: { state: string }) {
  const color = STATE_COLOR[state] ?? "var(--text-secondary)";
  const isSpinning = POLLING_STATES.has(state) || state === "waiting_for_login";
  return (
    <span
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        borderRadius: "50%",
        background: color,
        flexShrink: 0,
        animation: isSpinning ? "pulse 1.2s ease-in-out infinite" : "none",
      }}
    />
  );
}

// ─── SessionTimer ─────────────────────────────────────────────────────────────

function SessionTimer({ ageSeconds }: { ageSeconds: number }) {
  const { t } = useT();
  const [elapsed, setElapsed] = useState(ageSeconds);

  useEffect(() => {
    setElapsed(ageSeconds);
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, [ageSeconds]);

  const pct = Math.min(100, (elapsed / COLAB_MAX_AGE_SECONDS) * 100);
  const isWarn = elapsed >= COLAB_WARN_AGE_SECONDS;
  const barColor = isWarn ? "var(--warn)" : "var(--ok)";
  const remaining = Math.max(0, COLAB_MAX_AGE_SECONDS - elapsed);
  const fmtDur = (s: number) => formatDuration(s, t.dur_h, t.dur_m, t.dur_s);

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: "var(--text-secondary)" }}>
          {t.colab_elapsed}{" "}
          <span style={{ fontWeight: 600, fontFamily: "monospace" }}>{fmtDur(elapsed)}</span>
        </span>
        <span style={{ color: isWarn ? "var(--warn)" : "var(--text-secondary)" }}>
          {remaining > 0
            ? tt(t.colab_remaining, { t: fmtDur(remaining) })
            : t.colab_expired}
        </span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: "var(--border)", overflow: "hidden" }}>
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background: barColor,
            borderRadius: 3,
            transition: "width 1s linear",
          }}
        />
      </div>
      {isWarn && (
        <div style={{ fontSize: 12, color: "var(--warn)", marginTop: 4 }}>{t.colab_warn}</div>
      )}
    </div>
  );
}

// ─── ColabBootstrapProgress ──────────────────────────────────────────────────
// Phase-based progress bar for the OmniVoice-on-Colab bootstrap. The install +
// model download takes minutes; a static text line read as a hang. This shows
// which phase is active, an always-moving shimmer, and a live elapsed timer.

function ColabBootstrapProgress({ state, startedAt }: { state: string; startedAt: number | null }) {
  const { t } = useT();
  // Tick locally every second so the elapsed clock advances smoothly between the
  // 4-second status polls (otherwise it would jump and feel frozen).
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const steps = [
    { key: "waiting_for_gpu", label: t.colab_step_gpu },
    { key: "starting", label: t.colab_step_start },
    { key: "installing", label: t.colab_step_install },
    { key: "loading", label: t.colab_step_model },
  ];
  const currentIdx = steps.findIndex((s) => s.key === state);
  const pct = BOOTSTRAP_PCT[state] ?? 0;
  const phaseMsg =
    state === "loading"
      ? t.colab_loading_msg
      : state === "installing"
        ? t.colab_installing_msg
        : state === "starting"
          ? t.colab_starting_msg
          : t.colab_waiting_gpu;
  const elapsed = startedAt ? Math.max(0, Math.floor(now / 1000 - startedAt)) : null;
  const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  return (
    <div style={{ marginTop: 12 }}>
      <div className="progress-track">
        <div className="colab-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 6, marginTop: 7 }}>
        {steps.map((s, i) => (
          <span
            key={s.key}
            style={{
              fontSize: 11,
              fontWeight: i === currentIdx ? 700 : 500,
              color:
                i < currentIdx
                  ? "var(--ok)"
                  : i === currentIdx
                    ? "var(--primary)"
                    : "var(--text-tertiary)",
            }}
          >
            {i < currentIdx ? "✓ " : ""}
            {s.label}
          </span>
        ))}
      </div>
      <div style={{ marginTop: 9, fontSize: 13, color: "var(--text-secondary)" }}>
        {phaseMsg}
        {elapsed != null && <span style={{ color: "var(--text-tertiary)" }}> · {fmt(elapsed)}</span>}
      </div>
      {state === "loading" && (
        <div style={{ marginTop: 3, fontSize: 11, color: "var(--text-tertiary)" }}>{t.colab_model_hint}</div>
      )}
    </div>
  );
}

// ─── ColabStatusPanel ────────────────────────────────────────────────────────

function ColabStatusPanel({
  status,
  onLaunch,
  onStop,
  onSetup,
  onSwitchAccount,
  onSubmitAuthCode,
  setupMessage,
  busy,
}: {
  status: OmniVoiceColabStatus;
  onLaunch: () => void;
  onStop: () => void;
  onSetup: () => void;
  onSwitchAccount: () => void;
  onSubmitAuthCode: (code: string) => void;
  setupMessage: string;
  busy: boolean;
}) {
  const { t } = useT();
  const [authCode, setAuthCode] = useState("");
  // Track the last code we auto-submitted so re-firing window focus events do not
  // resubmit the same value (and so a wrong/expired code isn't retried in a loop).
  const lastAutoCode = useRef("");

  // Google authorization codes look like "4/0Axxxx…" — long, URL-safe charset.
  const looksLikeAuthCode = (value: string) => /^4\/[0-9A-Za-z_-]{20,}$/.test(value);

  // After the user clicks "Copy" on Google's page, the code sits on the clipboard.
  // Read it back, auto-fill, and submit — so returning to the app is enough, no
  // manual paste. Clipboard reads can be blocked without a user gesture, so this
  // is best-effort on focus and reliably available via the explicit button.
  const tryClipboardCode = useCallback(
    async (autoSubmit: boolean) => {
      try {
        const text = (await navigator.clipboard.readText()).trim();
        if (!looksLikeAuthCode(text)) return;
        setAuthCode(text);
        if (autoSubmit && text !== lastAutoCode.current) {
          lastAutoCode.current = text;
          onSubmitAuthCode(text);
          setAuthCode("");
        }
      } catch {
        /* clipboard unavailable — fall back to manual paste */
      }
    },
    [onSubmitAuthCode],
  );

  // While waiting for the code, auto-detect it from the clipboard when the app
  // window regains focus (i.e. the user just came back from the browser).
  useEffect(() => {
    if (!status.needs_auth_code) {
      lastAutoCode.current = "";
      return;
    }
    void tryClipboardCode(true);
    const onFocus = () => void tryClipboardCode(true);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [status.needs_auth_code, tryClipboardCode]);

  const STATE_LABELS: Record<string, string> = {
    stopped: t.colab_state_stopped,
    setup_required: t.colab_state_setup_required,
    installing: t.colab_state_installing,
    starting: t.colab_state_starting,
    waiting_for_login: t.colab_state_waiting_for_login,
    waiting_for_gpu: t.colab_state_waiting_for_gpu,
    loading: t.colab_state_loading,
    ready: t.colab_state_ready,
    disconnected: t.colab_state_disconnected,
    error: t.colab_state_error,
  };

  const stateLabel = STATE_LABELS[status.state] ?? status.state;
  const stateColor = STATE_COLOR[status.state] ?? "var(--text-secondary)";
  const isLive = LIVE_STATES.has(status.state);
  const isReady = status.state === "ready";
  const needsSetup = status.state === "setup_required";
  const needsLogin = status.state === "waiting_for_login";
  const hasError = status.state === "error" || status.state === "disconnected";

  return (
    <div>
      {/* Status header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "12px 14px",
          background: "var(--surface-muted)",
          borderRadius: 8,
          marginBottom: 14,
        }}
      >
        <StateDot state={status.state} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: stateColor }}>{stateLabel}</div>
          {status.account_hint && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 1 }}>
              {status.account_hint}
            </div>
          )}
        </div>
        {status.gpu_name && (
          <div style={{ fontSize: 12, textAlign: "right", color: "var(--text-secondary)" }}>
            <div style={{ fontWeight: 600, color: "var(--ok)" }}>{status.gpu_name}</div>
            {status.gpu_memory_gb ? <div>{status.gpu_memory_gb} GB VRAM</div> : null}
          </div>
        )}
      </div>

      {isReady && status.session_age_seconds != null && (
        <SessionTimer ageSeconds={status.session_age_seconds} />
      )}

      {status.api_url && (
        <div
          style={{
            marginTop: 10,
            padding: "8px 10px",
            background: "var(--surface-muted)",
            borderRadius: 6,
            fontSize: 12,
          }}
        >
          <span style={{ color: "var(--text-secondary)" }}>{t.colab_endpoint} </span>
          <span className="mono" style={{ color: "var(--primary-hover)", wordBreak: "break-all" }}>
            {status.api_url}
          </span>
        </div>
      )}

      {status.authorization_url && (
        <div
          style={{
            marginTop: 12,
            padding: 12,
            background: "color-mix(in srgb, var(--warn) 10%, transparent)",
            border: "1px solid color-mix(in srgb, var(--warn) 40%, transparent)",
            borderRadius: 8,
          }}
        >
          <p style={{ margin: "0 0 8px", fontWeight: 600, fontSize: 13 }}>{t.colab_login_title}</p>
          <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--text-secondary)" }}>
            {t.colab_login_desc}
          </p>
          <button
            className="btn primary"
            onClick={() => void openUrl(status.authorization_url!)}
          >
            {t.colab_login_btn}
          </button>

          {status.needs_auth_code && (
            <div style={{ marginTop: 12, borderTop: "1px solid color-mix(in srgb, var(--warn) 30%, transparent)", paddingTop: 12 }}>
              <p style={{ margin: "0 0 8px", fontSize: 12, color: "var(--text-secondary)" }}>
                {t.colab_login_code_desc}
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  className="mono"
                  value={authCode}
                  disabled={busy}
                  onChange={(e) => setAuthCode(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && authCode.trim() && !busy) {
                      onSubmitAuthCode(authCode.trim());
                      setAuthCode("");
                    }
                  }}
                  placeholder={t.colab_login_code_placeholder}
                  style={{ flex: 1, minWidth: 0, padding: "8px 10px", borderRadius: 6 }}
                />
                <button
                  className="btn primary"
                  disabled={busy || !authCode.trim()}
                  onClick={() => {
                    onSubmitAuthCode(authCode.trim());
                    setAuthCode("");
                  }}
                >
                  {t.colab_login_code_btn}
                </button>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void tryClipboardCode(true)}
                >
                  {t.colab_login_code_paste_btn}
                </button>
                <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                  {t.colab_login_code_auto_hint}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {BOOTSTRAP_STATES.includes(status.state) && (
        <ColabBootstrapProgress state={status.state} startedAt={status.session_started_at} />
      )}

      {status.quota_message && !BOOTSTRAP_STATES.includes(status.state) && (
        <div
          style={{
            marginTop: 10,
            fontSize: 12,
            color: status.quota_state === "exhausted" ? "var(--err)" : "var(--text-secondary)",
          }}
        >
          {status.quota_message}
        </div>
      )}

      {(hasError || status.error) && status.error && (
        <div
          className="banner err"
          style={{ marginTop: 12, fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}
        >
          {status.error}
        </div>
      )}

      {setupMessage && (
        <div className="banner info" style={{ marginTop: 12, fontSize: 12 }}>
          {setupMessage}
        </div>
      )}

      {needsSetup && (
        <div
          style={{
            marginTop: 12,
            padding: 12,
            background: "var(--surface-muted)",
            borderRadius: 8,
            fontSize: 13,
          }}
        >
          {!status.wsl_available ? (
            <>
              <p style={{ margin: "0 0 6px", fontWeight: 600 }}>{t.colab_wsl_title}</p>
              <p style={{ margin: "0 0 10px", color: "var(--text-secondary)" }}>{t.colab_wsl_desc}</p>
              <code
                className="mono"
                style={{
                  display: "block",
                  padding: "6px 8px",
                  background: "var(--canvas)",
                  borderRadius: 4,
                  fontSize: 12,
                  marginBottom: 8,
                }}
              >
                {t.colab_wsl_cmd}
              </code>
              <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: 12 }}>
                {t.colab_wsl_restart}{" "}
                <strong>{t.colab_btn_install}</strong>.
              </p>
            </>
          ) : !status.cli_installed ? (
            <>
              <p style={{ margin: "0 0 6px", fontWeight: 600 }}>
                {t.colab_cli_title} ({status.wsl_distro})
              </p>
              <p style={{ margin: "0 0 8px", color: "var(--text-secondary)" }}>
                {t.colab_cli_desc}{" "}
                <strong>{t.colab_btn_install}</strong>{" "}
                {t.colab_cli_desc2}
              </p>
            </>
          ) : null}
        </div>
      )}

      <div className="actions" style={{ marginTop: 16 }}>
        {needsSetup && (
          <button className="btn primary" disabled={busy} onClick={onSetup}>
            {busy
              ? t.colab_btn_installing
              : !status.distro_available
                ? t.colab_btn_install_wsl
                : t.colab_btn_install}
          </button>
        )}
        {!needsSetup && !isLive && (
          <button className="btn primary" disabled={busy} onClick={onLaunch}>
            {busy ? t.colab_btn_launching : t.colab_btn_launch}
          </button>
        )}
        {isLive && (
          <button className="btn danger" disabled={busy} onClick={onStop}>
            {busy ? t.colab_btn_stopping : t.colab_btn_stop}
          </button>
        )}
        {(hasError || status.state === "disconnected") && !needsSetup && (
          <button className="btn" disabled={busy} onClick={onLaunch}>
            {t.retry}
          </button>
        )}
        {(isLive || isReady) && (
          <button className="btn" disabled={busy} onClick={onSwitchAccount}>
            {t.colab_btn_switch}
          </button>
        )}
        {status.state === "stopped" && status.cli_installed && (
          <button className="btn" onClick={onSwitchAccount}>
            {t.colab_btn_switch}
          </button>
        )}
      </div>

      {status.cli_installed && (
        <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)" }}>
          {t.colab_cli_info}{" "}
          <span className="mono">{status.cli_version || "google-colab-cli"}</span>{" · "}
          {t.colab_wsl_info}{" "}
          <span className="mono">{status.wsl_distro}</span>
        </div>
      )}
    </div>
  );
}

// ─── NgrokConfig ──────────────────────────────────────────────────────────────

/**
 * Optional ngrok tunnel configuration for the Colab runtime. When an authtoken is
 * present the bootstrap prefers ngrok over the zero-config Cloudflare quick tunnel
 * — ngrok keeps a persistent authenticated session (and, with a free reserved
 * domain, a URL that never rotates), which is far more stable for long renders.
 */
function NgrokConfig({
  isVI,
  configured,
  token,
  domain,
  saving,
  saved,
  onTokenChange,
  onDomainChange,
  onSave,
}: {
  isVI: boolean;
  configured: boolean;
  token: string;
  domain: string;
  saving: boolean;
  saved: boolean;
  onTokenChange: (v: string) => void;
  onDomainChange: (v: string) => void;
  onSave: () => void;
}) {
  return (
    <div
      style={{
        marginTop: 18,
        paddingTop: 16,
        borderTop: "1px solid var(--border-subtle)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>
          {isVI ? "Tunnel ổn định (ngrok)" : "Stable tunnel (ngrok)"}
        </h3>
        <span
          style={{
            fontSize: 11,
            fontWeight: 700,
            padding: "1px 8px",
            borderRadius: 999,
            background: configured ? "var(--success-soft)" : "var(--surface-muted)",
            color: configured ? "var(--ok)" : "var(--text-secondary)",
          }}
        >
          {configured ? (isVI ? "Đang dùng ngrok" : "Using ngrok") : (isVI ? "Đang dùng Cloudflare" : "Using Cloudflare")}
        </span>
      </div>
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
        {isVI
          ? "Tunnel Cloudflare mặc định (không cần cấu hình) hay tự rớt giữa render. Dán authtoken ngrok để có tunnel ổn định hơn nhiều. Thêm tên miền cố định (miễn phí 1 cái/tài khoản) để URL không bao giờ đổi."
          : "The default zero-config Cloudflare tunnel often drops mid-render. Paste an ngrok authtoken for a much more stable tunnel. Add a reserved domain (1 free per account) so the URL never changes."}
      </p>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <label style={{ fontSize: 12, fontWeight: 600 }}>
          {isVI ? "Ngrok authtoken" : "Ngrok authtoken"}
        </label>
        <button
          className="btn-link"
          style={{ fontSize: 11 }}
          onClick={() => void openUrl("https://dashboard.ngrok.com/get-started/your-authtoken")}
        >
          {isVI ? "Lấy token →" : "Get token →"}
        </button>
      </div>
      <input
        type="password"
        value={token}
        autoComplete="off"
        placeholder={configured ? (isVI ? "•••••••• (đã lưu — để trống nếu giữ nguyên)" : "•••••••• (saved — leave blank to keep)") : "2..."}
        onChange={(e) => onTokenChange(e.target.value)}
        style={{ width: "100%", marginBottom: 10 }}
      />

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <label style={{ fontSize: 12, fontWeight: 600 }}>
          {isVI ? "Tên miền cố định (tuỳ chọn)" : "Reserved domain (optional)"}
        </label>
        <button
          className="btn-link"
          style={{ fontSize: 11 }}
          onClick={() => void openUrl("https://dashboard.ngrok.com/cloud-edge/domains")}
        >
          {isVI ? "Tạo tên miền →" : "Create domain →"}
        </button>
      </div>
      <input
        type="text"
        value={domain}
        autoComplete="off"
        placeholder="my-app.ngrok-free.app"
        onChange={(e) => onDomainChange(e.target.value)}
        style={{ width: "100%", marginBottom: 12 }}
      />

      <div className="actions" style={{ alignItems: "center", gap: 10 }}>
        <button className="btn primary" disabled={saving} onClick={onSave}>
          {saving ? (isVI ? "Đang lưu…" : "Saving…") : (isVI ? "Lưu tunnel" : "Save tunnel")}
        </button>
        {saved && (
          <span style={{ fontSize: 12, color: "var(--ok)" }}>
            {isVI ? "Đã lưu. Khởi động lại Colab để áp dụng." : "Saved. Relaunch Colab to apply."}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── UploadButton ─────────────────────────────────────────────────────────────

function UploadButton({
  accept,
  label,
  hint,
  uploading,
  onUpload,
}: {
  accept: string;
  label: string;
  hint: string;
  uploading: boolean;
  onUpload: (file: File) => void;
}) {
  const { t } = useT();
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8 }}>
      <input
        ref={ref}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onUpload(file);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        className="btn sm"
        disabled={uploading}
        onClick={() => ref.current?.click()}
      >
        {uploading ? t.uploading : label}
      </button>
      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{hint}</span>
    </div>
  );
}

// ─── VoiceDesignField ────────────────────────────────────────────────────────

function VoiceDesignField({
  instruction,
  onChange,
  t,
}: {
  instruction: string;
  onChange: (value: string) => void;
  t: ReturnType<typeof useT>["t"];
}) {
  const parsed = useMemo(() => parseInstruct(instruction), [instruction]);

  function update(key: "gender" | "age" | "pitch" | "style", value: string) {
    onChange(buildInstruct({ ...parsed, [key]: value }));
  }

  return (
    <Field label={t.profile_instruction} hint={t.profile_instruction_hint}>
      <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
        <div style={{ flex: "1 1 120px" }}>
          <select value={parsed.gender} onChange={(e) => update("gender", e.target.value)}>
            <option value="">{t.voice_instruct_auto} — {t.voice_instruct_gender}</option>
            {INSTRUCT_GENDER.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
        <div style={{ flex: "1 1 120px" }}>
          <select value={parsed.age} onChange={(e) => update("age", e.target.value)}>
            <option value="">{t.voice_instruct_auto} — {t.voice_instruct_age}</option>
            {INSTRUCT_AGE.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
        <div style={{ flex: "1 1 120px" }}>
          <select value={parsed.pitch} onChange={(e) => update("pitch", e.target.value)}>
            <option value="">{t.voice_instruct_auto} — {t.voice_instruct_pitch}</option>
            {INSTRUCT_PITCH.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
        <div style={{ flex: "1 1 120px" }}>
          <select value={parsed.style} onChange={(e) => update("style", e.target.value)}>
            <option value="">{t.voice_instruct_auto} — {t.voice_instruct_style}</option>
            {INSTRUCT_STYLE.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
      </div>
      {instruction ? (
        <div className="hint" style={{ marginTop: 6, fontFamily: "monospace" }}>
          {t.voice_instruct_result}: {instruction}
        </div>
      ) : null}
    </Field>
  );
}

// ─── VoiceForm ───────────────────────────────────────────────────────────────

function WaveformTrimmer({
  src,
  sourcePath,
  duration,
  start,
  end,
  onDuration,
  onChange,
}: {
  src: string;
  sourcePath: string;
  duration: number;
  start: number;
  end: number;
  onDuration: (duration: number) => void;
  onChange: (start: number, end: number) => void;
}) {
  const { t } = useT();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const activeHandleRef = useRef<"start" | "end" | null>(null);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(start);
  const minimumSelection = Math.min(0.1, duration || 0.1);

  useEffect(() => {
    let cancelled = false;
    setPeaks([]);
    void (async () => {
      try {
        const waveform = await getVoiceReferenceWaveform(sourcePath);
        if (cancelled) return;
        setPeaks(waveform.peaks);
        onDuration(waveform.duration);
      } catch {
        if (!cancelled) setPeaks([0.12]);
      }
    })();
    return () => { cancelled = true; };
  }, [sourcePath, onDuration]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const track = trackRef.current;
    if (!canvas || !track) return;
    const draw = () => {
      const width = Math.max(1, track.clientWidth);
      const height = Math.max(1, track.clientHeight);
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.scale(ratio, ratio);
      context.clearRect(0, 0, width, height);
      const center = height / 2;
      const barWidth = Math.max(1, width / Math.max(1, peaks.length) - 1);
      const startRatio = duration > 0 ? start / duration : 0;
      const endRatio = duration > 0 ? end / duration : 1;
      peaks.forEach((peak, index) => {
        const x = (index / Math.max(1, peaks.length - 1)) * width;
        const selected = x / width >= startRatio && x / width <= endRatio;
        context.fillStyle = selected ? "#c85f28" : "#b7aca4";
        const barHeight = Math.max(3, peak * (height - 18));
        context.fillRect(x, center - barHeight / 2, barWidth, barHeight);
      });
      if (playing && duration > 0) {
        const x = Math.min(width, Math.max(0, currentTime / duration * width));
        context.fillStyle = "#171311";
        context.fillRect(x - 1, 5, 2, height - 10);
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(track);
    return () => observer.disconnect();
  }, [peaks, duration, start, end, currentTime, playing]);

  useEffect(() => {
    setCurrentTime(start);
    const audio = audioRef.current;
    if (audio && !playing) audio.currentTime = start;
  }, [start, playing]);

  useEffect(() => () => audioRef.current?.pause(), []);

  function valueFromPointer(clientX: number): number {
    const bounds = trackRef.current?.getBoundingClientRect();
    if (!bounds || duration <= 0) return 0;
    return Math.max(0, Math.min(duration, (clientX - bounds.left) / bounds.width * duration));
  }

  function updateHandle(handle: "start" | "end", value: number) {
    if (handle === "start") {
      onChange(Math.min(Math.max(0, value), end - minimumSelection), end);
    } else {
      onChange(start, Math.max(start + minimumSelection, Math.min(duration, value)));
    }
  }

  function beginDrag(event: React.PointerEvent<HTMLDivElement>) {
    const value = valueFromPointer(event.clientX);
    const handle = Math.abs(value - start) <= Math.abs(value - end) ? "start" : "end";
    activeHandleRef.current = handle;
    event.currentTarget.setPointerCapture(event.pointerId);
    updateHandle(handle, value);
  }

  function drag(event: React.PointerEvent<HTMLDivElement>) {
    if (activeHandleRef.current) updateHandle(activeHandleRef.current, valueFromPointer(event.clientX));
  }

  function endDrag(event: React.PointerEvent<HTMLDivElement>) {
    activeHandleRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function moveWithKeyboard(handle: "start" | "end", event: React.KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    const step = event.shiftKey ? 1 : 0.1;
    updateHandle(handle, (handle === "start" ? start : end) + direction * step);
  }

  async function togglePlayback() {
    const audio = audioRef.current;
    if (!audio) return;
    if (playing) {
      audio.pause();
      setPlaying(false);
      return;
    }
    audio.currentTime = start;
    setCurrentTime(start);
    try {
      await audio.play();
      setPlaying(true);
    } catch {
      setPlaying(false);
    }
  }

  const startPercent = duration > 0 ? start / duration * 100 : 0;
  const endPercent = duration > 0 ? end / duration * 100 : 100;

  return (
    <div className="waveform-editor">
      <audio
        ref={audioRef}
        src={src}
        preload="auto"
        onLoadedMetadata={(event) => onDuration(event.currentTarget.duration)}
        onTimeUpdate={(event) => {
          const time = event.currentTarget.currentTime;
          if (time >= end) {
            event.currentTarget.pause();
            event.currentTarget.currentTime = start;
            setCurrentTime(start);
            setPlaying(false);
          } else {
            setCurrentTime(time);
          }
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
      />
      <div
        ref={trackRef}
        className="waveform-track"
        onPointerDown={beginDrag}
        onPointerMove={drag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <canvas ref={canvasRef} aria-hidden="true" />
        {!peaks.length ? <span className="waveform-loading">{t.profile_waveform_loading}</span> : null}
        <div
          className="waveform-selection"
          style={{ left: `${startPercent}%`, width: `${Math.max(0, endPercent - startPercent)}%` }}
        />
        <button
          type="button"
          className="waveform-handle start"
          style={{ left: `${startPercent}%` }}
          aria-label={t.profile_trim_start}
          onKeyDown={(event) => moveWithKeyboard("start", event)}
        />
        <button
          type="button"
          className="waveform-handle end"
          style={{ left: `${endPercent}%` }}
          aria-label={t.profile_trim_end}
          onKeyDown={(event) => moveWithKeyboard("end", event)}
        />
      </div>
      <div className="waveform-toolbar">
        <button
          type="button"
          className="btn icon sm"
          onClick={() => void togglePlayback()}
          title={playing ? t.profile_preview_pause : t.profile_preview_play}
          aria-label={playing ? t.profile_preview_pause : t.profile_preview_play}
        >
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <span>{formatMediaTime(playing ? currentTime : start)}</span>
        <span className="waveform-toolbar-spacer" />
        <strong>{formatMediaTime(start)} - {formatMediaTime(end)}</strong>
        <span>{formatMediaTime(end - start)}</span>
      </div>
      <div className={`voice-length-advice ${end - start < 5 || end - start > 10 ? "warn" : "ok"}`}>
        <Info size={14} />
        <span>{t.profile_trim_recommendation}</span>
      </div>
    </div>
  );
}

function VoiceForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: VoiceProfile;
  onSave: (profile: VoiceProfile) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useT();
  const [form, setForm] = useState<VoiceProfile>(initial);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [uploadingAudio, setUploadingAudio] = useState(false);
  const [uploadingText, setUploadingText] = useState(false);
  const [mediaUrl, setMediaUrl] = useState("");
  const [mediaPreviewUrl, setMediaPreviewUrl] = useState("");
  const [mediaSource, setMediaSource] = useState("");
  const [mediaFormat, setMediaFormat] = useState<"wav" | "mp3">("wav");
  const [mediaDuration, setMediaDuration] = useState(0);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [downloadingMedia, setDownloadingMedia] = useState(false);
  const [trimmingMedia, setTrimmingMedia] = useState(false);
  const isNew = !initial.id;

  function patch<K extends keyof VoiceProfile>(key: K, value: VoiceProfile[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleAudioUpload(file: File) {
    setUploadingAudio(true);
    setErr("");
    try {
      const result = await uploadVoiceReference(file);
      setReferenceMedia(result);
    } catch (e) {
      setErr(e instanceof Error ? e.message : t.upload_error);
    } finally {
      setUploadingAudio(false);
    }
  }

  function setReferenceMedia(media: VoiceReferenceMedia) {
    patch("reference_audio_path", media.path);
    patch("reference_audio_url", "");
    setMediaSource(media.path);
    setMediaPreviewUrl(outputUrl(media.url) ?? "");
    setMediaDuration(media.duration || 0);
    setTrimStart(0);
    setTrimEnd(media.duration || 0);
  }

  async function handleMediaDownload() {
    if (!mediaUrl.trim()) return;
    setDownloadingMedia(true);
    setErr("");
    try {
      setReferenceMedia(await downloadVoiceReference(mediaUrl.trim(), mediaFormat));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t.profile_media_download_error);
    } finally {
      setDownloadingMedia(false);
    }
  }

  async function handleTrim() {
    if (!mediaSource || trimEnd <= trimStart) return;
    setTrimmingMedia(true);
    setErr("");
    try {
      setReferenceMedia(await trimVoiceReference(mediaSource, trimStart, trimEnd, "wav"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t.profile_media_trim_error);
    } finally {
      setTrimmingMedia(false);
    }
  }

  const handleMediaDuration = useCallback((duration: number) => {
    if (!Number.isFinite(duration) || duration <= 0) return;
    setMediaDuration(duration);
    setTrimEnd((current) => current > 0 ? Math.min(current, duration) : duration);
  }, []);

  async function handleTextUpload(file: File) {
    setUploadingText(true);
    setErr("");
    try {
      const text = await file.text();
      patch("reference_text", text.trim());
    } catch (e) {
      setErr(e instanceof Error ? e.message : t.upload_error);
    } finally {
      setUploadingText(false);
    }
  }

  async function submit() {
    if (!form.id.trim()) { setErr(t.profile_error_id); return; }
    if (!form.name.trim()) { setErr(t.profile_error_name); return; }
    setSaving(true);
    setErr("");
    try {
      await onSave(form);
    } catch (e) {
      setErr(e instanceof Error ? e.message : t.profile_error_save);
      setSaving(false);
    }
  }

  return (
    <div className="voice-form card" style={{ marginTop: 12 }}>
      <h3 style={{ marginTop: 0, marginBottom: 12 }}>
        {isNew ? t.profile_form_new : `${t.profile_form_edit} ${initial.name}`}
      </h3>
      {err ? <div className="banner err">{err}</div> : null}

      <div className="row">
        <Field label={t.profile_id}>
          <input
            type="text"
            value={form.id}
            disabled={!isNew}
            onChange={(e) => patch("id", e.target.value)}
            placeholder="vd: my-voice-clone"
          />
        </Field>
        <Field label={t.profile_name}>
          <input
            type="text"
            value={form.name}
            onChange={(e) => patch("name", e.target.value)}
            placeholder="vd: Giọng tôi"
          />
        </Field>
      </div>

      <div className="row">
        <Field label={t.profile_locale}>
          <input
            type="text"
            value={form.locale}
            onChange={(e) => patch("locale", e.target.value)}
            placeholder="vi-VN"
          />
        </Field>
        <Field label={t.profile_language}>
          <input
            type="text"
            value={form.language}
            onChange={(e) => patch("language", e.target.value)}
            placeholder="Vietnamese"
          />
        </Field>
        <Field label={t.profile_type}>
          <select value={form.type} onChange={(e) => patch("type", e.target.value)}>
            <option value="Custom">Custom</option>
            <option value="Narration">Narration</option>
            <option value="Clone">Clone</option>
            <option value="Disabled">Disabled</option>
          </select>
        </Field>
      </div>

      <Field label={t.profile_desc}>
        <input
          type="text"
          value={form.description ?? ""}
          onChange={(e) => patch("description", e.target.value)}
          placeholder="Mô tả ngắn về giọng này"
        />
      </Field>

      <div className="card" style={{ background: "var(--surface-muted)", marginTop: 12 }}>
        <p style={{ margin: "0 0 8px", fontWeight: 600, fontSize: 13 }}>{t.profile_omnivoice}</p>

        <Field label={t.profile_mode}>
          <select
            value={form.omnivoice_mode ?? ""}
            onChange={(e) => patch("omnivoice_mode", e.target.value)}
          >
            <option value="">{t.profile_mode_default}</option>
            <option value="clone">{t.profile_mode_clone}</option>
            <option value="instruct">{t.profile_mode_instruct}</option>
            <option value="auto">{t.profile_mode_auto}</option>
            <option value="plain">{t.profile_mode_plain}</option>
          </select>
        </Field>

        <div className="voice-media-import">
          <Field label={t.profile_media_url} hint={t.profile_media_url_hint}>
            <div className="voice-media-import-row">
              <input
                type="url"
                value={mediaUrl}
                onChange={(e) => setMediaUrl(e.target.value)}
                placeholder="https://youtube.com/..."
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void handleMediaDownload();
                  }
                }}
              />
              <select
                value={mediaFormat}
                onChange={(e) => setMediaFormat(e.target.value as "wav" | "mp3")}
                aria-label={t.profile_media_format}
              >
                <option value="wav">WAV</option>
                <option value="mp3">MP3</option>
              </select>
              <button
                type="button"
                className="btn"
                disabled={downloadingMedia || !mediaUrl.trim()}
                onClick={() => void handleMediaDownload()}
                title={t.profile_media_download}
              >
                <Download size={16} />
                {downloadingMedia ? t.profile_media_downloading : t.profile_media_download}
              </button>
            </div>
          </Field>

          {mediaSource && mediaPreviewUrl ? (
            <div className="voice-audio-editor">
              <WaveformTrimmer
                src={mediaPreviewUrl}
                sourcePath={mediaSource}
                duration={mediaDuration}
                start={trimStart}
                end={trimEnd}
                onDuration={handleMediaDuration}
                onChange={(start, end) => {
                  setTrimStart(start);
                  setTrimEnd(end);
                }}
              />
              {mediaDuration > 0 ? (
                <>
                  <button
                    type="button"
                    className="btn sm"
                    disabled={trimmingMedia || trimEnd <= trimStart}
                    onClick={() => void handleTrim()}
                  >
                    <Scissors size={15} />
                    {trimmingMedia ? t.profile_trimming : t.profile_trim_apply}
                  </button>
                  <span className="hint" style={{ marginLeft: 8 }}>{t.profile_trim_hint}</span>
                </>
              ) : null}
            </div>
          ) : null}
        </div>

        <Field label={t.profile_ref_path} hint={t.profile_ref_path_hint}>
          <input
            type="text"
            value={form.reference_audio_path ?? ""}
            onChange={(e) => patch("reference_audio_path", e.target.value)}
            placeholder="storage/voice-references/..."
          />
          <UploadButton
            accept="audio/*,.wav,.mp3,.flac,.ogg,.m4a"
            label={t.profile_upload_audio}
            hint={t.profile_upload_audio_hint}
            uploading={uploadingAudio}
            onUpload={(f) => void handleAudioUpload(f)}
          />
        </Field>

        <Field label={t.profile_ref_text} hint={t.profile_ref_text_hint}>
          <textarea
            rows={2}
            value={form.reference_text ?? ""}
            onChange={(e) => patch("reference_text", e.target.value)}
            placeholder="Nội dung đọc trong file audio tham chiếu…"
          />
        </Field>

        <Field label={t.profile_ref_text_path}>
          <input
            type="text"
            value={form.reference_text_path ?? ""}
            onChange={(e) => patch("reference_text_path", e.target.value)}
            placeholder="storage/voice-references/..."
          />
          <UploadButton
            accept=".txt,text/plain"
            label={t.profile_upload_text}
            hint=".txt"
            uploading={uploadingText}
            onUpload={(f) => void handleTextUpload(f)}
          />
        </Field>

        <VoiceDesignField
          instruction={form.instruction ?? ""}
          onChange={(v) => patch("instruction", v)}
          t={t}
        />
      </div>

      <div className="actions" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={saving} onClick={() => void submit()}>
          {saving ? t.profile_saving : isNew ? t.profile_save : t.profile_update}
        </button>
        <button className="btn" onClick={onCancel}>{t.cancel}</button>
      </div>
    </div>
  );
}

function formatMediaTime(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const minutes = Math.floor(safe / 60);
  const remainder = safe - minutes * 60;
  return `${minutes}:${remainder.toFixed(1).padStart(4, "0")}`;
}

// ─── VoiceList ────────────────────────────────────────────────────────────────

function VoiceList({
  voices,
  onEdit,
  onDelete,
}: {
  voices: VoiceProfile[];
  onEdit: (v: VoiceProfile) => void;
  onDelete: (id: string) => void;
}) {
  const { t } = useT();
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  function playPreview(id: string) {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    if (playing === id) { setPlaying(null); return; }
    const audio = new Audio(voicePreviewUrl(id));
    audioRef.current = audio;
    setPlaying(id);
    audio.onended = () => setPlaying(null);
    audio.onerror = () => setPlaying(null);
    void audio.play();
  }

  const isBuiltIn = (v: VoiceProfile) => v.id === "none" || v.id.includes("Neural");

  return (
    <div className="voice-list">
      {voices.length === 0 && <p className="muted">{t.profiles_empty}</p>}
      {voices.map((v) => (
        <div
          key={v.id}
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            padding: "8px 0",
            borderBottom: "1px solid var(--border-subtle)",
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>
              {v.name}
              {v.omnivoice_mode ? (
                <span
                  className="mono"
                  style={{
                    marginLeft: 6, fontSize: 11,
                    color: "var(--primary-hover)",
                    background: "var(--primary-soft)",
                    borderRadius: 4, padding: "1px 5px",
                  }}
                >
                  {v.omnivoice_mode}
                </span>
              ) : null}
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              {v.locale} · {v.type}{v.description ? ` · ${v.description}` : ""}
            </div>
          </div>
          <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
            <button className="btn sm" title={t.listen} onClick={() => playPreview(v.id)} disabled={v.id === "none"}>
              {playing === v.id ? t.stop_play : t.play}
            </button>
            {!isBuiltIn(v) && (
              <>
                <button className="btn sm" onClick={() => onEdit(v)}>{t.edit}</button>
                {confirmDelete === v.id ? (
                  <>
                    <button className="btn sm danger" onClick={() => { setConfirmDelete(null); onDelete(v.id); }}>
                      {t.confirm_delete}
                    </button>
                    <button className="btn sm" onClick={() => setConfirmDelete(null)}>{t.cancel}</button>
                  </>
                ) : (
                  <button className="btn sm danger" onClick={() => setConfirmDelete(v.id)}>{t.delete}</button>
                )}
              </>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Main screen ─────────────────────────────────────────────────────────────

export default function VoiceConfigScreen({ onBack }: { onBack: () => void }) {
  const { t } = useT();
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [options, setOptions] = useState<RuntimeOptions | null>(null);
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [colab, setColab] = useState<OmniVoiceColabStatus | null>(null);
  const [omniKey, setOmniKey] = useState("");
  const [ngrokToken, setNgrokToken] = useState("");
  const [ngrokSaving, setNgrokSaving] = useState(false);
  const [ngrokSaved, setNgrokSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [editVoice, setEditVoice] = useState<VoiceProfile | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [section, setSection] = useState<"engine" | "colab" | "profiles">("engine");
  const [colabBusy, setColabBusy] = useState(false);
  const [wslSetupMessage, setWslSetupMessage] = useState("");
  const [selectedDefaultVoice, setSelectedDefaultVoice] = useState("");
  const [setup, setSetup] = useState<OmniVoiceLocalSetup | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const setupPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function reload() {
    try {
      const [s, o, v, c, ws, su] = await Promise.all([
        getRuntimeSettings(),
        getRuntimeOptions().catch(() => null),
        getVoiceOptions().catch(() => [] as VoiceProfile[]),
        getColabStatus().catch(() => null),
        getWorkspaceSettings().catch(() => null),
        getLocalOmniVoiceSetup().catch(() => null),
      ]);
      setSettings(s);
      setOptions(o);
      setVoices(v);
      if (c) setColab(c);
      if (su) setSetup(su);
      if (ws?.default_voice_id) setSelectedDefaultVoice(ws.default_voice_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : t.error_load);
    }
  }

  const refreshColab = useCallback(async () => {
    try {
      const c = await getColabStatus();
      setColab(c);
      if (c.api_url && c.state === "ready") {
        setSettings((prev) =>
          prev ? { ...prev, omnivoice_api_url: c.api_url, omnivoice_runtime: "colab" } : prev,
        );
      }
      return c;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => { void reload(); }, []);

  useEffect(() => {
    if (!colab) return;
    const needsPoll = POLLING_STATES.has(colab.state) || colab.state === "waiting_for_login";
    if (needsPoll) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(() => { void refreshColab(); }, 4000);
    } else {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    }
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [colab?.state, refreshColab]);

  useEffect(() => {
    if (section === "colab" && !colab) void refreshColab();
  }, [section, colab, refreshColab]);

  const refreshSetup = useCallback(async () => {
    try {
      const su = await getLocalOmniVoiceSetup();
      setSetup((prev) => {
        // On the busy → ready transition, refresh runtime options so the engine
        // status (CUDA, effective runtime) reflects the freshly installed venv.
        if (prev?.busy && !su.busy && su.state === "ready") {
          void getRuntimeOptions().then(setOptions).catch(() => {});
        }
        return su;
      });
      return su;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    if (setup?.busy) {
      if (setupPollRef.current) clearInterval(setupPollRef.current);
      setupPollRef.current = setInterval(() => { void refreshSetup(); }, 3000);
    } else if (setupPollRef.current) {
      clearInterval(setupPollRef.current);
      setupPollRef.current = null;
    }
    return () => {
      if (setupPollRef.current) { clearInterval(setupPollRef.current); setupPollRef.current = null; }
    };
  }, [setup?.busy, refreshSetup]);

  async function runSetup() {
    try {
      const su = await startLocalOmniVoiceSetup();
      setSetup(su);
      void refreshSetup();
    } catch (e) {
      setError(e instanceof Error ? e.message : t.error_load);
    }
  }

  function patch<K extends keyof RuntimeSettings>(key: K, value: RuntimeSettings[K]) {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function saveEngine() {
    if (!settings) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const payload: Partial<RuntimeSettings> = {
        tts_provider: settings.tts_provider,
        prefer_local_gpu: settings.prefer_local_gpu,
        omnivoice_runtime: settings.omnivoice_runtime,
        omnivoice_mode: settings.omnivoice_mode,
        omnivoice_instruct: settings.omnivoice_instruct,
        omnivoice_ref_audio_url: settings.omnivoice_ref_audio_url,
        omnivoice_ref_audio_path: settings.omnivoice_ref_audio_path,
        omnivoice_ref_text: settings.omnivoice_ref_text,
        omnivoice_ref_text_path: settings.omnivoice_ref_text_path,
      };
      if (omniKey.trim()) payload.omnivoice_api_key = omniKey.trim();
      const [updated] = await Promise.all([
        updateRuntimeSettings(payload),
        selectedDefaultVoice
          ? updateWorkspaceSettings({ default_voice_id: selectedDefaultVoice }).catch(() => undefined)
          : Promise.resolve(),
      ]);
      setSettings(updated);
      setOmniKey("");
      setSaved(true);
      setOptions(await getRuntimeOptions().catch(() => options));
    } catch (e) {
      setError(e instanceof Error ? e.message : t.error_save);
    } finally {
      setSaving(false);
    }
  }

  async function handleColabAction(action: () => Promise<OmniVoiceColabStatus>) {
    setColabBusy(true);
    try {
      const c = await action();
      setColab(c);
    } catch (e) {
      setError(e instanceof Error ? e.message : t.voice_action_error);
    } finally {
      setColabBusy(false);
    }
  }

  async function setupColabEnvironment() {
    if (!colab) return;
    setWslSetupMessage("");
    if (colab.distro_available) {
      await handleColabAction(setupColabCli);
      return;
    }

    setColabBusy(true);
    setError("");
    try {
      await invoke("install_wsl_ubuntu");
      const refreshed = await getColabStatus().catch(() => null);
      if (refreshed) setColab(refreshed);
      // Windows may report the distro before the optional WSL features are
      // usable. Always stop here so the required reboot can complete cleanly.
      setWslSetupMessage(t.colab_wsl_install_complete);
    } catch (e) {
      setError(e instanceof Error ? e.message : t.colab_wsl_install_failed);
    } finally {
      setColabBusy(false);
    }
  }

  async function saveNgrok() {
    if (!settings) return;
    setNgrokSaving(true);
    setNgrokSaved(false);
    setError("");
    try {
      const payload: Partial<RuntimeSettings> = { ngrok_domain: settings.ngrok_domain ?? "" };
      // Blank token = keep the stored one (the backend treats "" as unchanged).
      if (ngrokToken.trim()) payload.ngrok_authtoken = ngrokToken.trim();
      const updated = await updateRuntimeSettings(payload);
      setSettings(updated);
      setNgrokToken("");
      setNgrokSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : t.error_save);
    } finally {
      setNgrokSaving(false);
    }
  }

  async function handleSaveVoice(profile: VoiceProfile) {
    await createVoiceOption(profile);
    setEditVoice(null);
    setShowAddForm(false);
    const updated = await getVoiceOptions().catch(() => voices);
    setVoices(updated);
  }

  async function handleDeleteVoice(id: string) {
    try {
      await deleteVoiceOption(id);
      setVoices((prev) => prev.filter((v) => v.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : t.profile_delete_error);
    }
  }

  if (!settings) {
    return (
      <div className="page">
        {error ? <div className="banner err">{error}</div> : <p className="muted">{t.loading}</p>}
      </div>
    );
  }

  const gpuLabel =
    options && options.gpu_count > 0
      ? `${options.gpus[0]?.name} (${options.gpus[0]?.memory_gb} GB)`
      : t.gpu_not_found;

  const effectiveRuntime = options?.effective_tts_runtime ?? "—";
  const colabIsReady = colab?.state === "ready";
  const localSetupReady = setup?.state === "ready" || effectiveRuntime === "omnivoice_local";

  const setupTarget = setup?.target || (options && options.gpu_count > 0 ? "cu128" : "cpu");
  const setupTargetLabel =
    setupTarget === "cu128" ? t.setup_target_cu128
      : setupTarget === "mps" ? t.setup_target_mps
        : t.setup_target_cpu;
  const setupStateLabel =
    setup?.state === "installing_python" ? t.setup_state_installing_python
      : setup?.state === "creating_venv" ? t.setup_state_creating_venv
        : setup?.state === "installing_torch" ? t.setup_state_installing_torch
          : setup?.state === "installing_deps" ? t.setup_state_installing_deps
            : setup?.state === "verifying" ? t.setup_state_verifying
              : setup?.state === "ready" ? t.setup_state_ready
                : setup?.state === "error" ? t.setup_state_error
                  : "";

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: "6px 14px",
    border: "1px solid var(--border)",
    borderRadius: 6,
    background: active ? "var(--primary)" : "var(--surface)",
    color: active ? "#fff" : "var(--text-primary)",
    fontWeight: active ? 600 : 400,
    cursor: "pointer",
    fontSize: 13,
    display: "flex",
    alignItems: "center",
    gap: 6,
  });

  return (
    <div className="page">
      <style>{`
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
        .btn.danger { color: var(--err); border-color: color-mix(in srgb, var(--err) 40%, transparent); }
        .btn.danger:hover { background: color-mix(in srgb, var(--err) 12%, transparent); }
        .btn.sm { padding: 3px 8px; font-size: 12px; }
      `}</style>

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 4 }}>
        <button className="btn sm" onClick={onBack}>{t.back}</button>
        <h1 style={{ margin: 0 }}>{t.voice_config_title}</h1>
      </div>
      <p className="sub" style={{ marginBottom: 16 }}>{t.voice_config_sub}</p>

      {error ? <div className="banner err" style={{ marginBottom: 12 }}>{error}</div> : null}
      {saved ? <div className="banner ok" style={{ marginBottom: 12 }}>{t.saved}</div> : null}

      {/* Tab navigation */}
      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        <button style={tabStyle(section === "engine")} onClick={() => setSection("engine")}>
          {t.tab_engine}
        </button>
        <button style={tabStyle(section === "colab")} onClick={() => setSection("colab")}>
          {t.tab_colab}
          {colab && (
            <span
              style={{
                width: 8, height: 8, borderRadius: "50%",
                background: STATE_COLOR[colab.state] ?? "var(--text-secondary)",
                display: "inline-block",
              }}
            />
          )}
        </button>
        <button style={tabStyle(section === "profiles")} onClick={() => setSection("profiles")}>
          {t.tab_profiles} ({voices.length})
        </button>
      </div>

      {/* ── Engine section ── */}
      {section === "engine" && (
        <Section title={t.engine_title}>
          <div
            className="hint"
            style={{ marginBottom: 12, padding: "8px 10px", background: "var(--surface-muted)", borderRadius: 6 }}
          >
            {t.engine_using}{" "}
            <span className="mono" style={{ fontWeight: 600 }}>{effectiveRuntime}</span>
            {options && options.gpu_count > 0 ? (
              <span style={{ marginLeft: 8, color: "var(--ok)" }}>· {t.gpu_label} {gpuLabel}</span>
            ) : (
              <span style={{ marginLeft: 8, color: "var(--text-secondary)" }}>· {gpuLabel}</span>
            )}
            {colabIsReady && (
              <span style={{ marginLeft: 8, color: "var(--ok)" }}>· {t.engine_colab_ready} {colab?.gpu_name}</span>
            )}
          </div>

          {/* Local OmniVoice auto-provisioning (hardware-aware torch) */}
          <div
            style={{
              marginBottom: 14,
              padding: "12px 14px",
              border: "1px solid var(--border)",
              borderRadius: 8,
              background: "var(--surface)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>
                  {localSetupReady ? t.setup_ready_title : t.setup_title}
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                  {localSetupReady ? t.setup_ready_desc : t.setup_desc}
                </div>
              </div>
              <button
                className={localSetupReady ? "btn sm" : "btn primary"}
                disabled={setup?.busy}
                onClick={() => void runSetup()}
              >
                {setup?.busy
                  ? t.setup_btn_installing
                  : localSetupReady
                    ? t.setup_btn_reinstall
                    : t.setup_btn_install}
              </button>
            </div>

            <div style={{ marginTop: 10, fontSize: 13 }}>
              <span className="muted">
                {localSetupReady ? t.setup_installed_label : t.setup_target_label}: {" "}
              </span>
              <span style={{ fontWeight: 600 }}>{setupTargetLabel}</span>
            </div>

            {localSetupReady && setup?.state === "idle" ? (
              <div style={{ marginTop: 8, fontSize: 13, color: "var(--ok)" }}>
                {t.setup_state_ready}
              </div>
            ) : setup && setup.state !== "idle" && setupStateLabel && (
              <div style={{ marginTop: 8, fontSize: 13, display: "flex", alignItems: "center", gap: 8 }}>
                {setup.busy && (
                  <span
                    style={{
                      width: 8, height: 8, borderRadius: "50%",
                      background: "var(--warn)", animation: "pulse 1s infinite",
                    }}
                  />
                )}
                <span
                  style={{
                    color:
                      setup.state === "error" ? "var(--err)"
                        : setup.state === "ready" ? "var(--ok)"
                          : "var(--text-primary)",
                  }}
                >
                  {setupStateLabel}
                </span>
              </div>
            )}

            {setup?.state === "error" && setup.error && (
              <div className="banner err" style={{ marginTop: 8, fontSize: 12 }}>{setup.error}</div>
            )}

            {!localSetupReady && (
              <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>{t.setup_note}</div>
            )}

            {setup && (setup.busy || setup.state === "error") && setup.log_tail && (
              <pre
                style={{
                  marginTop: 8, maxHeight: 140, overflow: "auto", fontSize: 11,
                  background: "var(--surface-muted)", padding: 8, borderRadius: 6, whiteSpace: "pre-wrap",
                }}
              >
                {setup.log_tail}
              </pre>
            )}
          </div>

          <Field label={t.engine_tts_provider} hint={settings.tts_provider === "vieneu" ? t.tts_vieneu_hint : undefined}>
            <select value={settings.tts_provider} onChange={(e) => patch("tts_provider", e.target.value)}>
              <option value="omnivoice">{t.tts_omnivoice}</option>
              <option value="vieneu">{t.tts_vieneu}</option>
              <option value="edge">{t.tts_edge}</option>
            </select>
          </Field>

          <Field label={t.engine_runtime}>
            <select value={settings.omnivoice_runtime} onChange={(e) => patch("omnivoice_runtime", e.target.value)}>
              <option value="auto">{t.engine_runtime_auto}</option>
              <option value="local">{t.engine_runtime_local}</option>
              <option value="colab">{t.engine_runtime_colab}</option>
            </select>
          </Field>

          <label className="check field">
            <input
              type="checkbox"
              checked={settings.prefer_local_gpu}
              onChange={(e) => patch("prefer_local_gpu", e.target.checked)}
            />
            <span>{t.engine_prefer_local}</span>
          </label>

          <Field label={t.engine_api_key} hint={t.engine_api_key_hint}>
            <input
              type="password"
              value={omniKey}
              onChange={(e) => setOmniKey(e.target.value)}
              placeholder={t.engine_api_key_placeholder}
            />
          </Field>

          <div style={{ borderTop: "1px solid var(--border-subtle)", margin: "14px 0 10px" }} />

          <Field label={t.engine_defaults}>
            <select
              value={selectedDefaultVoice}
              onChange={(e) => {
                const id = e.target.value;
                setSelectedDefaultVoice(id);
                const profile = voices.find((v) => v.id === id);
                if (!profile) return;
                patch("omnivoice_mode", profile.omnivoice_mode ?? "auto");
                patch("omnivoice_instruct", profile.instruction ?? "");
                patch("omnivoice_ref_audio_url", profile.reference_audio_url ?? "");
                patch("omnivoice_ref_audio_path", profile.reference_audio_path ?? "");
                patch("omnivoice_ref_text", profile.reference_text ?? "");
                patch("omnivoice_ref_text_path", profile.reference_text_path ?? "");
              }}
            >
              <option value="">{t.engine_defaults_none}</option>
              {voices.filter((v) => v.id !== "none").map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} · {v.locale}
                </option>
              ))}
            </select>
          </Field>

          <div className="actions" style={{ marginTop: 16 }}>
            <button className="btn primary" disabled={saving} onClick={() => void saveEngine()}>
              {saving ? t.saving : t.save}
            </button>
          </div>
        </Section>
      )}

      {/* ── Colab section ── */}
      {section === "colab" && (
        <Section title={t.colab_title}>
          <p style={{ margin: "0 0 14px", fontSize: 13, color: "var(--text-secondary)" }}>
            {t.colab_sub}
          </p>
          <p style={{ margin: "0 0 14px", fontWeight: 600, fontSize: 13, color: "var(--warn)" }}>
            {t.colab_windows_only}
          </p>
          {colab ? (
            <ColabStatusPanel
              status={colab}
              busy={colabBusy}
              setupMessage={wslSetupMessage}
              onLaunch={() => void handleColabAction(launchColab)}
              onStop={() => void handleColabAction(stopColab)}
              onSetup={() => void setupColabEnvironment()}
              onSwitchAccount={() => void handleColabAction(switchColabAccount)}
              onSubmitAuthCode={(code) => void handleColabAction(() => submitColabAuthCode(code))}
            />
          ) : (
            <p className="muted">{t.colab_loading}</p>
          )}

          <NgrokConfig
            isVI={t.nav_features === "Tính năng"}
            configured={settings.ngrok_authtoken_configured}
            token={ngrokToken}
            domain={settings.ngrok_domain ?? ""}
            saving={ngrokSaving}
            saved={ngrokSaved}
            onTokenChange={(v) => { setNgrokToken(v); setNgrokSaved(false); }}
            onDomainChange={(v) => { patch("ngrok_domain", v); setNgrokSaved(false); }}
            onSave={() => void saveNgrok()}
          />
        </Section>
      )}

      {/* ── Voice profiles section ── */}
      {section === "profiles" && (
        <Section title={t.profiles_title}>
          <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--text-secondary)" }}>
            {t.profiles_sub}
          </p>

          <VoiceList
            voices={voices}
            onEdit={(v) => { setEditVoice(v); setShowAddForm(false); }}
            onDelete={(id) => void handleDeleteVoice(id)}
          />

          {editVoice && (
            <VoiceForm
              initial={editVoice}
              onSave={handleSaveVoice}
              onCancel={() => setEditVoice(null)}
            />
          )}

          {!showAddForm && !editVoice && (
            <button className="btn primary" style={{ marginTop: 14 }} onClick={() => setShowAddForm(true)}>
              {t.profiles_add}
            </button>
          )}

          {showAddForm && (
            <VoiceForm
              initial={emptyProfile()}
              onSave={handleSaveVoice}
              onCancel={() => setShowAddForm(false)}
            />
          )}
        </Section>
      )}
    </div>
  );
}
