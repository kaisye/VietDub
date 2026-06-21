import type { SubtitleStyle } from "../types";

export const CREATE_DRAFT_VERSION = 3;
export const CREATE_DRAFT_STORAGE_KEY = "videodubbing.create-draft.v2";

// Default hard-subtitle OCR crop (center-based frame fractions): a wide band
// across the lower third where burned-in captions usually sit. Shared by the
// default draft, the config override, and the UI fallback for legacy drafts.
export const DEFAULT_OCR_REGION = { x: 0.5, y: 0.85, width: 0.72, height: 0.18 };

export type CreateMode = "quick_video";

export type CreateDraft = {
  schema_version: number;
  mode: CreateMode;
  updated_at: string;
  source: {
    method: "url" | "upload";
    url: string;
    upload_name: string;
    upload_path: string;
    title: string;
    platform: string;
    duration: string;
    resolution: string;
    subtitle_strategy: "auto" | "embedded" | "speech" | "ocr";
    // Hard-subtitle OCR crop, center-based frame fractions (same convention as
    // the subtitle blur box). Only used when subtitle_strategy === "ocr".
    ocr_region: { x: number; y: number; width: number; height: number };
    download_quality: "reliable" | "best";
    test_clip_seconds: number;
  };
  languages: {
    source: string;
    target: string;
  };
  voice: {
    voice_id: string;
    rate: number;
    instruction: string;
  };
  translation: {
    tone: string;
    context: string;
    glossary: string;
    proper_names: "preserve" | "transliterate" | "localize";
    remove_sound_tags: boolean;
    semantic_fidelity: "strict" | "balanced" | "natural";
  };
  subtitle: {
    style_id: string;
    style_overrides: Partial<SubtitleStyle>;
    cue_density: "compact" | "balanced" | "relaxed";
    max_lines: number;
    position: "bottom" | "middle" | "top";
  };
  audio: {
    original_volume: number;
    dubbed_volume: number;
    ducking: boolean;
  };
  workflow: {
    transcript_review: boolean;
    speaker_review: boolean;
    audio_review: boolean;
    final_review: boolean;
    auto_render: boolean;
  };
  output: {
    render_mode: "auto" | "manual";
    render_quality: "fast" | "balanced" | "quality";
    aspect_ratio: "source" | "16:9" | "9:16" | "1:1";
    platform: string;
    format: "mp4" | "webm";
  };
};

export type DraftValidation = Record<string, string>;

export function createDefaultDraft(mode: CreateMode = "quick_video"): CreateDraft {
  return {
    schema_version: CREATE_DRAFT_VERSION,
    mode,
    updated_at: new Date().toISOString(),
    source: {
      method: "url",
      url: "",
      upload_name: "",
      upload_path: "",
      title: "",
      platform: "Auto detect",
      duration: "Available after source selection",
      resolution: "Available after source selection",
      subtitle_strategy: "auto",
      ocr_region: { ...DEFAULT_OCR_REGION },
      download_quality: "reliable",
      test_clip_seconds: 0,
    },
    languages: { source: "auto", target: "VI" },
    voice: {
      voice_id: "vi-VN-HoaiMyNeural",
      rate: 0,
      instruction: "",
    },
    translation: {
      tone: "Natural and context-aware",
      context: "",
      glossary: "",
      proper_names: "preserve",
      remove_sound_tags: true,
      semantic_fidelity: "balanced",
    },
    subtitle: {
      style_id: "default",
      style_overrides: {},
      cue_density: "balanced",
      max_lines: 2,
      position: "bottom",
    },
    audio: {
      original_volume: 28,
      dubbed_volume: 100,
      ducking: true,
    },
    // One-click tool: no manual review gates, render automatically.
    workflow: {
      transcript_review: false,
      speaker_review: false,
      audio_review: false,
      final_review: false,
      auto_render: true,
    },
    output: {
      render_mode: "auto",
      render_quality: "balanced",
      aspect_ratio: "source",
      platform: "YouTube",
      format: "mp4",
    },
  };
}

export function loadCreateDraft(mode: CreateMode = "quick_video"): CreateDraft {
  if (typeof window === "undefined") return createDefaultDraft(mode);
  try {
    const raw = window.localStorage.getItem(CREATE_DRAFT_STORAGE_KEY);
    if (!raw) return createDefaultDraft(mode);
    const parsed = JSON.parse(raw) as CreateDraft;
    if (parsed.schema_version !== CREATE_DRAFT_VERSION || parsed.mode !== mode) {
      return createDefaultDraft(mode);
    }
    return parsed;
  } catch {
    return createDefaultDraft(mode);
  }
}

export function saveCreateDraft(draft: CreateDraft) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    CREATE_DRAFT_STORAGE_KEY,
    JSON.stringify({ ...draft, updated_at: new Date().toISOString() }),
  );
}

export function clearCreateDraft() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(CREATE_DRAFT_STORAGE_KEY);
}

export function hasSavedCreateDraft(mode: CreateMode = "quick_video") {
  if (typeof window === "undefined") return false;
  try {
    const raw = window.localStorage.getItem(CREATE_DRAFT_STORAGE_KEY);
    if (!raw) return false;
    const parsed = JSON.parse(raw) as CreateDraft;
    return parsed.schema_version === CREATE_DRAFT_VERSION && parsed.mode === mode;
  } catch {
    return false;
  }
}

export function applyDefaultVoice(draft: CreateDraft, voiceId: string): CreateDraft {
  return { ...draft, voice: { ...draft.voice, voice_id: voiceId } };
}

// Step order: 0 Source · 1 Voice · 2 Translation · 3 Subtitle · 4 Audio & Output · 5 Review
export function validateCreateStep(step: number, draft: CreateDraft): DraftValidation {
  const errors: DraftValidation = {};
  if (step === 0) {
    const hasSource =
      (draft.source.method === "url" && draft.source.url.trim()) ||
      (draft.source.method === "upload" && (draft.source.upload_path || draft.source.upload_name));
    if (!hasSource) errors.source = "Choose a video URL or upload a local file.";
    if (!draft.languages.target) errors.target = "Choose a target language.";
  }
  if (step === 1 && !draft.voice.voice_id) errors.voice = "Choose a voice.";
  if (step === 2 && !draft.translation.tone.trim()) {
    errors.tone = "Describe the intended translation tone.";
  }
  if (step === 3 && (draft.subtitle.max_lines < 1 || draft.subtitle.max_lines > 2)) {
    errors.subtitle = "Subtitle lines must be 1 or 2.";
  }
  return errors;
}

export function sourceDisplay(draft: CreateDraft) {
  if (draft.source.method === "url") return draft.source.title || draft.source.url || "No source selected";
  return draft.source.upload_name || "No file selected";
}

export function parseDraftGlossary(value: string) {
  const glossary: Record<string, string> = {};
  for (const line of value.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const separator = trimmed.includes("=") ? "=" : trimmed.includes(":") ? ":" : "";
    if (!separator) continue;
    const [source, ...targetParts] = trimmed.split(separator);
    const target = targetParts.join(separator).trim();
    if (source.trim() && target) glossary[source.trim()] = target;
  }
  return glossary;
}

export function createDraftOverrides(draft: CreateDraft) {
  return {
    source: {
      strategy: draft.source.method,
      subtitle_strategy: draft.source.subtitle_strategy,
      ocr_region: draft.source.ocr_region ?? DEFAULT_OCR_REGION,
      download_quality: draft.source.download_quality,
      test_clip_seconds: draft.source.test_clip_seconds,
    },
    languages: {
      source: draft.languages.source,
      target: draft.languages.target,
    },
    voice: {
      voice_id: draft.voice.voice_id,
      rate: draft.voice.rate,
      instruction: draft.voice.instruction,
    },
    translation: {
      tone: draft.translation.tone,
      context: draft.translation.context,
      glossary: parseDraftGlossary(draft.translation.glossary),
      proper_names: draft.translation.proper_names,
      remove_sound_tags: draft.translation.remove_sound_tags,
      semantic_fidelity: draft.translation.semantic_fidelity,
    },
    subtitle: {
      style_id: draft.subtitle.style_id,
      style_overrides: draft.subtitle.style_overrides ?? {},
      cue_density: draft.subtitle.cue_density,
      max_lines: draft.subtitle.max_lines,
      position: draft.subtitle.position,
    },
    audio: {
      original_volume: draft.audio.original_volume,
      dubbed_volume: draft.audio.dubbed_volume,
      ducking: draft.audio.ducking,
      normalize: true,
    },
    speakers: {
      enabled: false,
      min_count: null,
      max_count: null,
      unknown_voice_id: draft.voice.voice_id,
    },
    workflow: {
      ...draft.workflow,
    },
    output: {
      ...draft.output,
    },
    publish: {
      platforms: [draft.output.platform],
    },
  };
}
