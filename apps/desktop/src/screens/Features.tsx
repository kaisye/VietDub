import { useEffect, useState } from "react";
import { getRuntimeOptions, getRuntimeSettings, updateRuntimeSettings } from "../api";
import type { RuntimeOptions, RuntimeSettings } from "../types";
import { useT } from "../i18n";

type Status = "ok" | "warn" | "off";

function Dot({ status }: { status: Status }) {
  const color = status === "ok" ? "#47785c" : status === "warn" ? "#81651f" : "#928a80";
  return (
    <span
      style={{
        display: "inline-block",
        width: 9,
        height: 9,
        borderRadius: "50%",
        background: color,
        flexShrink: 0,
        marginTop: 2,
      }}
    />
  );
}

function FeatureCard({
  title,
  status,
  badge,
  children,
}: {
  title: string;
  status: Status;
  badge?: string;
  children: React.ReactNode;
}) {
  const badgeColor =
    status === "ok"
      ? { bg: "var(--success-soft)", color: "var(--ok)" }
      : status === "warn"
      ? { bg: "var(--warning-soft)", color: "var(--warn)" }
      : { bg: "var(--danger-soft)", color: "var(--err)" };

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{title}</h2>
        {badge && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 800,
              padding: "2px 9px",
              borderRadius: 999,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              background: badgeColor.bg,
              color: badgeColor.color,
            }}
          >
            {badge}
          </span>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>{children}</div>
    </div>
  );
}

function Row({ label, value, status }: { label: string; value: string; status?: Status }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
      {status && <Dot status={status} />}
      <span style={{ fontSize: 12, color: "var(--muted)", minWidth: 130, flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>{value}</span>
    </div>
  );
}

function ControlRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", minWidth: 130, flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

function Toggle({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="check" style={{ cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.6 : 1 }}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span style={{ fontSize: 13 }}>{label}</span>
    </label>
  );
}

function Section({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
        gap: 14,
      }}
    >
      {children}
    </div>
  );
}

export default function FeaturesScreen({ onOpenVoiceSetup }: { onOpenVoiceSetup?: () => void }) {
  const { t } = useT();
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [options, setOptions] = useState<RuntimeOptions | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([getRuntimeSettings(), getRuntimeOptions()])
      .then(([s, o]) => {
        setSettings(s);
        setOptions(o);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Load failed"));
  }, []);

  async function applyPatch(partial: Partial<RuntimeSettings>) {
    setSaving(true);
    try {
      const updated = await updateRuntimeSettings(partial);
      setSettings(updated);
      // effective_tts_runtime depends on settings, so refresh options too.
      setOptions(await getRuntimeOptions());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (error) return <div className="page"><div className="banner err">{error}</div></div>;
  if (!settings || !options) return <div className="page"><p className="muted">{t.loading}</p></div>;

  // ── derived states ──────────────────────────────────────────────────────
  const hasCuda = options.torch.cuda_available;
  const hasGpu = options.gpu_count > 0;
  const gpuStatus: Status = hasCuda ? "ok" : hasGpu ? "warn" : "off";

  const ttsRuntime = options.effective_tts_runtime ?? settings.tts_provider;
  const isOmniVoice = ttsRuntime === "omnivoice" || ttsRuntime.startsWith("omnivoice_");
  const isZeroTTS = ttsRuntime === "zerotts";
  // OmniVoice (GPU) and ZeroTTS (offline CPU) are fully working states; Edge is the
  // always-available fallback default, hence "warn".
  const ttsStatus: Status = isOmniVoice || isZeroTTS ? "ok" : "warn";
  const ttsBadge = isOmniVoice ? "OmniVoice" : isZeroTTS ? "ZeroTTS" : "Edge TTS";

  const hasNvidiaKey = settings.nvidia_api_key_configured;
  // Translation is 9router-only now; NVIDIA NIM was removed as a translation backend.
  const translationModel =
    settings.local_translation_model || settings.local_translation_base_url || "translate";
  const transStatus: Status = "ok";

  // NVIDIA NIM (when a key is configured) now powers speech-to-text only.
  const nvidiaStatus: Status = hasNvidiaKey ? "ok" : "off";

  // ── labels ──────────────────────────────────────────────────────────────
  const isVI = t.nav_features === "Tính năng";

  const L = {
    active: isVI ? "Hoạt động" : "Active",
    fallback: isVI ? "Fallback" : "Fallback",
    inactive: isVI ? "Không hoạt động" : "Inactive",
    notConfigured: isVI ? "Chưa cấu hình" : "Not configured",
    available: isVI ? "Khả dụng" : "Available",
    unavailable: isVI ? "Không khả dụng" : "Unavailable",
    yes: isVI ? "Có" : "Yes",
    no: isVI ? "Không" : "No",
  };

  return (
    <div className="page">
      <h1>{t.nav_features}</h1>
      <p className="sub">
        {isVI
          ? "Trạng thái tích hợp và tính năng đang hoạt động. Chọn nhà cung cấp ngay tại đây."
          : "Status of integrations and active features. Pick providers right here."}
        {saving ? <span style={{ marginLeft: 8, color: "var(--primary)" }}>{isVI ? "Đang lưu…" : "Saving…"}</span> : null}
      </p>

      {/* GPU is optional. For machines without CUDA, present a friendly, hardware-aware
          note (not an alarming warning) — the CPU/cloud paths are fully supported. The
          raw torch/CUDA diagnostic is only meaningful when the user wants GPU OmniVoice. */}
      {!hasCuda && (
        <div className="banner info" style={{ marginBottom: 16 }}>
          {hasGpu ? (
            <>
              <div>
                {isVI ? (
                  <>
                    Máy có <strong>{options.gpus[0]?.name}</strong>, nhưng PyTorch hiện là bản CPU nên CUDA chưa bật.{" "}
                    <strong>Edge</strong> (giọng đám mây) vẫn dùng đầy đủ. Muốn chạy <strong>OmniVoice</strong> trên GPU,
                    cài đặt môi trường GPU (PyTorch bản CUDA) bằng nút bên dưới.
                  </>
                ) : (
                  <>
                    This machine has <strong>{options.gpus[0]?.name}</strong>, but PyTorch is a CPU-only build so CUDA is off.{" "}
                    <strong>Edge</strong> (cloud voices) still works fully. To run <strong>OmniVoice</strong> on the GPU,
                    set up the GPU environment (CUDA build of PyTorch) with the button below.
                  </>
                )}
              </div>
              {onOpenVoiceSetup && (
                <button className="btn primary" style={{ marginTop: 10 }} onClick={onOpenVoiceSetup}>
                  {isVI ? "Cài đặt GPU cho OmniVoice" : "Set up GPU for OmniVoice"}
                </button>
              )}
            </>
          ) : isVI ? (
            <>
              Máy này chưa có GPU/CUDA — <strong>không sao cả</strong>. Bạn vẫn dùng được{" "}
              <strong>Edge</strong> (giọng đám mây) hoặc <strong>ZeroTTS</strong> (giọng Việt local, CPU).
              GPU chỉ cần cho <strong>OmniVoice</strong> (clone / voice design).
            </>
          ) : (
            <>
              No GPU/CUDA on this machine — <strong>that's fine</strong>. You can still use{" "}
              <strong>Edge</strong> (cloud voices) or <strong>ZeroTTS</strong> (local Vietnamese on CPU).
              A GPU is only needed for <strong>OmniVoice</strong> (clone / voice design).
            </>
          )}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>

        {/* GPU */}
        <Section>
          <FeatureCard
            title={isVI ? "GPU & CUDA" : "GPU & CUDA"}
            status={gpuStatus}
            badge={hasCuda ? L.active : hasGpu ? "No CUDA" : L.unavailable}
          >
            <Row label="GPU" value={hasGpu ? `${options.gpu_count} GPU` : L.unavailable} status={hasGpu ? "ok" : "off"} />
            <Row label="CUDA" value={hasCuda ? L.available : L.unavailable} status={gpuStatus} />
            {options.gpus.map((gpu) => (
              <Row
                key={gpu.index}
                label={`GPU ${gpu.index}`}
                value={`${gpu.name} · ${gpu.memory_gb.toFixed(1)} GB`}
              />
            ))}
            {hasCuda && (
              <Row
                label={isVI ? "Tính năng mở khóa" : "Unlocked features"}
                value={isVI ? "OmniVoice local · Whisper nhanh hơn" : "OmniVoice local · Faster Whisper"}
                status="ok"
              />
            )}
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border-subtle)" }}>
              <Toggle
                checked={settings.prefer_local_gpu}
                disabled={saving || !hasGpu}
                onChange={(v) => void applyPatch({ prefer_local_gpu: v })}
                label={isVI ? "Ưu tiên dùng GPU cục bộ" : "Prefer local GPU"}
              />
            </div>
          </FeatureCard>

          {/* STT */}
          <FeatureCard
            title={isVI ? "Nhận dạng giọng nói (STT)" : "Speech-to-Text (STT)"}
            status="ok"
            badge={L.active}
          >
            <Row label="Engine" value="OpenAI Whisper" status="ok" />
            <Row
              label={isVI ? "Tăng tốc" : "Acceleration"}
              value={hasCuda ? "CUDA (GPU)" : isVI ? "CPU (chậm hơn)" : "CPU (slower)"}
              status={hasCuda ? "ok" : "warn"}
            />
            <Row
              label={isVI ? "Ngôn ngữ" : "Languages"}
              value={isVI ? "Tự động nhận diện" : "Auto-detect"}
              status="ok"
            />
          </FeatureCard>
        </Section>

        {/* TTS + Translation */}
        <Section>
          <FeatureCard
            title={isVI ? "Giọng đọc TTS" : "Text-to-Speech (TTS)"}
            status={ttsStatus}
            badge={ttsBadge}
          >
            <ControlRow label={isVI ? "Chọn engine" : "Select engine"}>
              <select
                value={settings.tts_provider}
                disabled={saving}
                onChange={(e) => void applyPatch({ tts_provider: e.target.value })}
              >
                <optgroup label={isVI ? "Edge TTS — chất lượng tương đối, không cần GPU" : "Edge TTS — decent quality, no GPU"}>
                  <option value="edge">Edge TTS ({isVI ? "đám mây" : "cloud"})</option>
                  <option value="zerotts">ZeroTTS ({isVI ? "Việt chất lượng cao · CPU" : "high-quality Vietnamese · CPU"})</option>
                </optgroup>
                <optgroup label={isVI ? "OmniVoice — chất lượng cao, cho phép clone, GPU/Colab (cần setup)" : "OmniVoice — high quality, clone, GPU/Colab (needs setup)"}>
                  <option value="omnivoice">OmniVoice ({isVI ? "GPU · nâng cao" : "GPU · advanced"})</option>
                </optgroup>
              </select>
            </ControlRow>
            <Row
              label={isVI ? "Đang chạy" : "Effective"}
              value={ttsRuntime}
              status={ttsStatus}
            />
            {isOmniVoice ? (
              <>
                <Row
                  label="Runtime"
                  value={settings.omnivoice_runtime}
                  status="ok"
                />
                <Row
                  label={isVI ? "Tính năng" : "Features"}
                  value={isVI ? "Voice Design · Clone · Instruct" : "Voice Design · Clone · Instruct"}
                  status="ok"
                />
              </>
            ) : isZeroTTS ? (
              <>
                <Row
                  label={isVI ? "Tính năng" : "Features"}
                  value={isVI ? "8 giọng tích hợp + community · CPU · không cần GPU" : "8 built-in + community voices · CPU · no GPU"}
                  status="ok"
                />
                <Row
                  label={isVI ? "Yêu cầu" : "Requirements"}
                  value={isVI ? "Không cần GPU/key · tải model lần đầu" : "No GPU/key · downloads model on first use"}
                  status="ok"
                />
              </>
            ) : (
              <>
                <Row
                  label={isVI ? "Tính năng" : "Features"}
                  value={isVI ? "Giọng Azure đa ngôn ngữ" : "Azure multilingual voices"}
                  status="ok"
                />
                <Row
                  label="OmniVoice"
                  value={isVI ? "Chưa kích hoạt — cấu hình trong Settings" : "Not active — configure in Settings"}
                  status="warn"
                />
              </>
            )}
          </FeatureCard>

          <FeatureCard
            title={isVI ? "Dịch thuật (LLM)" : "Translation (LLM)"}
            status={transStatus}
            badge="9router"
          >
            <Row
              label={isVI ? "Nhà cung cấp" : "Provider"}
              value={isVI ? "9router · LLM cục bộ" : "9router · local LLM"}
              status="ok"
            />
            <Row
              label="Base URL"
              value={settings.local_translation_base_url || "http://127.0.0.1:20128/v1"}
              status="ok"
            />
            <Row label="Model" value={translationModel} status={transStatus} />
            <Row
              label={isVI ? "Cấu hình" : "Configure"}
              value={isVI ? "Dashboard 9router · 127.0.0.1:20128" : "9router dashboard · 127.0.0.1:20128"}
              status="ok"
            />
          </FeatureCard>
        </Section>

        {/* NVIDIA NIM — speech-to-text only (translation moved to 9router) */}
        <Section>
          <FeatureCard
            title="NVIDIA NIM"
            status={nvidiaStatus}
            badge={hasNvidiaKey ? L.active : L.notConfigured}
          >
            <Row
              label="API Key"
              value={hasNvidiaKey ? L.active : L.notConfigured}
              status={nvidiaStatus}
            />
            <Row
              label={isVI ? "Nhận dạng giọng nói" : "Speech-to-Text"}
              value={
                hasNvidiaKey
                  ? isVI
                    ? "Phiên âm qua NVIDIA Riva"
                    : "Transcription via NVIDIA Riva"
                  : L.notConfigured
              }
              status={nvidiaStatus}
            />
            <Row
              label={isVI ? "Ghi chú" : "Note"}
              value={
                isVI
                  ? "Dịch thuật đã chuyển hẳn sang 9router"
                  : "Translation now runs through 9router"
              }
              status="ok"
            />
          </FeatureCard>
        </Section>

      </div>
    </div>
  );
}
