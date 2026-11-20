import { useEffect, useState } from "react";
import { createSubtitleStyle, getSubtitleStyles } from "../api";
import type { EditorBlur, EditorStyle, SubtitleStylePreset } from "../types";

type Patch = Partial<EditorStyle>;

/** Built-in looks. Each only sets appearance; position and font are kept. */
export const BUILTIN_STYLES: { id: string; vi: string; en: string; style: Patch }[] = [
  {
    id: "stroke",
    vi: "Trắng viền đen",
    en: "White, black stroke",
    style: { text_color: "#FFFFFF", outline: 2, outline_color: "#000000", shadow: 0, box_opacity: 0, bold: false },
  },
  {
    id: "dark-rounded",
    vi: "Nền tối bo góc",
    en: "Dark rounded box",
    style: {
      text_color: "#FFFFFF", outline: 0, shadow: 0, bold: false,
      box_style: "panel", box_color: "#000000", box_opacity: 0.6, box_radius: 0.35, box_padding: 0.35,
    },
  },
  {
    id: "glass",
    vi: "Kính mờ + viền",
    en: "Slate glass + stroke",
    style: {
      text_color: "#FFFFFF", outline: 1.5, outline_color: "#000000", shadow: 0, bold: false,
      box_style: "panel", box_color: "#1E2433", box_opacity: 0.7, box_radius: 0.5, box_padding: 0.4,
    },
  },
  {
    id: "yellow",
    vi: "Vàng đậm viền đen",
    en: "Bold yellow",
    style: { text_color: "#FFE14D", bold: true, outline: 2.5, outline_color: "#000000", shadow: 0, box_opacity: 0 },
  },
  {
    id: "light-card",
    vi: "Thẻ trắng chữ đen",
    en: "Light card",
    style: {
      text_color: "#111111", outline: 0, shadow: 0, bold: false,
      box_style: "panel", box_color: "#FFFFFF", box_opacity: 0.92, box_radius: 0.3, box_padding: 0.3,
    },
  },
  {
    id: "shadow",
    vi: "Bóng đổ nhẹ",
    en: "Soft shadow",
    style: { text_color: "#FFFFFF", outline: 0.8, outline_color: "#000000", shadow: 2, box_opacity: 0, bold: true },
  },
  {
    id: "black-bar",
    vi: "Thanh đen vuông",
    en: "Square black bar",
    style: {
      text_color: "#FFFFFF", outline: 0, shadow: 0, bold: false,
      box_style: "panel", box_color: "#000000", box_opacity: 0.8, box_radius: 0, box_padding: 0.25,
    },
  },
];

const FONTS = ["Arial", "Helvetica", "Helvetica Neue", "Verdana", "Tahoma", "Times New Roman", "Georgia"];

type BoxMode = "none" | "classic" | "panel";

function boxMode(style: EditorStyle): BoxMode {
  if (!style.box_opacity) return "none";
  return style.box_style === "panel" ? "panel" : "classic";
}

export default function StylePanel({
  style,
  blur,
  maxLines,
  onChange,
  onMaxLines,
  L,
}: {
  style: EditorStyle;
  blur: EditorBlur;
  maxLines: number;
  onChange: (patch: Patch) => void;
  onMaxLines: (value: number) => void;
  L: (vi: string, en: string) => string;
}) {
  const [saved, setSaved] = useState<SubtitleStylePreset[]>([]);
  const [saveName, setSaveName] = useState("");
  const [savedNote, setSavedNote] = useState<string | null>(null);

  useEffect(() => {
    getSubtitleStyles()
      .then((data) => setSaved(data.styles))
      .catch(() => undefined);
  }, []);

  const mode = boxMode(style);
  const setMode = (next: BoxMode) => {
    if (next === "none") onChange({ box_opacity: 0 });
    else onChange({ box_style: next, box_opacity: style.box_opacity || 0.6 });
  };

  const applySaved = (preset: SubtitleStylePreset) => {
    // Saved styles also carry blur settings; the blur region is edited separately.
    const patch: Patch = {};
    for (const [key, value] of Object.entries(preset.style)) {
      if (!key.startsWith("hard_sub_blur_") && key !== "alignment" && key !== "margin_v") {
        (patch as Record<string, unknown>)[key] = value;
      }
    }
    onChange(patch);
  };

  const saveAsStyle = async () => {
    const name = saveName.trim();
    if (!name) return;
    try {
      // Carry the blur region too, so a new video using this style also hides its hard subs.
      const blurFields = Object.fromEntries(
        Object.entries(blur).map(([key, value]) => [`hard_sub_blur_${key}`, value]),
      );
      const preset = await createSubtitleStyle({ name, style: { ...style, ...blurFields }, make_active: false });
      setSaved((items) => [...items.filter((item) => item.id !== preset.id), preset]);
      setSaveName("");
      setSavedNote(L(`Đã lưu style "${name}" — dùng được cho video mới.`, `Saved "${name}" — available for new videos.`));
    } catch (e) {
      setSavedNote(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="editor-style-panel">
      <span className="muted editor-hint">{L("Kéo phụ đề trên video để đổi vị trí dọc.", "Drag the subtitle on the video to move it.")}</span>

      <div className="editor-presets">
        {BUILTIN_STYLES.map((preset) => (
          <button key={preset.id} className="editor-preset" onClick={() => onChange(preset.style)}>
            <PresetSwatch style={{ ...style, ...preset.style }} />
            <span>{L(preset.vi, preset.en)}</span>
          </button>
        ))}
        {saved.map((preset) => (
          <button key={preset.id} className="editor-preset saved" onClick={() => applySaved(preset)}>
            <PresetSwatch style={{ ...style, ...preset.style }} />
            <span>{preset.name}</span>
          </button>
        ))}
      </div>

      <div className="editor-style-grid">
        <label>
          {L("Font", "Font")}
          <select value={style.font_name} onChange={(e) => onChange({ font_name: e.target.value })}>
            {[...new Set([style.font_name, ...FONTS])].map((font) => (
              <option key={font}>{font}</option>
            ))}
          </select>
        </label>
        <Slider label={L("Cỡ chữ", "Size")} min={10} max={72} step={1} value={style.font_size} onCommit={(font_size) => onChange({ font_size })} />
        <label className="editor-check">
          <input type="checkbox" checked={style.bold} onChange={(e) => onChange({ bold: e.target.checked })} />
          {L("Đậm", "Bold")}
        </label>
        <label>
          {L("Màu chữ", "Text")}
          <ColorInput value={style.text_color} onChange={(text_color) => onChange({ text_color })} />
        </label>
        <label>
          {L("Số dòng tối đa", "Max lines")}
          <select value={maxLines} onChange={(e) => onMaxLines(Number(e.target.value))}>
            {[1, 2, 3].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>

        <Slider label={L("Viền chữ", "Stroke")} min={0} max={8} step={0.25} value={style.outline} onCommit={(outline) => onChange({ outline })} disabled={mode === "classic"} />
        <label>
          {L("Màu viền", "Stroke colour")}
          <ColorInput value={style.outline_color} onChange={(outline_color) => onChange({ outline_color })} />
        </label>
        <Slider label={L("Bóng đổ", "Shadow")} min={0} max={8} step={1} value={style.shadow} onCommit={(shadow) => onChange({ shadow })} disabled={mode === "classic"} />

        <label>
          {L("Nền", "Box")}
          <select value={mode} onChange={(e) => setMode(e.target.value as BoxMode)}>
            <option value="none">{L("Không", "None")}</option>
            <option value="panel">{L("Khung bo góc", "Rounded panel")}</option>
            <option value="classic">{L("Nền liền (kiểu cũ)", "Classic box")}</option>
          </select>
        </label>
        {mode !== "none" ? (
          <>
            <label>
              {L("Màu nền", "Box colour")}
              <ColorInput value={style.box_color} onChange={(box_color) => onChange({ box_color })} />
            </label>
            <Slider label={L("Độ đậm nền", "Opacity")} min={0.05} max={1} step={0.05} value={style.box_opacity} onCommit={(box_opacity) => onChange({ box_opacity })} format={(v) => `${Math.round(v * 100)}%`} />
          </>
        ) : null}
        {mode === "panel" ? (
          <>
            <Slider label={L("Bo góc", "Radius")} min={0} max={1} step={0.05} value={style.box_radius ?? 0.35} onCommit={(box_radius) => onChange({ box_radius })} format={(v) => `${Math.round(v * 100)}%`} />
            <Slider label={L("Đệm", "Padding")} min={0} max={1.5} step={0.05} value={style.box_padding ?? 0.35} onCommit={(box_padding) => onChange({ box_padding })} format={(v) => `${Math.round(v * 100)}%`} />
          </>
        ) : null}
        {mode === "classic" ? (
          <span className="muted editor-hint">{L("Nền liền không có viền chữ/bóng; dùng Khung bo góc nếu cần cả hai.", "Classic box has no stroke/shadow; use Rounded panel for both.")}</span>
        ) : null}

        <label>
          {L("Chữ chạy theo giọng", "Words follow the voice")}
          <select
            value={style.karaoke_mode ?? "off"}
            onChange={(e) => onChange({ karaoke_mode: e.target.value as EditorStyle["karaoke_mode"] })}
          >
            <option value="off">{L("Tắt", "Off")}</option>
            <option value="highlight">{L("Tô màu chữ đang đọc", "Highlight the spoken word")}</option>
            <option value="reveal">{L("Hiện dần từng chữ", "Reveal word by word")}</option>
          </select>
        </label>
        {style.karaoke_mode === "highlight" ? (
          <label>
            {L("Màu chữ đang đọc", "Highlight colour")}
            <ColorInput value={style.karaoke_color ?? "#FFE14D"} onChange={(karaoke_color) => onChange({ karaoke_color })} />
          </label>
        ) : null}
      </div>

      <div className="editor-style-save">
        <input
          placeholder={L("Tên style để dùng lại…", "Name to reuse this style…")}
          value={saveName}
          onChange={(e) => setSaveName(e.target.value)}
        />
        <button className="btn sm" disabled={!saveName.trim()} onClick={() => void saveAsStyle()}>
          {L("Lưu thành style", "Save as style")}
        </button>
        {savedNote ? <span className="muted editor-hint">{savedNote}</span> : null}
      </div>
    </div>
  );
}

function PresetSwatch({ style }: { style: EditorStyle }) {
  const mode = boxMode(style);
  const stroke = mode === "classic" ? 0 : style.outline;
  return (
    <span className="editor-swatch">
      <span
        style={{
          color: style.text_color,
          fontWeight: style.bold ? 700 : 500,
          padding: mode === "none" ? 0 : "1px 5px",
          background: mode === "none" ? "transparent" : hexAlpha(style.box_color, style.box_opacity),
          borderRadius: mode === "panel" ? `${(style.box_radius ?? 0) * 9}px` : 0,
          WebkitTextStroke: stroke ? `${Math.min(2, stroke * 0.6)}px ${style.outline_color}` : undefined,
          textShadow: style.shadow && mode !== "classic" ? "1px 1px 1px rgba(0,0,0,0.6)" : undefined,
        }}
      >
        Aa
      </span>
    </span>
  );
}

export function hexAlpha(hex: string, opacity: number): string {
  const value = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16) || 0);
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

function ColorInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  // Pickers fire continuously while dragging; the editor debounces the save.
  return <input type="color" value={value.toLowerCase()} onChange={(e) => onChange(e.target.value.toUpperCase())} />;
}

function Slider({
  label,
  min,
  max,
  step,
  value,
  onCommit,
  disabled,
  format,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onCommit: (value: number) => void;
  disabled?: boolean;
  format?: (value: number) => string;
}) {
  const [live, setLive] = useState(value);
  useEffect(() => setLive(value), [value]);
  const commit = () => {
    if (live !== value) onCommit(live);
  };
  return (
    <label className={disabled ? "disabled" : undefined}>
      {label}
      <span className="editor-slider">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={live}
          disabled={disabled}
          onChange={(e) => setLive(Number(e.target.value))}
          onPointerUp={commit}
          onKeyUp={commit}
        />
        <span className="mono">{format ? format(live) : live}</span>
      </span>
    </label>
  );
}
