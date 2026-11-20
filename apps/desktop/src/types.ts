export type RuntimeSettings = {
  tts_provider: string;
  prefer_local_gpu: boolean;
  prefer_existing_subtitles: boolean;
  nvidia_api_key: string;
  nvidia_api_key_configured: boolean;
  groq_api_key: string;
  groq_api_key_configured: boolean;
  omnivoice_runtime: string;
  omnivoice_device: string;
  omnivoice_api_url: string;
  omnivoice_api_key: string;
  omnivoice_mode: string;
  omnivoice_instruct: string;
  omnivoice_ref_audio_url: string;
  omnivoice_ref_audio_path: string;
  omnivoice_ref_text: string;
  omnivoice_ref_text_path: string;
  translation_provider: string;
  translation_nvidia_model: string;
  local_translation_base_url: string;
  local_translation_model: string;
  local_translation_api_key: string;
  local_translation_api_key_configured?: boolean;
  soniox_api_key: string;
  soniox_api_key_configured: boolean;
  openrouter_api_key: string;
  openrouter_api_key_configured: boolean;
  openrouter_model: string;
  ytdlp_cookies_browser: string;
  ytdlp_cookies_file: string;
  ngrok_authtoken: string;
  ngrok_authtoken_configured: boolean;
  ngrok_domain: string;
};

export type TranslationRouterStatus = {
  ready: boolean;
  running: boolean;
  state: string;
  message: string;
  configured_model: string;
  models: string[];
};

export type GpuInfo = { index: number; name: string; memory_gb: number };

export type RuntimeOptions = {
  recommended_id: string;
  prefer_local_gpu: boolean;
  effective_tts_runtime: string;
  gpu_count: number;
  gpus: GpuInfo[];
  torch: { installed: boolean; cuda_available: boolean; warning?: string };
};

export type StorageUsage = {
  project_data: number;
  zerotts_cache: number;
  voice_data: number;
  logs: number;
  total: number;
};

export type VoiceProfile = {
  id: string;
  name: string;
  locale: string;
  language: string;
  type: string;
  description?: string;
  omnivoice_mode?: string;
  reference_audio_url?: string;
  reference_audio_path?: string;
  reference_text?: string;
  reference_text_path?: string;
  instruction?: string;
  engine?: string;
  removable?: boolean;
};

export type ZeroTTSCommunityVoice = {
  id: string;
  name: string;
  description: string;
  tags: string[];
  language: string;
  preview_url: string;
  installed: boolean;
  installed_voice_id: string;
};

export type WorkspaceSettings = {
  default_voice_id: string;
};

export type SubtitleStyle = {
  font_name: string;
  font_size: number;
  text_color: string;
  box_color: string;
  box_opacity: number;
  box_blur: number;
  /** classic = libass whole-cue box; panel = drawn box with rounded corners, text keeps its outline. */
  box_style?: "classic" | "panel";
  /** 0–1 fraction of half the box's short side (1 = pill). */
  box_radius?: number;
  /** 0–1.5 × font size. */
  box_padding?: number;
  outline_color: string;
  outline: number;
  shadow: number;
  alignment: "bottom_center" | "middle_center" | "top_center" | string;
  margin_v: number;
  position_x: number;
  position_y: number;
  bold: boolean;
  bilingual_enabled?: boolean;
  bilingual_font_scale?: number;
  bilingual_color?: string;
  /** Word-by-word subtitles: highlight = spoken word changes colour; reveal = words appear as spoken. */
  karaoke_mode?: "off" | "highlight" | "reveal";
  karaoke_color?: string;
  hard_sub_blur_enabled?: boolean;
  hard_sub_blur_x?: number;
  hard_sub_blur_y?: number;
  hard_sub_blur_width?: number;
  hard_sub_blur_height?: number;
  hard_sub_blur_style?: "box" | "vertical" | "horizontal";
  hard_sub_blur_strength?: number;
};

export type SubtitleStylePreset = {
  id: string;
  name: string;
  style: SubtitleStyle;
};

export type SubtitleStyles = {
  active_style_id: string;
  styles: SubtitleStylePreset[];
};

export type SourceVideoUpload = {
  filename: string;
  path: string;
  url: string;
};

export type QuickVideoJobInput = {
  source: {
    video_url: string;
    source_title?: string | null;
    thumbnail_url?: string | null;
    content?: string;
    platform?: string;
    overrides?: Record<string, unknown>;
  };
  preset_id?: string | null;
  preset_version?: number | null;
  overrides: Record<string, unknown>;
  run: boolean;
};

export type CreatedMediaJob = {
  video: {
    id: string;
    project_id?: string | null;
    video_url: string;
    content: string;
    platform: string;
  };
  job: Job;
};

export type Job = {
  id: string;
  video_url: string;
  source_title?: string | null;
  source_language: string;
  target_language: string;
  voice: string;
  burn_subtitles?: boolean;
  download_quality?: string;
  render_quality?: string;
  test_clip_seconds?: number;
  status: string;
  progress: number;
  current_step: string;
  step_detail?: string;
  output_url?: string | null;
  error_message?: string | null;
  logs?: string;
  created_at?: string;
  updated_at?: string;
  // Server-persisted processing window. started_at excludes queue wait; the UI
  // shows completed_at - started_at as a stable "completed in" duration.
  started_at?: string | null;
  completed_at?: string | null;
  thumbnail_url?: string | null;
  // Resolved production configuration snapshot (frozen at job creation). Used to
  // label pipeline steps by what the job actually does, not the global runtime.
  configuration_snapshot?: {
    source?: { subtitle_strategy?: string };
    [key: string]: unknown;
  } | null;
};

export type OmniVoiceColabStatus = {
  state:
    | "stopped"
    | "setup_required"
    | "installing"
    | "starting"
    | "waiting_for_login"
    | "waiting_for_gpu"
    | "loading"
    | "ready"
    | "disconnected"
    | "error"
    | string;
  reachable: boolean;
  api_url: string;
  device: string;
  gpu_name: string;
  gpu_memory_gb: number | null;
  quota_state: "available" | "exhausted" | "unknown" | string;
  quota_message: string;
  account_hint: string;
  account_email: string;
  account_name: string;
  account_picture: string;
  session_started_at: number | null;
  session_age_seconds: number | null;
  updated_at: number | null;
  error: string | null;
  model: string | null;
  authorization_url: string;
  needs_auth_code: boolean;
  wsl_available: boolean;
  distro_available: boolean;
  cli_installed: boolean;
  cli_version: string;
  wsl_distro: string;
  session_name: string;
  setup_command: string;
  log_path: string;
};

export type ColabAccount = {
  slug: string;
  email: string;
  name: string;
  picture: string;
  active: boolean;
  last_used_at: number | null;
};

export type ColabAccounts = {
  accounts: ColabAccount[];
  active_email: string;
};

export type OmniVoiceLocalSetup = {
  state:
    | "idle"
    | "creating_venv"
    | "installing_torch"
    | "installing_deps"
    | "verifying"
    | "ready"
    | "error"
    | string;
  busy: boolean;
  message: string;
  error: string;
  target: "cu128" | "cpu" | "mps" | string;
  venv_path: string;
  started_at: number | null;
  finished_at: number | null;
  log_tail: string;
};

export type JobOutput = {
  job_id: string;
  status: string;
  output_url?: string | null;
  error_message?: string | null;
};

export type NewJobInput = {
  video_url: string;
  target_language: string;
  voice: string;
  burn_subtitles: boolean;
  download_quality: string;
  render_quality: string;
};

export type EditorIssue = "empty" | "untranslated" | "no_audio" | "stale_audio" | "overrun" | "term";

/** A proper name / converted amount and the one spelling the dub uses (services/terms.py). */
export type Term = {
  target: string;
  /** Spellings as they appear in the source transcript. */
  source: string[];
  /** Wrong renderings that get corrected to `target`. */
  wrong: string[];
  kind: "person" | "brand" | "organization" | "place" | "product" | "number" | "other";
  note: string;
  origin: "user" | "dictionary" | "auto" | string;
};

/** A term with where the edited lines stand (computed server-side on every answer). */
export type TermOverview = Term & {
  uses: number;
  /** Lines whose source mentions the term but whose text lacks it. */
  missing_ids: string[];
  /** Wrong spellings found in the text, with the lines using them. */
  variants: { text: string; ids: string[] }[];
};

export type TermProposal = { id: string; before: string; after: string; how: "spelling" | "ai" };

export type EditorSegment = {
  id: string;
  start: number;
  end: number;
  text: string;
  source_text: string;
  audio_url: string | null;
  audio_start: number;
  audio_duration: number;
  audio_text: string;
  audio_generated_at?: string;
  /** Voice length the user set on the timeline; regenerated takes fit it. */
  voice_slot?: number;
  /** Natural length of the unfitted take behind audio_url, once known. */
  audio_source_duration?: number;
  previous_audio_url?: string | null;
  previous_audio_duration?: number;
  muted: boolean;
  issues: EditorIssue[];
  /** Burned-in layout: consecutive timed displays of at most max_lines lines. */
  display: { start: number; end: number; lines: string[] }[];
};

export type EditorBlur = {
  enabled: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
  style: "box" | "vertical" | "horizontal";
  strength: number;
};

export type EditorStyle = Omit<SubtitleStyle, "alignment" | "margin_v" | `hard_sub_blur_${string}`>;

export type EditorSubtitleLayout = EditorStyle & {
  font_px: number;
  chars_per_line: number;
  max_lines: number;
};

export type EditorRegion = {
  id: string;
  start: number;
  end: number;
  volume: number;
  /** Seconds to ramp from the base volume up to `volume` / back down. */
  fade_in: number;
  fade_out: number;
};

export type EditorAudio = {
  original_volume: number;
  dubbed_volume: number;
  /** Switches used by both the preview and the render. */
  dubbed_enabled: boolean;
  original_enabled: boolean;
};

export type EditorWorkspace = {
  job_id: string;
  title: string;
  source_language: string;
  video_url: string;
  duration: number;
  width: number;
  height: number;
  blur: EditorBlur;
  audio: EditorAudio;
  style: EditorStyle;
  max_lines: number;
  /** Original-audio volume overrides (percent) inside time ranges. */
  original_regions: EditorRegion[];
  /** Music / sound-effect tracks (always present in API answers; [] for older workspaces). */
  audio_tracks: EditorAudioTrack[];
  /** Text boxes drawn on the video (always present in API answers; [] for older workspaces). */
  text_overlays: EditorTextOverlay[];
  /** Derived server-side from style; never sent back. */
  subtitle: EditorSubtitleLayout;
  segments: EditorSegment[];
  /** Name/number sheet of this dub (absent from workspaces of older backends). */
  terms?: TermOverview[];
  render: { status: "idle" | "running" | "done" | "failed"; output_url?: string; error?: string };
  updated_at: string;
};

/**
 * Text box on the video for [start, end). (x, y) are fractions of the frame; `align` says which
 * point of the box sits there (left edge, centre, right edge; always vertically centred).
 * `size` is the font size as a fraction of the frame height. See services/text_overlays.py.
 */
export type EditorTextOverlay = {
  id: string;
  text: string;
  start: number;
  end: number;
  x: number;
  y: number;
  size: number;
  color: string;
  bg_color: string;
  bg_opacity: number;
  bold: boolean;
  align: "left" | "center" | "right";
  /** Empty: the subtitle font. */
  font_name: string;
};

// ── Editor audio tracks & music library ──
export type EditorDucking = {
  enabled: boolean;
  /** Gain under the voice, in dB (≤ 0). */
  amount_db: number;
  /** Seconds the music takes to go down before a line / come back up after it. */
  attack: number;
  release: number;
};

export type EditorAudioClip = {
  id: string;
  asset_id: string;
  /** Display name snapshot (kept if the asset is later deleted). */
  title?: string;
  /** Timeline position (s). */
  start: number;
  /** Offset into the file (s). */
  in: number;
  duration: number;
  /** Percent, 0–200. */
  volume: number;
  fade_in: number;
  fade_out: number;
  loop: boolean;
  ducking: EditorDucking;
};

export type EditorAudioTrack = {
  id: string;
  name: string;
  kind: "music" | "sfx";
  muted: boolean;
  locked: boolean;
  volume: number;
  clips: EditorAudioClip[];
};

export type MusicAsset = {
  id: string;
  title: string;
  artist: string;
  kind: "music" | "sfx";
  duration: number;
  tags: string[];
  source: "upload" | "openverse" | "jamendo" | string;
  source_id: string;
  source_url: string;
  license: string;
  license_version: string;
  license_url: string;
  license_label: string;
  attribution: string;
  attribution_required: boolean;
  commercial_ok: boolean;
  file_url: string;
  missing: boolean;
  created_at: string;
};

export type OnlineTrack = {
  provider: "openverse" | "jamendo";
  id: string;
  title: string;
  artist: string;
  duration: number | null;
  license: string;
  license_label: string;
  license_url: string;
  commercial_ok: boolean;
  attribution_required: boolean;
  attribution: string;
  preview_url: string;
  landing_url: string;
  source: string;
  category: string;
  tags: string[];
  imported: boolean;
};

export type MusicCredits = {
  text: string;
  items: MusicAsset[];
  credited_count: number;
  missing: string[];
  non_commercial: string[];
};
// ── end Editor audio tracks & music library ──

export type EditorSegmentPatch = Partial<Pick<EditorSegment, "text" | "start" | "end" | "muted" | "audio_start">> & {
  id: string;
  /** Re-time the voice take to last this many seconds (server speeds it up / slows it down). */
  audio_length?: number;
};
