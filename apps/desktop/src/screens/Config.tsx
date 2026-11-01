import { useEffect, useRef, useState } from "react";
import {
  getRuntimeOptions,
  getRuntimeSettings,
  getTranslationRouterStatus,
  getStorageUsage,
  getVoiceOptions,
  cleanupStorage,
  revealStorage,
  updateRuntimeSettings,
} from "../api";
import type { RuntimeOptions, RuntimeSettings, StorageUsage, TranslationRouterStatus, VoiceProfile } from "../types";
import { TARGET_LANGUAGES } from "../languages";
import { loadDefaults, saveDefaults } from "../App";
import { useT } from "../i18n";
import { openUrl } from "../lib/open-url";
import { invoke } from "@tauri-apps/api/core";

export default function ConfigScreen({ onSaved }: { onSaved: () => void }) {
  const { t, lang } = useT();
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [options, setOptions] = useState<RuntimeOptions | null>(null);
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [nvidiaKey, setNvidiaKey] = useState("");
  const [groqKey, setGroqKey] = useState("");
  const [routerKey, setRouterKey] = useState("");
  const [sonioxKey, setSonioxKey] = useState("");
  const [openrouterKey, setOpenrouterKey] = useState("");
  const [clearingKey, setClearingKey] = useState<"nvidia" | "groq" | "soniox" | null>(null);
  const [routerStatus, setRouterStatus] = useState<TranslationRouterStatus | null>(null);
  const [checkingRouter, setCheckingRouter] = useState(false);
  const [defaults, setDefaults] = useState(loadDefaults());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [storage, setStorage] = useState<StorageUsage | null>(null);
  const [storageError, setStorageError] = useState("");
  const [cleaning, setCleaning] = useState<"temporary" | "zerotts_cache" | null>(null);
  const [confirmCleanup, setConfirmCleanup] = useState<"temporary" | "zerotts_cache" | null>(null);
  const [storageNotice, setStorageNotice] = useState("");
  const [revealingStorage, setRevealingStorage] = useState(false);

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
        void getTranslationRouterStatus().then(setRouterStatus).catch(() => undefined);
        await loadStorageUsage();
      } catch (e) {
        setError(e instanceof Error ? e.message : t.error_load);
      }
    })();
  }, [t.error_load]);

  function patch<K extends keyof RuntimeSettings>(key: K, value: RuntimeSettings[K]) {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  const usingOpenRouter = settings?.translation_provider === "openrouter";

  async function loadStorageUsage() {
    setStorageError("");
    try {
      setStorage(await getStorageUsage());
    } catch (e) {
      setStorageError(e instanceof Error ? e.message : t.error_load);
    }
  }

  async function cleanStorage(target: "temporary" | "zerotts_cache") {
    setConfirmCleanup(null);
    setCleaning(target);
    setStorageNotice("");
    try {
      const result = await cleanupStorage(target);
      setStorage(result.usage);
      setStorageNotice(
        lang === "vi"
          ? `Đã giải phóng ${formatBytes(result.removed_bytes)}.`
          : `Freed ${formatBytes(result.removed_bytes)}.`,
      );
    } catch (e) {
      setStorageNotice(e instanceof Error ? e.message : lang === "vi" ? "Dọn dẹp thất bại." : "Cleanup failed.");
    } finally {
      setCleaning(null);
    }
  }

  async function openStorageFolder() {
    setRevealingStorage(true);
    setStorageNotice("");
    try {
      await revealStorage();
    } catch (e) {
      setStorageNotice(e instanceof Error ? e.message : lang === "vi" ? "Không thể mở thư mục lưu trữ." : "Could not open storage folder.");
    } finally {
      setRevealingStorage(false);
    }
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
        ...translationPayload(),
        ytdlp_cookies_browser: settings.ytdlp_cookies_browser,
        ytdlp_cookies_file: settings.ytdlp_cookies_file,
      };
      if (nvidiaKey.trim()) payload.nvidia_api_key = nvidiaKey.trim();
      if (groqKey.trim()) payload.groq_api_key = groqKey.trim();
      if (sonioxKey.trim()) payload.soniox_api_key = sonioxKey.trim();
      const updated = await updateRuntimeSettings(payload);
      setSettings(updated);
      setNvidiaKey("");
      setGroqKey("");
      setSonioxKey("");
      setRouterKey("");
      setOpenrouterKey("");
      setRouterStatus(await getTranslationRouterStatus().catch(() => null));
      saveDefaults(defaults);
      setSaved(true);
      setOptions(await getRuntimeOptions().catch(() => options));
    } catch (e) {
      setError(e instanceof Error ? e.message : t.error_save);
    } finally {
      setSaving(false);
    }
  }

  function translationPayload(): Partial<RuntimeSettings> {
    if (!settings) return {};
    const payload: Partial<RuntimeSettings> = {
      translation_provider: usingOpenRouter ? "openrouter" : "openai-compatible",
      local_translation_base_url: settings.local_translation_base_url,
      local_translation_model: settings.local_translation_model,
      openrouter_model: settings.openrouter_model,
    };
    if (routerKey.trim()) payload.local_translation_api_key = routerKey.trim();
    if (openrouterKey.trim()) payload.openrouter_api_key = openrouterKey.trim();
    return payload;
  }

  async function checkRouterConfiguration() {
    if (!settings) return;
    setCheckingRouter(true);
    setError("");
    try {
      const updated = await updateRuntimeSettings(translationPayload());
      setSettings(updated);
      setRouterKey("");
      setOpenrouterKey("");
      setRouterStatus(await getTranslationRouterStatus());
    } catch (e) {
      setError(e instanceof Error ? e.message : t.error_load);
    } finally {
      setCheckingRouter(false);
    }
  }

  async function clearSttKey(provider: "nvidia" | "groq" | "soniox") {
    setClearingKey(provider);
    setError("");
    setSaved(false);
    try {
      const updated = await updateRuntimeSettings({ [`${provider}_api_key`]: "" });
      setSettings(updated);
      if (provider === "nvidia") setNvidiaKey("");
      else if (provider === "groq") setGroqKey("");
      else setSonioxKey("");
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : t.error_save);
    } finally {
      setClearingKey(null);
    }
  }

  if (!settings) {
    return (
      <div className="page page-form">
        {error ? <div className="banner err">{error}</div> : <p className="muted">{t.loading}</p>}
      </div>
    );
  }

  const gpuLabel =
    options && options.gpu_count > 0
      ? `${options.gpus[0]?.name} (${options.gpus[0]?.memory_gb} GB)`
      : t.gpu_not_found;

  return (
    <div className="page page-form">
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
              {lang === "vi" ? "Đăng ký / Lấy key →" : "Sign up / Get key →"}
            </a>
          </div>
          <input
            type="password"
            value={nvidiaKey}
            onChange={(e) => setNvidiaKey(e.target.value)}
            placeholder={settings.nvidia_api_key_configured ? t.stt_key_configured : "nvapi-..."}
          />
          <div className="hint">{t.stt_key_hint}</div>
          {settings.nvidia_api_key_configured ? (
            <button
              type="button"
              className="btn secondary"
              disabled={clearingKey !== null}
              onClick={() => void clearSttKey("nvidia")}
              style={{ marginTop: 8 }}
            >
              {clearingKey === "nvidia"
                ? (lang === "vi" ? "Đang xóa…" : "Removing…")
                : (lang === "vi" ? "Xóa khóa NVIDIA đã lưu" : "Remove saved NVIDIA key")}
            </button>
          ) : null}
        </div>
        <div className="field">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <label style={{ margin: 0 }}>{t.groq_key_label}</label>
            <a
              href="https://console.groq.com/keys"
              onClick={(e) => { e.preventDefault(); void openUrl("https://console.groq.com/keys"); }}
              style={{ fontSize: 11, fontWeight: 600, color: "var(--primary-hover)", textDecoration: "none", whiteSpace: "nowrap", cursor: "pointer" }}
            >
              {lang === "vi" ? "Đăng ký / Lấy key →" : "Sign up / Get key →"}
            </a>
          </div>
          <input
            type="password"
            value={groqKey}
            onChange={(e) => setGroqKey(e.target.value)}
            placeholder={settings.groq_api_key_configured ? t.groq_key_configured : "gsk_..."}
          />
          <div className="hint">{t.groq_key_hint}</div>
          {settings.groq_api_key_configured ? (
            <button
              type="button"
              className="btn secondary"
              disabled={clearingKey !== null}
              onClick={() => void clearSttKey("groq")}
              style={{ marginTop: 8 }}
            >
              {clearingKey === "groq"
                ? (lang === "vi" ? "Đang xóa…" : "Removing…")
                : (lang === "vi" ? "Xóa khóa Groq đã lưu" : "Remove saved Groq key")}
            </button>
          ) : null}
        </div>
        <div className="field">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <label style={{ margin: 0 }}>Soniox API key</label>
            <a
              href="https://console.soniox.com"
              onClick={(e) => { e.preventDefault(); void openUrl("https://console.soniox.com"); }}
              style={{ fontSize: 11, fontWeight: 600, color: "var(--primary-hover)", textDecoration: "none", whiteSpace: "nowrap", cursor: "pointer" }}
            >
              {lang === "vi" ? "Đăng ký / Lấy key →" : "Sign up / Get key →"}
            </a>
          </div>
          <input
            type="password"
            value={sonioxKey}
            onChange={(e) => setSonioxKey(e.target.value)}
            placeholder={settings.soniox_api_key_configured ? (lang === "vi" ? "Đã lưu key — nhập để thay" : "Key saved — type to replace") : "Soniox API key"}
          />
          <div className="hint">
            {lang === "vi"
              ? "Khi có key, Soniox (stt-async-v5) được dùng đầu tiên; NVIDIA và Groq là dự phòng."
              : "When set, Soniox (stt-async-v5) runs first; NVIDIA and Groq become fallbacks."}
          </div>
          {settings.soniox_api_key_configured ? (
            <button
              type="button"
              className="btn secondary"
              disabled={clearingKey !== null}
              onClick={() => void clearSttKey("soniox")}
              style={{ marginTop: 8 }}
            >
              {clearingKey === "soniox"
                ? (lang === "vi" ? "Đang xóa…" : "Removing…")
                : (lang === "vi" ? "Xóa khóa Soniox đã lưu" : "Remove saved Soniox key")}
            </button>
          ) : null}
        </div>
      </div>

      <div className="card">
        <h2>{lang === "vi" ? "Tải video" : "Video download"}</h2>
        <div className="field">
          <label>{lang === "vi" ? "Dùng cookie YouTube từ trình duyệt" : "Use YouTube cookies from browser"}</label>
          <select
            value={settings.ytdlp_cookies_browser}
            onChange={(e) => patch("ytdlp_cookies_browser", e.target.value)}
          >
            <option value="">{lang === "vi" ? "Không dùng" : "None"}</option>
            <option value="chrome">Chrome</option>
            <option value="brave">Brave</option>
            <option value="safari">Safari</option>
            <option value="firefox">Firefox</option>
            <option value="edge">Edge</option>
          </select>
          <div className="hint">
            {lang === "vi"
              ? "Bật khi YouTube báo \"Sign in to confirm you're not a bot\". Chọn trình duyệt đang đăng nhập YouTube. Trên macOS, lần đầu sẽ hiện hộp thoại Keychain — chọn \"Luôn cho phép\". Safari cần cấp Full Disk Access cho Terminal."
              : "Enable when YouTube says \"Sign in to confirm you're not a bot\". Pick a browser that is signed in to YouTube. On macOS, allow the Keychain prompt the first time. Safari needs Full Disk Access for Terminal."}
          </div>
        </div>
        <div className="field">
          <label>{lang === "vi" ? "Hoặc file cookies.txt (ưu tiên hơn trình duyệt)" : "Or a cookies.txt file (takes priority)"}</label>
          <input
            type="text"
            value={settings.ytdlp_cookies_file}
            onChange={(e) => patch("ytdlp_cookies_file", e.target.value)}
            placeholder="/Users/.../cookies.txt"
          />
        </div>
      </div>

      <div className="card">
        <h2>{t.translation_title}</h2>
        <div className="field">
          <label>{lang === "vi" ? "Nhà cung cấp dịch" : "Translation provider"}</label>
          <select
            value={usingOpenRouter ? "openrouter" : "openai-compatible"}
            onChange={(e) => {
              patch("translation_provider", e.target.value);
              setRouterStatus(null);
            }}
          >
            <option value="openai-compatible">{lang === "vi" ? "9router (cục bộ)" : "9router (local)"}</option>
            <option value="openrouter">OpenRouter (cloud)</option>
          </select>
        </div>
        {usingOpenRouter ? (
          <>
            <div className="field">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <label style={{ margin: 0 }}>OpenRouter API key</label>
                <a
                  href="https://openrouter.ai/keys"
                  onClick={(e) => { e.preventDefault(); void openUrl("https://openrouter.ai/keys"); }}
                  style={{ fontSize: 11, fontWeight: 600, color: "var(--primary-hover)", textDecoration: "none", whiteSpace: "nowrap", cursor: "pointer" }}
                >
                  {lang === "vi" ? "Đăng ký / Lấy key →" : "Sign up / Get key →"}
                </a>
              </div>
              <input
                type="password"
                value={openrouterKey}
                onChange={(e) => setOpenrouterKey(e.target.value)}
                placeholder={settings.openrouter_api_key_configured ? (lang === "vi" ? "Đã lưu key — nhập để thay" : "Key saved — type to replace") : "sk-or-..."}
              />
            </div>
            <div className="field">
              <label>{t.translation_model}</label>
              <input
                type="text"
                value={settings.openrouter_model}
                onChange={(e) => patch("openrouter_model", e.target.value)}
                placeholder="qwen/qwen3.7-flash"
              />
            </div>
          </>
        ) : (
        <>
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
          <label>API key 9router</label>
          <input
            type="password"
            value={routerKey}
            onChange={(e) => setRouterKey(e.target.value)}
            placeholder={
              settings.local_translation_api_key_configured
                ? lang === "vi" ? "Đã lưu key — nhập để thay" : "Key saved — type to replace"
                : lang === "vi" ? "sk-... (lấy trong dashboard 9router)" : "sk-... (from the 9router dashboard)"
            }
          />
          <div className="hint">
            {lang === "vi"
              ? "Chỉ cần khi 9router bật \"Require API key\". Sao chép key trong dashboard 9router."
              : "Only needed when 9router has \"Require API key\" enabled. Copy the key from the 9router dashboard."}
          </div>
        </div>
        <div className="field">
          <label>{t.translation_model}</label>
          {routerStatus?.models.length ? (
            <select
              value={settings.local_translation_model}
              onChange={(e) => patch("local_translation_model", e.target.value)}
            >
              {!routerStatus.models.includes(settings.local_translation_model) ? (
                <option value={settings.local_translation_model}>{settings.local_translation_model}</option>
              ) : null}
              {routerStatus.models.map((model) => <option key={model} value={model}>{model}</option>)}
            </select>
          ) : (
            <input
              type="text"
              value={settings.local_translation_model}
              onChange={(e) => patch("local_translation_model", e.target.value)}
              placeholder="provider/model-name"
            />
          )}
        </div>
        </>
        )}
        {routerStatus ? (
          <div className={`banner ${routerStatus.ready ? "ok" : "err"}`} style={{ marginBottom: 12 }}>
            {routerStatus.message}
          </div>
        ) : null}
        <div className="actions" style={{ justifyContent: "flex-start" }}>
          <button className="btn" disabled={checkingRouter} onClick={() => void checkRouterConfiguration()}>
            {checkingRouter
              ? lang === "vi" ? "Đang kiểm tra…" : "Checking…"
              : lang === "vi" ? "Lưu & kiểm tra kết nối" : "Save & test connection"}
          </button>
          {usingOpenRouter ? null : (
            <button className="btn" onClick={() => void openUrl(`${NINE_ROUTER_ENDPOINT}/dashboard`)}>
              {lang === "vi" ? "Mở dashboard 9router" : "Open 9router dashboard"}
            </button>
          )}
        </div>
      </div>

      <div className="card">
        <h2>{t.tts_title}</h2>
        <div className="field">
          <label>{t.tts_provider_label}</label>
          <select value={settings.tts_provider} onChange={(e) => patch("tts_provider", e.target.value)}>
            <optgroup label={t.tts_group_zerotts}>
              <option value="zerotts">{t.tts_zerotts}</option>
            </optgroup>
            <optgroup label={t.tts_group_omnivoice}>
              <option value="omnivoice">{t.tts_omnivoice}</option>
            </optgroup>
          </select>
          {settings.tts_provider === "zerotts" && (
            <div className="hint">{t.tts_zerotts_hint}</div>
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

      <div className="card">
        <h2>{lang === "vi" ? "Dung lượng & dữ liệu" : "Storage & data"}</h2>
        <p className="hint" style={{ marginTop: -4 }}>
          {lang === "vi"
            ? "Quản lý các file được tạo bởi VietDub. Xóa một dự án sẽ xóa cả file liên quan."
            : "Manage files generated by VietDub. Deleting a project also removes its associated files."}
        </p>
        {storage ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10, margin: "14px 0" }}>
            <StorageItem label={lang === "vi" ? "Dự án & media" : "Projects & media"} value={storage.project_data} />
            <StorageItem label="ZeroTTS cache" value={storage.zerotts_cache} />
            <StorageItem label={lang === "vi" ? "Giọng đã lưu" : "Saved voices"} value={storage.voice_data} />
            <StorageItem label={lang === "vi" ? "Tổng đang dùng" : "Total used"} value={storage.total} strong />
          </div>
        ) : storageError ? (
          <div className="banner err" style={{ marginBottom: 12 }}>
            {lang === "vi" ? `Không tải được dung lượng: ${storageError}` : `Could not load storage usage: ${storageError}`}
            {" "}<button className="link-btn" onClick={() => void loadStorageUsage()}>{lang === "vi" ? "Thử lại" : "Retry"}</button>
          </div>
        ) : <p className="muted">{lang === "vi" ? "Đang tải dung lượng…" : "Loading storage usage…"}</p>}
        {storageNotice ? <div className={`banner ${storageNotice.startsWith("Đã") || storageNotice.startsWith("Freed") ? "ok" : "err"}`} style={{ marginBottom: 12 }}>{storageNotice}</div> : null}
        {confirmCleanup ? (
          <div className="banner" style={{ marginBottom: 12 }}>
            {lang === "vi"
              ? confirmCleanup === "zerotts_cache"
                ? "Xóa cache mô hình ZeroTTS? Lần dùng sau sẽ phải tải lại model."
                : "Dọn video gốc đã tải và dữ liệu trung gian của các công việc đã kết thúc? Video đã xuất và file nguồn đã tải lên sẽ được giữ lại."
              : confirmCleanup === "zerotts_cache"
                ? "Delete the ZeroTTS model cache? The model will download again next time."
                : "Clean downloaded source videos and intermediate files for finished jobs? Rendered videos and uploaded source files will be kept."}
            <div className="actions" style={{ justifyContent: "flex-start", marginTop: 10 }}>
              <button className="btn danger" onClick={() => void cleanStorage(confirmCleanup)}>{lang === "vi" ? "Xác nhận" : "Confirm"}</button>
              <button className="btn" onClick={() => setConfirmCleanup(null)}>{lang === "vi" ? "Hủy" : "Cancel"}</button>
            </div>
          </div>
        ) : null}
        <div className="actions" style={{ justifyContent: "flex-start" }}>
          <button className="btn" disabled={cleaning !== null} onClick={() => setConfirmCleanup("temporary")}>
            {cleaning === "temporary" ? (lang === "vi" ? "Đang dọn…" : "Cleaning…") : (lang === "vi" ? "Dọn file tạm" : "Clean temporary files")}
          </button>
          <button className="btn danger" disabled={cleaning !== null} onClick={() => setConfirmCleanup("zerotts_cache")}>
            {cleaning === "zerotts_cache" ? (lang === "vi" ? "Đang xóa…" : "Deleting…") : (lang === "vi" ? "Xóa cache ZeroTTS" : "Delete ZeroTTS cache")}
          </button>
          <button className="btn" disabled={revealingStorage} onClick={() => void openStorageFolder()}>
            {revealingStorage ? (lang === "vi" ? "Đang mở…" : "Opening…") : (lang === "vi" ? "Mở thư mục lưu trữ" : "Open storage folder")}
          </button>
        </div>
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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[index]}`;
}

function StorageItem({ label, value, strong = false }: { label: string; value: number; strong?: boolean }) {
  return <div style={{ padding: 10, border: "1px solid var(--border)", borderRadius: 7, background: "var(--surface-muted)" }}>
    <div className="muted" style={{ fontSize: 12 }}>{label}</div>
    <div style={{ marginTop: 3, fontSize: 18, fontWeight: strong ? 700 : 600 }}>{formatBytes(value)}</div>
  </div>;
}

// ── 9router setup widget ──────────────────────────────────────────────────────

type NineRouterPhase =
  | "idle" | "checking" | "starting" | "installing" | "waiting" | "running" | "error";

const NINE_ROUTER_ENDPOINT = "http://localhost:20128";
const PHASE_LABEL: Record<NineRouterPhase, [vi: string, en: string]> = {
  idle:       ["", ""],
  checking:   ["Đang kiểm tra…", "Checking…"],
  starting:   ["Đang khởi động 9router…", "Starting 9router…"],
  installing: ["Đang cài đặt qua npm (1-2 phút)…", "Installing via npm (1-2 minutes)…"],
  waiting:    ["Chờ 9router khởi động…", "Waiting for 9router to start…"],
  running:    ["", ""],
  error:      ["", ""],
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
  const { lang } = useT();
  const L = (vi: string, en: string) => (lang === "vi" ? vi : en);
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
      setErrorMsg(
        L(
          "9router đã khởi động nhưng chưa phản hồi. Thử mở http://localhost:20128 thủ công.",
          "9router started but is not responding yet. Try opening http://localhost:20128 manually.",
        ),
      );
    }
  }

  const busy = phase === "checking" || phase === "starting" || phase === "installing" || phase === "waiting";

  return (
    <div style={{
      marginBottom: 14,
      padding: "10px 14px",
      borderRadius: 8,
      border: `1px solid ${phase === "running" ? "var(--success-border)" : phase === "error" ? "var(--danger-border)" : "var(--border)"}`,
      background: phase === "running" ? "var(--success-soft)" : phase === "error" ? "var(--danger-soft)" : "var(--surface-muted)",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          {phase === "running"
            ? L("9router đang chạy", "9router is running")
            : phase === "error"
            ? L("Lỗi cài đặt 9router", "9router setup failed")
            : L("9router — tự động cài đặt", "9router — automatic setup")}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          {phase === "running" && (
            <button
              className="btn"
              style={{ fontSize: 12, padding: "3px 10px" }}
              onClick={() => void openUrl(`${NINE_ROUTER_ENDPOINT}/dashboard`)}
            >
              {L("Mở dashboard →", "Open dashboard →")}
            </button>
          )}
          {(phase === "idle" || phase === "error") && (
            <button
              className="btn primary"
              style={{ fontSize: 12, padding: "3px 10px" }}
              onClick={() => void setup()}
            >
              {phase === "error" ? L("Thử lại", "Retry") : L("Cài đặt", "Set up")}
            </button>
          )}
          {busy && (
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{L(...PHASE_LABEL[phase])}</span>
          )}
        </div>
      </div>
      {phase === "running" && (
        <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--success)" }}>
          {L(
            "Endpoint đã được điền tự động. Vào dashboard để lấy API key và chọn model.",
            "The endpoint was filled in automatically. Open the dashboard to get an API key and pick a model.",
          )}
        </p>
      )}
      {errorMsg && (
        <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--danger)", whiteSpace: "pre-wrap" }}>{errorMsg}</p>
      )}
    </div>
  );
}
