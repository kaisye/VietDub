import { useEffect, useRef, useState } from "react";
import {
  getWatermarkSettings,
  putWatermarkSettings,
  uploadWatermarkImage,
  watermarkImageUrl,
  type WatermarkSettings,
} from "../api";

const POSITIONS: { value: WatermarkSettings["position"]; vi: string; en: string }[] = [
  { value: "top_left", vi: "Trên trái", en: "Top left" },
  { value: "top_right", vi: "Trên phải", en: "Top right" },
  { value: "bottom_left", vi: "Dưới trái", en: "Bottom left" },
  { value: "bottom_right", vi: "Dưới phải", en: "Bottom right" },
];

/** Settings → Watermark: a logo stamped on every rendered video. Saves on each change. */
export default function WatermarkSettingsCard({ lang }: { lang: string }) {
  const L = (vi: string, en: string) => (lang === "vi" ? vi : en);
  const [settings, setSettings] = useState<WatermarkSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(() => Date.now());
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    getWatermarkSettings()
      .then(setSettings)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const save = async (patch: Partial<WatermarkSettings>) => {
    if (!settings) return;
    setSettings({ ...settings, ...patch });
    try {
      setSettings(await putWatermarkSettings(patch));
      setError(null);
      if ("image" in patch) setVersion(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    try {
      setSettings(await uploadWatermarkImage(file));
      setVersion(Date.now());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="card">
      <h2>Watermark</h2>
      <p className="hint" style={{ marginTop: -4 }}>
        {L(
          "Logo gắn lên mọi video xuất ra (kể cả xuất từ Editor). Có thể tắt riêng từng video ở bước Xuất khi tạo video.",
          "A logo stamped on every rendered video (Editor renders too). Each video can skip it in its Output step.",
        )}
      </p>
      {error ? <div className="banner err" style={{ marginBottom: 12 }}>{error}</div> : null}
      {settings ? (
        <>
          <label className="check field">
            <input type="checkbox" checked={settings.enabled} onChange={(e) => void save({ enabled: e.target.checked })} />
            <span>{L("Gắn watermark lên video", "Add the watermark to videos")}</span>
          </label>

          <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", margin: "8px 0 14px" }}>
            <WatermarkPreview settings={settings} src={watermarkImageUrl(version)} />
            <div style={{ display: "grid", gap: 8 }}>
              <span className="hint">
                {settings.image ? L("Đang dùng logo tải lên.", "Using an uploaded logo.") : L("Đang dùng logo VietNote mặc định.", "Using the default VietNote logo.")}
              </span>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="btn secondary" onClick={() => fileRef.current?.click()}>
                  {L("Đổi logo…", "Change logo…")}
                </button>
                {settings.image ? (
                  <button className="btn secondary" onClick={() => void save({ image: null })}>
                    {L("Dùng lại logo VietNote", "Back to the VietNote logo")}
                  </button>
                ) : null}
              </div>
              <span className="hint">{L("Nên dùng PNG nền trong suốt.", "A transparent PNG works best.")}</span>
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                hidden
                onChange={(e) => {
                  void upload(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </div>
          </div>

          <div className="row">
            <div className="field">
              <label>{L("Vị trí", "Position")}</label>
              <select
                value={settings.position}
                onChange={(e) => void save({ position: e.target.value as WatermarkSettings["position"] })}
              >
                {POSITIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {L(item.vi, item.en)}
                  </option>
                ))}
              </select>
            </div>
            <RangeField
              label={L("Kích thước", "Size")}
              min={0.06}
              max={0.4}
              step={0.01}
              value={settings.size}
              format={(v) => `${Math.round(v * 100)}`}
              onCommit={(size) => void save({ size })}
            />
            <RangeField
              label={L("Độ rõ", "Opacity")}
              min={0.1}
              max={1}
              step={0.05}
              value={settings.opacity}
              format={(v) => `${Math.round(v * 100)}%`}
              onCommit={(opacity) => void save({ opacity })}
            />
          </div>
        </>
      ) : null}
    </div>
  );
}

/** A 16:9 frame with the logo in its corner, sized like the render (size × short side × 1.6). */
function WatermarkPreview({ settings, src }: { settings: WatermarkSettings; src: string }) {
  const width = 240;
  const height = 135;
  const logoWidth = settings.size * height * 1.6;
  const margin = settings.margin * height;
  const [vertical, horizontal] = settings.position.split("_");
  return (
    <div
      style={{
        position: "relative",
        width,
        height,
        borderRadius: 8,
        overflow: "hidden",
        background: "linear-gradient(135deg, #2b4562, #6a7f94 60%, #c9c3b6)",
        flex: "none",
      }}
    >
      <img
        src={src}
        alt=""
        style={{
          position: "absolute",
          width: logoWidth,
          opacity: settings.opacity,
          [vertical]: margin,
          [horizontal]: margin,
        }}
      />
    </div>
  );
}

function RangeField({
  label,
  min,
  max,
  step,
  value,
  format,
  onCommit,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  format: (value: number) => string;
  onCommit: (value: number) => void;
}) {
  const [live, setLive] = useState(value);
  useEffect(() => setLive(value), [value]);
  return (
    <div className="field">
      <label>
        {label} · {format(live)}
      </label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={live}
        onChange={(e) => setLive(Number(e.target.value))}
        onPointerUp={() => onCommit(live)}
        onKeyUp={() => onCommit(live)}
      />
    </div>
  );
}
