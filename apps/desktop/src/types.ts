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
  ngrok_authtoken: string;
  ngrok_authtoken_configured: boolean;
  ngrok_domain: string;
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
  outline_color: string;
  outline: number;
  shadow: number;
  alignment: "bottom_center" | "middle_center" | "top_center" | string;
  margin_v: number;
  position_x: number;
  position_y: number;
  bold: boolean;
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
