import { useEffect, useRef, useState } from "react";
import {
  getRuntimeOptions,
  getRuntimeSettings,
  getVoiceOptions,
  updateRuntimeSettings,
} from "../api";
import type { RuntimeOptions, RuntimeSettings, VoiceProfile } from "../types";
import { TARGET_LANGUAGES } from "../languages";
import { loadDefaults, saveDefaults } from "../App";
import { useT } from "../i18n";
import { openUrl } from "../lib/open-url";
import { invoke } from "@tauri-apps/api/core";

export default function ConfigScreen({ onSaved }: { onSaved: () => void }) {
  const { t } = useT();
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [options, setOptions] = useState<RuntimeOptions | null>(null);
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [nvidiaKey, setNvidiaKey] = useState("");
  const [groqKey, setGroqKey] = useState("");
  const [defaults, setDefaults] = useState(loadDefaults());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [s, o, v] = await Promise.all([
          getRuntimeSettings(),
          getRuntimeOptions().catch(() => null),
          getVoiceOptions().catch(() => [] as VoiceProfile[]),
        ]);
        setSettings(s);
        setOptions(o);
        setVoices(v);
      } catch (e) {
        setError(e instanceof Error ? e.message : t.error_load);
      }
    })();
  }, [t.error_load]);

  function patch<K extends keyof RuntimeSettings>(key: K, value: RuntimeSettings[K]) {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function save() {
    if (!settings) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const payload: Partial<RuntimeSettings> = {
        tts_provider: settings.tts_provider,
        prefer_local_gpu: settings.prefer_local_gpu,
        prefer_existing_subtitles: settings.prefer_existing_subtitles,
        translation_provider: "openai-compatible",
        local_translation_base_url: settings.local_translation_base_url,
        local_translation_model: settings.local_translation_model,
      };
      if (nvidiaKey.trim()) payload.nvidia_api_key = nvidiaKey.trim();
      if (groqKey.trim()) payload.groq_api_key = groqKey.trim();
      const updated = await updateRuntimeSettings(payload);
      setSettings(updated);
      setNvidiaKey("");
      setGroqKey("");
      saveDefaults(defaults);
      setSaved(true);
      setOptions(await getRuntimeOptions().catch(() => options));
    } catch (e) {
      setError(e instanceof Error ? e.message : t.error_save);
    } finally {
      setSaving(false);
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

  return (
    <div className="page">
      <h1>{t.config_title}</h1>
      <p className="sub">{t.config_sub}</p>

      {error ? <div className="banner err">{error}</div> : null}
      {saved ? <div className="banner ok">{t.saved}</div> : null}

      <div className="card">
        <h2>{t.stt_title}</h2>
        <div className="field">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <label style={{ margin: 0 }}>{t.stt_key_label}</label>
            <a
              href="https://build.nvidia.com/settings/api-keys"
              onClick={(e) => { e.preventDefault(); void openUrl("https://build.nvidia.com/settings/api-keys"); }}
              style={{ fontSize: 11, fontWeight: 600, color: "var(--primary-hover)", textDecoration: "none", whiteSpace: "nowrap", cursor: "pointer" }}
            >
              Đăng ký / Lấy key →
            </a>
          </div>
          <input
            type="password"
            value={nvidiaKey}
            onChange={(e) => setNvidiaKey(e.target.value)}
            placeholder={settings.nvidia_api_key_configured ? t.stt_key_configured : "nvapi-..."}
          />
          <div className="hint">{t.stt_key_hint}</div>
        </div>
        <div className="field">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <label style={{ margin: 0 }}>{t.groq_key_label}</label>
            <a
              href="https://console.groq.com/keys"
              onClick={(e) => { e.preventDefault(); void openUrl("https://console.groq.com/keys"); }}
              style={{ fontSize: 11, fontWeight: 600, color: "var(--primary-hover)", textDecoration: "none", whiteSpace: "nowrap", cursor: "pointer" }}
            >
              Đăng ký / Lấy key →
            </a>
          </div>
          <input
            type="password"
            value={groqKey}
            onChange={(e) => setGroqKey(e.target.value)}
            placeholder={settings.groq_api_key_configured ? t.groq_key_configured : "gsk_..."}
          />
          <div className="hint">{t.groq_key_hint}</div>
        </div>
      </div>

      <div className="card">
        <h2>{t.translation_title}</h2>
        <NineRouterSetup
          onReady={() => {
            patch("local_translation_base_url", "http://localhost:20128/v1");
          }}
        />
        <div className="field">
          <label>{t.translation_endpoint}</label>
          <input
            type="text"
            value={settings.local_translation_base_url}
            onChange={(e) => patch("local_translation_base_url", e.target.value)}
            placeholder="http://127.0.0.1:20128/v1"
          />
        </div>
        <div className="field">
          <label>{t.translation_model}</label>
          <input
            type="text"
            value={settings.local_translation_model}
            onChange={(e) => patch("local_translation_model", e.target.value)}
            placeholder="translate"
          />
        </div>
      </div>

      <div className="card">
        <h2>{t.tts_title}</h2>
        <div className="field">
          <label>{t.tts_provider_label}</label>
          <select value={settings.tts_provider} onChange={(e) => patch("tts_provider", e.target.value)}>
            <option value="omnivoice">{t.tts_omnivoice}</option>
            <option value="vieneu">{t.tts_vieneu}</option>
            <option value="edge">{t.tts_edge}</option>
          </select>
          {settings.tts_provider === "edge" && (
            <div className="hint">
              Giọng đọc miễn phí của Microsoft — không cần GPU, luôn hoạt động. Chất lượng thấp hơn OmniVoice, không hỗ trợ clone giọng. Dùng làm dự phòng khi OmniVoice chưa sẵn sàng.
            </div>
          )}
          {settings.tts_provider === "vieneu" && (
            <div className="hint">{t.tts_vieneu_hint}</div>
          )}
        </div>
        <label className="check field">
          <input
            type="checkbox"
            checked={settings.prefer_local_gpu}
            onChange={(e) => patch("prefer_local_gpu", e.target.checked)}
          />
          <span>
            {t.tts_prefer_local}{" "}
            <span className="muted">
              {t.gpu_label} {gpuLabel}. {t.currently_using} {options?.effective_tts_runtime ?? "—"}.
            </span>
          </span>
        </label>
      </div>

      <div className="card">
        <h2>{t.defaults_title}</h2>
        <div className="row">
          <div className="field">
            <label>{t.target_language}</label>
            <select
              value={defaults.targetLanguage}
              onChange={(e) => setDefaults({ ...defaults, targetLanguage: e.target.value })}
            >
              {TARGET_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>{t.default_voice}</label>
            <select
              value={defaults.voice}
              onChange={(e) => setDefaults({ ...defaults, voice: e.target.value })}
            >
              <option value="">{t.default_voice_vn}</option>
              <option value="none">{t.voice_none}</option>
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} · {v.locale}
                </option>
              ))}
            </select>
          </div>
        </div>
        <label className="check field">
          <input
            type="checkbox"
            checked={settings.prefer_existing_subtitles}
            onChange={(e) => patch("prefer_existing_subtitles", e.target.checked)}
          />
          <span>{t.prefer_subtitles}</span>
        </label>
      </div>

      <div className="actions">
        <button className="btn primary" disabled={saving} onClick={() => void save()}>
          {saving ? t.saving : t.save}
        </button>
        <button className="btn" onClick={onSaved}>
          {t.done}
        </button>
      </div>
    </div>
  );
}

// ── 9router setup widget ──────────────────────────────────────────────────────

type NineRouterPhase =
  | "idle" | "checking" | "starting" | "installing" | "waiting" | "running" | "error";

const NINE_ROUTER_ENDPOINT = "http://localhost:20128";
const PHASE_LABEL: Record<NineRouterPhase, string> = {
  idle:       "",
  checking:   "Đang kiểm tra...",
  starting:   "Đang khởi động 9router...",
  installing: "Đang cài đặt qua npm (1-2 phút)...",
  waiting:    "Chờ 9router khởi động...",
  running:    "",
  error:      "",
};

async function ping9router(): Promise<boolean> {
  try {
    return await invoke<boolean>("status_9router");
  } catch {
    return false;
  }
}

async function waitFor9router(maxAttempts = 15): Promise<boolean> {
  for (let i = 0; i < maxAttempts; i++) {
    if (await ping9router()) return true;
    await new Promise<void>((r) => setTimeout(r, 2000));
  }
  return false;
}

function NineRouterSetup({ onReady }: { onReady: () => void }) {
  const [phase, setPhase] = useState<NineRouterPhase>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  // Auto-check on mount
  useEffect(() => {
    void (async () => {
      setPhase("checking");
      if (await ping9router()) {
        setPhase("running");
      } else {
        setPhase("idle");
      }
    })();
  }, []);

  async function setup() {
    setPhase("checking");
    setErrorMsg("");

    // Already running?
    if (await ping9router()) {
      setPhase("running");
      onReadyRef.current();
      void openUrl(`${NINE_ROUTER_ENDPOINT}/dashboard`);
      return;
    }

    // Try to start (already installed?)
    setPhase("starting");
    try { await invoke("start_9router"); } catch { /* not installed yet */ }

    setPhase("waiting");
    if (await waitFor9router(6)) {
      setPhase("running");
      onReadyRef.current();
      void openUrl(`${NINE_ROUTER_ENDPOINT}/dashboard`);
      return;
    }

    // Not installed — run npm install
    setPhase("installing");
    try {
      await invoke("install_9router");
    } catch (e) {
      setPhase("error");
      setErrorMsg(e instanceof Error ? e.message : String(e));
      return;
    }

    // Start after install
    setPhase("starting");
    try {
      await invoke("start_9router");
    } catch (e) {
      setPhase("error");
      setErrorMsg(e instanceof Error ? e.message : String(e));
      return;
    }

    setPhase("waiting");
    if (await waitFor9router(20)) {
      setPhase("running");
      onReadyRef.current();
      void openUrl(`${NINE_ROUTER_ENDPOINT}/dashboard`);
    } else {
      setPhase("error");
      setErrorMsg("9router đã khởi động nhưng chưa phản hồi. Thử mở http://localhost:20128 thủ công.");
    }
  }

  const busy = phase === "checking" || phase === "starting" || phase === "installing" || phase === "waiting";

  return (
    <div style={{
      marginBottom: 14,
      padding: "10px 14px",
      borderRadius: 8,
      border: `1px solid ${phase === "running" ? "#86efac" : phase === "error" ? "#fca5a5" : "var(--border)"}`,
      background: phase === "running" ? "#f0fdf4" : phase === "error" ? "#fff5f5" : "var(--surface-muted)",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          {phase === "running"
            ? "🟢 9router đang chạy"
            : phase === "error"
            ? "🔴 Lỗi cài đặt 9router"
            : "9router — tự động cài đặt"}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          {phase === "running" && (
            <button
              className="btn"
              style={{ fontSize: 12, padding: "3px 10px" }}
              onClick={() => void openUrl(`${NINE_ROUTER_ENDPOINT}/dashboard`)}
            >
              Mở dashboard →
            </button>
          )}
          {(phase === "idle" || phase === "error") && (
            <button
              className="btn primary"
              style={{ fontSize: 12, padding: "3px 10px" }}
              onClick={() => void setup()}
            >
              {phase === "error" ? "Thử lại" : "Setup"}
            </button>
          )}
          {busy && (
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>⏳ {PHASE_LABEL[phase]}</span>
          )}
        </div>
      </div>
      {phase === "running" && (
        <p style={{ margin: "6px 0 0", fontSize: 11, color: "#16a34a" }}>
          Endpoint đã được điền tự động. Vào dashboard để lấy API key và chọn model.
        </p>
      )}
      {errorMsg && (
        <p style={{ margin: "6px 0 0", fontSize: 12, color: "#dc2626", whiteSpace: "pre-wrap" }}>{errorMsg}</p>
      )}
    </div>
  );
}
