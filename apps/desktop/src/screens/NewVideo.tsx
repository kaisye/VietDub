import {
  Captions,
  Check,
  Download,
  FileVideo,
  Link2,
  Move,
  Pause,
  Play,
  Save,
  ScanLine,
  Scaling,
  Volume2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";

import { EditorSection, WizardShell } from "../components/create/wizard-shell";
import { useCreateDraft } from "../components/create/use-create-draft";
import { Button } from "../components/ui/button";
import { Field, SegmentedControl, Select, Slider, Switch, TextArea, TextField } from "../components/ui/form";
import { InlineNotice, LoadingState } from "../components/ui/feedback";
import { VideoPreview } from "../components/ui/media";
import type { StepItem } from "../components/ui/navigation";
import {
  clearCreateDraft,
  createDraftOverrides,
  DEFAULT_OCR_REGION,
  saveCreateDraft,
  sourceDisplay,
  validateCreateStep,
  type CreateDraft,
  type DraftValidation,
} from "../lib/create-draft";
import {
  createSubtitleStyle,
  createQuickVideoJob,
  getSubtitleStyles,
  getVoiceOptions,
  outputUrl,
  previewSourceVideo,
  uploadSourceVideo,
  voicePreviewUrl,
} from "../api";
import { subtitlePreviewStyle, subtitleSecondaryPreviewStyle } from "../lib/subtitle-style-preview";
import { SOURCE_LANGUAGE_OPTIONS, TARGET_LANGUAGE_OPTIONS } from "../lib/translation-languages";
import type { SubtitleStyle, SubtitleStylePreset, VoiceProfile } from "../types";
import { orderVoiceProfiles } from "../lib/voice-order";
import { VOICE_PROFILES } from "../lib/voices";
import { useT, tt } from "../i18n";
import {
  INSTRUCT_GENDER,
  INSTRUCT_AGE,
  INSTRUCT_PITCH,
  INSTRUCT_STYLE,
  parseInstruct,
  buildInstruct,
  type InstructAttrs,
} from "../lib/voice-design";

// steps are built inside component to access translations

const SUBTITLE_FONT_OPTIONS = [
  "Arial",
  "Tahoma",
  "Verdana",
  "Georgia",
  "Times New Roman",
] as const;

export default function NewVideoScreen({ onStarted }: { onStarted: (jobId: string) => void }) {
  const { t } = useT();
  const steps: StepItem[] = [
    { id: "source", label: t.step_source_label, description: t.step_source_desc },
    { id: "voice", label: t.step_voice_label, description: t.step_voice_desc },
    { id: "translation", label: t.step_translation_label, description: t.step_translation_desc },
    { id: "subtitle", label: t.step_subtitle_label, description: t.step_subtitle_desc },
    { id: "output", label: t.step_output_label, description: t.step_output_desc },
    { id: "review", label: t.step_review_label, description: t.step_review_desc },
  ];
  const { draft, setDraft, hydrated, reset, defaultVoiceId } = useCreateDraft("quick_video");
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<DraftValidation>({});
  const [saved, setSaved] = useState(false);
  const [completed, setCompleted] = useState<"draft" | "run" | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [sourceAspectRatio, setSourceAspectRatio] = useState(16 / 9);
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>(VOICE_PROFILES);
  const [voiceLibraryError, setVoiceLibraryError] = useState("");
  const [subtitleStyles, setSubtitleStyles] = useState<SubtitleStylePreset[]>([]);
  const [activeSubtitleStyleId, setActiveSubtitleStyleId] = useState("default");
  const [subtitleLibraryError, setSubtitleLibraryError] = useState("");
  const [submitting, setSubmitting] = useState<"draft" | "run" | null>(null);
  const [submitError, setSubmitError] = useState("");
  const fileUrlRef = useRef("");
  const selectedFileRef = useRef<File | null>(null);

  useEffect(
    () => () => {
      if (fileUrlRef.current) URL.revokeObjectURL(fileUrlRef.current);
    },
    [],
  );

  useEffect(() => {
    if (!hydrated || draft.source.method !== "url") return;
    setPreviewUrl(resolveBrowserVideoUrl(draft.source.url));
  }, [draft.source.method, draft.source.url, hydrated]);

  // When a draft is restored from localStorage with an upload_name but no actual
  // File object (because the blob was revoked on unmount), clear the stale upload
  // info so the user sees an empty upload slot and must re-select the file.
  useEffect(() => {
    if (!hydrated) return;
    if (draft.source.method === "upload" && !selectedFileRef.current) {
      setDraft((prev) => ({
        ...prev,
        source: { ...prev.source, upload_name: "", upload_path: "" },
      }));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated]);

  useEffect(() => {
    getVoiceOptions()
      .then((profiles) => {
        setVoiceProfiles(mergeVoiceProfiles(profiles, VOICE_PROFILES));
        setVoiceLibraryError("");
      })
      .catch((error: unknown) => {
        setVoiceProfiles(VOICE_PROFILES);
        setVoiceLibraryError(error instanceof Error ? error.message : t.voice_lib_err_body);
      });
  }, []);

  const reloadSubtitleStyles = useCallback(async () => {
    try {
      const library = await getSubtitleStyles();
      setSubtitleStyles(library.styles);
      setActiveSubtitleStyleId(library.active_style_id);
      setSubtitleLibraryError("");
      return library.styles;
    } catch (error: unknown) {
      setSubtitleStyles([]);
      setSubtitleLibraryError(error instanceof Error ? error.message : t.sub_style_lib_err_body);
      return [] as SubtitleStylePreset[];
    }
  }, [t.sub_style_lib_err_body]);

  useEffect(() => {
    void reloadSubtitleStyles();
  }, [reloadSubtitleStyles]);

  function patch<K extends keyof CreateDraft>(key: K, value: CreateDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setErrors({});
    setCompleted(null);
  }

  function goNext() {
    const nextErrors = validateCreateStep(step, draft);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    if (step < steps.length - 1) setStep((current) => current + 1);
  }

  function saveNow() {
    saveCreateDraft(draft);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1800);
  }

  function resetAll() {
    if (fileUrlRef.current) URL.revokeObjectURL(fileUrlRef.current);
    fileUrlRef.current = "";
    selectedFileRef.current = null;
    setPreviewUrl("");
    setSourceAspectRatio(16 / 9);
    setStep(0);
    setErrors({});
    setCompleted(null);
    reset();
  }

  function selectUpload(file?: File) {
    if (!file) return;
    if (fileUrlRef.current) URL.revokeObjectURL(fileUrlRef.current);
    fileUrlRef.current = URL.createObjectURL(file);
    selectedFileRef.current = file;
    setPreviewUrl(fileUrlRef.current);
    patch("source", {
      ...draft.source,
      method: "upload",
      upload_name: file.name,
      title: file.name.replace(/\.[^.]+$/, ""),
      platform: "Local upload",
      duration: "Read when backend inspects the file",
      resolution: "Read when backend inspects the file",
    });
  }

  function capturePreviewMetadata(video: HTMLVideoElement) {
    if (video.videoWidth > 0 && video.videoHeight > 0) {
      setSourceAspectRatio(video.videoWidth / video.videoHeight);
    }
  }

  async function prepareSourcePreview() {
    if (previewLoading) return;
    setPreviewError("");
    if (draft.source.method === "upload") {
      if (!previewUrl) setPreviewError(t.src_err_reselect);
      return;
    }
    const videoUrl = draft.source.url.trim();
    if (!videoUrl) {
      setPreviewError(t.src_err_no_url);
      return;
    }
    setPreviewLoading(true);
    try {
      const preview = await previewSourceVideo(videoUrl, draft.source.download_quality);
      setPreviewUrl(outputUrl(preview.url) ?? "");
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : t.src_err_preview);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function createBackendJob(run: boolean) {
    if (submitting) return;
    setSubmitError("");
    setSubmitting(run ? "run" : "draft");
    try {
      let videoUrl = draft.source.url.trim();
      if (draft.source.method === "upload") {
        const file = selectedFileRef.current;
        if (!file) throw new Error(t.src_err_no_file);
        const uploaded = await uploadSourceVideo(file);
        videoUrl = uploaded.path;
      }
      if (!videoUrl) throw new Error(t.src_err_no_source);

      const created = await createQuickVideoJob({
        source: {
          video_url: videoUrl,
          source_title: draft.source.title || draft.source.upload_name || null,
          content: draft.source.title,
          platform: draft.output.platform,
        },
        overrides: createDraftOverrides(draft),
        run,
      });
      clearCreateDraft();
      if (run) {
        onStarted(created.job.id);
      } else {
        setCompleted("draft");
      }
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : t.src_err_create);
    } finally {
      setSubmitting(null);
    }
  }

  if (!hydrated) {
    return <LoadingState label={t.nv_restoring} />;
  }

  const primaryLabel = step === steps.length - 1 ? t.nv_create_run : undefined;

  return (
    <WizardShell
      title={t.nv_title}
      description={steps[step].description ?? ""}
      steps={steps}
      step={step}
      errors={errors}
      onStepChange={setStep}
      onBack={() => step && setStep((current) => current - 1)}
      onContinue={() => {
        if (step === steps.length - 1) void createBackendJob(true);
        else goNext();
      }}
      onReset={resetAll}
      onSaveDraft={saveNow}
      primaryLabel={primaryLabel}
    >
      {saved ? (
        <div className="mb-4">
          <InlineNotice title={t.nv_draft_saved_title} tone="success">{t.nv_draft_saved_body}</InlineNotice>
        </div>
      ) : null}
      {step === 0 ? (
        <SourceStep
          draft={draft}
          patch={patch}
          previewUrl={previewUrl}
          sourceAspectRatio={sourceAspectRatio}
          previewLoading={previewLoading}
          previewError={previewError}
          onRequestPreview={() => void prepareSourcePreview()}
          onPreviewMetadata={capturePreviewMetadata}
          onUpload={selectUpload}
        />
      ) : step === 1 ? (
        <VoiceStep draft={draft} patch={patch} voiceProfiles={voiceProfiles} voiceLibraryError={voiceLibraryError} defaultVoiceId={defaultVoiceId} />
      ) : step === 2 ? (
        <TranslationStep draft={draft} patch={patch} />
      ) : step === 3 ? (
        <SubtitleStep
          draft={draft}
          patch={patch}
          previewUrl={previewUrl}
          sourceAspectRatio={sourceAspectRatio}
          onPreviewMetadata={capturePreviewMetadata}
          styles={subtitleStyles}
          activeStyleId={activeSubtitleStyleId}
          libraryError={subtitleLibraryError}
          previewLoading={previewLoading}
          previewError={previewError}
          onRequestPreview={() => void prepareSourcePreview()}
          onReloadStyles={reloadSubtitleStyles}
        />
      ) : step === 4 ? (
        <OutputStep draft={draft} patch={patch} />
      ) : (
        <ReviewStep
          draft={draft}
          completed={completed}
          submitting={submitting}
          submitError={submitError}
          onCreate={(run) => void createBackendJob(run)}
        />
      )}
    </WizardShell>
  );
}

function SourceStep({
  draft,
  patch,
  previewUrl,
  sourceAspectRatio,
  previewLoading,
  previewError,
  onRequestPreview,
  onPreviewMetadata,
  onUpload,
}: {
  draft: CreateDraft;
  patch: <K extends keyof CreateDraft>(key: K, value: CreateDraft[K]) => void;
  previewUrl: string;
  sourceAspectRatio: number;
  previewLoading: boolean;
  previewError: string;
  onRequestPreview: () => void;
  onPreviewMetadata: (video: HTMLVideoElement) => void;
  onUpload: (file?: File) => void;
}) {
  const { t } = useT();
  const source = draft.source;
  const isOcr = source.subtitle_strategy === "ocr";
  const ocrRegion = source.ocr_region ?? DEFAULT_OCR_REGION;
  const previewRatio = sourceAspectRatio > 0 ? sourceAspectRatio : 16 / 9;

  const previewFrameRef = useRef<HTMLDivElement | null>(null);
  const previewVideoRef = useRef<HTMLVideoElement | null>(null);
  const ocrBoxRef = useRef<HTMLDivElement | null>(null);
  const ocrDragRef = useRef<{ offsetX: number; offsetY: number } | null>(null);
  const ocrScaleRef = useRef<{ startX: number; startY: number; width: number; height: number } | null>(null);
  const [ocrInteraction, setOcrInteraction] = useState<"drag" | "scale" | null>(null);
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [previewCurrentTime, setPreviewCurrentTime] = useState(0);
  const [previewDuration, setPreviewDuration] = useState(0);
  const previewPlayheadPercent = previewDuration > 0 ? (previewCurrentTime / previewDuration) * 100 : 0;

  function updateOcrRegion(next: Partial<typeof ocrRegion>) {
    patch("source", { ...source, ocr_region: { ...ocrRegion, ...next } });
  }

  function startOcrDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const box = ocrBoxRef.current?.getBoundingClientRect();
    if (!box) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    ocrDragRef.current = {
      offsetX: event.clientX - (box.left + box.width / 2),
      offsetY: event.clientY - (box.top + box.height / 2),
    };
    setOcrInteraction("drag");
  }

  function moveOcr(event: ReactPointerEvent<HTMLDivElement>) {
    if (ocrInteraction !== "drag") return;
    const frame = previewFrameRef.current?.getBoundingClientRect();
    const drag = ocrDragRef.current;
    if (!frame || !drag) return;
    const x = clampNumber(
      (event.clientX - drag.offsetX - frame.left) / Math.max(1, frame.width),
      ocrRegion.width / 2,
      1 - ocrRegion.width / 2,
    );
    const y = clampNumber(
      (event.clientY - drag.offsetY - frame.top) / Math.max(1, frame.height),
      ocrRegion.height / 2,
      1 - ocrRegion.height / 2,
    );
    updateOcrRegion({ x: roundPreviewValue(x), y: roundPreviewValue(y) });
  }

  function startOcrScale(event: ReactPointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    ocrScaleRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      width: ocrRegion.width,
      height: ocrRegion.height,
    };
    setOcrInteraction("scale");
  }

  function scaleOcr(event: ReactPointerEvent<HTMLDivElement>) {
    if (ocrInteraction !== "scale") return;
    const frame = previewFrameRef.current?.getBoundingClientRect();
    const scale = ocrScaleRef.current;
    if (!frame || !scale) return;
    event.stopPropagation();
    const width = clampNumber(
      scale.width + (event.clientX - scale.startX) * 2 / Math.max(1, frame.width),
      0.08,
      1,
    );
    const height = clampNumber(
      scale.height + (event.clientY - scale.startY) * 2 / Math.max(1, frame.height),
      0.05,
      0.9,
    );
    const x = clampNumber(ocrRegion.x, width / 2, 1 - width / 2);
    const y = clampNumber(ocrRegion.y, height / 2, 1 - height / 2);
    updateOcrRegion({
      width: roundPreviewValue(width),
      height: roundPreviewValue(height),
      x: roundPreviewValue(x),
      y: roundPreviewValue(y),
    });
  }

  function stopOcr(event: ReactPointerEvent<HTMLElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    ocrDragRef.current = null;
    ocrScaleRef.current = null;
    setOcrInteraction(null);
  }

  function toggleSourcePreviewPlayback() {
    const video = previewVideoRef.current;
    if (!video) return;
    if (video.paused) void video.play();
    else video.pause();
  }

  // Subtitle source, ordered by extraction priority and shown as a highlighted
  // card list at the top of the step. "auto" is the recommended default; the
  // three explicit methods are numbered 1→3 (existing subs → hard-sub OCR → STT).
  const subtitleSources: Array<{
    value: CreateDraft["source"]["subtitle_strategy"];
    badge: string;
    title: string;
    desc: string;
    tag?: string;
    recommended?: boolean;
  }> = [
    { value: "auto", badge: "★", title: t.src_sub_opt_auto, desc: t.src_sub_auto, tag: t.src_sub_recommended, recommended: true },
    { value: "embedded", badge: "1", title: t.src_sub_opt_embedded, desc: t.src_sub_embedded },
    { value: "ocr", badge: "2", title: t.src_sub_opt_ocr, desc: t.src_sub_ocr, tag: t.src_sub_for_hardsub },
    { value: "speech", badge: "3", title: t.src_sub_opt_speech, desc: t.src_sub_speech },
  ];

  return (
    <div className="space-y-5">
      <EditorSection compact title={t.src_title} description={t.src_desc}>
        <div className="grid gap-2 sm:grid-cols-2">
          <button
            type="button"
            aria-pressed={source.method === "url"}
            onClick={() => patch("source", { ...source, method: "url" })}
            className={`flex min-h-11 items-center gap-2 rounded-[var(--radius-control)] border px-3 text-left text-sm font-bold transition ${
              source.method === "url"
                ? "border-[var(--primary)] bg-[var(--primary-soft)]"
                : "border-[var(--border)] bg-[var(--surface-muted)]"
            }`}
          >
            <Link2 size={16} className="text-[var(--primary-hover)]" />
            {t.src_paste_url}
          </button>
          <button
            type="button"
            aria-pressed={source.method === "upload"}
            onClick={() => patch("source", { ...source, method: "upload" })}
            className={`flex min-h-11 items-center gap-2 rounded-[var(--radius-control)] border px-3 text-left text-sm font-bold transition ${
              source.method === "upload"
                ? "border-[var(--primary)] bg-[var(--primary-soft)]"
                : "border-[var(--border)] bg-[var(--surface-muted)]"
            }`}
          >
            <FileVideo size={16} className="text-[var(--primary-hover)]" />
            {t.src_upload}
          </button>
        </div>

        <div className="mt-3">
          {source.method === "url" ? (
            <Field label={t.src_url_label}>
              <TextField
                value={source.url}
                placeholder="https://www.youtube.com/watch?v=..."
                onChange={(event) => patch("source", { ...source, url: event.target.value, title: event.target.value })}
              />
            </Field>
          ) : (
            <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-[var(--radius-control)] border border-dashed border-[var(--border-strong)] bg-[var(--surface-muted)] px-4 py-2">
              <FileVideo size={20} className="shrink-0 text-[var(--primary)]" />
              <span className="min-w-0">
                <strong className="block truncate text-sm">{source.upload_name || t.src_choose_file}</strong>
                <span className="block text-xs text-[var(--text-secondary)]">{t.src_file_formats}</span>
              </span>
              <input className="sr-only" type="file" accept="video/*,.mkv" onChange={(event) => onUpload(event.target.files?.[0])} />
            </label>
          )}
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label={t.src_dl_quality}>
            <Select value={source.download_quality} onChange={(event) => patch("source", { ...source, download_quality: event.target.value as "reliable" | "best" })}>
              <option value="reliable">{t.src_dl_reliable}</option>
              <option value="best">{t.src_dl_best}</option>
            </Select>
          </Field>
          <Field label={t.src_test_clip}>
            <Select
              value={String(source.test_clip_seconds ?? 0)}
              onChange={(event) => patch("source", { ...source, test_clip_seconds: Number(event.target.value) })}
            >
              <option value="0">{t.src_test_full}</option>
              <option value="30">{t.src_test_30s}</option>
              <option value="60">{t.src_test_1m}</option>
              <option value="120">{t.src_test_2m}</option>
              <option value="300">{t.src_test_5m}</option>
            </Select>
          </Field>
        </div>
        {Number(source.test_clip_seconds ?? 0) > 0 && (
          <p className="mt-2 text-xs text-[var(--text-secondary)]">{t.src_test_clip_hint}</p>
        )}
      </EditorSection>

      <section className="rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] p-4 sm:p-5">
        <div className="mb-3 flex items-start gap-2.5">
          <Captions size={22} className="mt-0.5 shrink-0 text-[var(--primary-hover)]" />
          <div className="min-w-0">
            <h2 className="font-display text-xl leading-tight">{t.src_sub_source}</h2>
            <p className="mt-0.5 text-xs leading-5 text-[var(--text-secondary)]">{t.src_sub_section_desc}</p>
          </div>
        </div>
        <div className="grid gap-2">
          {subtitleSources.map((opt) => {
            const selected = source.subtitle_strategy === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                aria-pressed={selected}
                onClick={() => patch("source", { ...source, subtitle_strategy: opt.value })}
                className={`flex items-start gap-3 rounded-[var(--radius-control)] border p-3 text-left transition ${
                  selected
                    ? "border-[var(--primary)] bg-[var(--surface)] shadow-sm"
                    : "border-[var(--border)] bg-[var(--surface-muted)] hover:border-[var(--primary)]"
                }`}
              >
                <span
                  className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-extrabold ${
                    selected || opt.recommended
                      ? "bg-[var(--primary)] text-white"
                      : "bg-[var(--primary-soft)] text-[var(--primary-hover)]"
                  }`}
                >
                  {opt.badge}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-bold text-[var(--text-primary)]">{opt.title}</span>
                    {opt.recommended ? (
                      <span className="rounded-full bg-[var(--primary)] px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
                        {opt.tag}
                      </span>
                    ) : opt.tag ? (
                      <span className="rounded-full bg-[var(--primary-soft)] px-2 py-0.5 text-[10px] font-semibold text-[var(--primary-hover)]">
                        {opt.tag}
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-0.5 block text-xs leading-5 text-[var(--text-secondary)]">{opt.desc}</span>
                </span>
                {selected ? (
                  <Check size={16} strokeWidth={3} className="mt-0.5 shrink-0 text-[var(--primary)]" />
                ) : null}
              </button>
            );
          })}
        </div>
      </section>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(220px,.4fr)]">
        <EditorSection
          title={t.src_preview_title}
          description={t.src_preview_desc}
          actions={isOcr ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              loading={previewLoading}
              leadingIcon={previewUrl ? <Play size={15} /> : <Download size={15} />}
              onClick={onRequestPreview}
            >
              {previewUrl ? t.sub_btn_preview : t.sub_btn_prepare}
            </Button>
          ) : undefined}
        >
          {isOcr && previewError ? (
            <div className="mb-4">
              <InlineNotice title={t.sub_preview_unavail} tone="warning">{previewError}</InlineNotice>
            </div>
          ) : null}
          {previewUrl && isOcr ? (
            <>
              <div className="relative flex min-h-[240px] items-center justify-center overflow-hidden rounded-[var(--radius-panel-sm)] bg-black p-2">
                <div
                  ref={previewFrameRef}
                  className="relative max-w-full overflow-hidden rounded-[var(--radius-panel-sm)] bg-black"
                  style={{
                    aspectRatio: `${previewRatio}`,
                    width: previewRatio >= 1.3 ? "100%" : `min(100%, ${Math.round(360 * previewRatio)}px)`,
                  }}
                >
                  <video
                    ref={previewVideoRef}
                    className="h-full w-full object-contain"
                    muted
                    playsInline
                    preload="metadata"
                    src={previewUrl}
                    onLoadedMetadata={(event) => {
                      onPreviewMetadata(event.currentTarget);
                      setPreviewDuration(event.currentTarget.duration || 0);
                    }}
                    onPlay={() => setPreviewPlaying(true)}
                    onPause={() => setPreviewPlaying(false)}
                    onTimeUpdate={(event) => setPreviewCurrentTime(event.currentTarget.currentTime)}
                  />
                  <div
                    ref={ocrBoxRef}
                    className={`absolute z-[5] touch-none select-none border-2 border-dashed border-[var(--primary)] bg-[var(--primary)]/10 ${
                      ocrInteraction === "drag" ? "cursor-grabbing" : "cursor-grab"
                    }`}
                    style={{
                      left: `${ocrRegion.x * 100}%`,
                      top: `${ocrRegion.y * 100}%`,
                      width: `${ocrRegion.width * 100}%`,
                      height: `${ocrRegion.height * 100}%`,
                      transform: "translate(-50%, -50%)",
                    }}
                    onPointerDown={startOcrDrag}
                    onPointerMove={moveOcr}
                    onPointerUp={stopOcr}
                    onPointerCancel={stopOcr}
                  >
                    <span className="pointer-events-none absolute left-2 top-1 rounded bg-black/65 px-2 py-0.5 text-[10px] font-bold uppercase text-white">
                      {t.src_sub_ocr_region_label}
                    </span>
                    <span className="pointer-events-none absolute -left-3 -top-3 flex h-7 w-7 items-center justify-center rounded-full bg-[var(--primary)] text-white shadow-md">
                      <Move size={14} />
                    </span>
                    <div
                      className="absolute -bottom-3 -right-3 flex h-7 w-7 cursor-nwse-resize touch-none items-center justify-center rounded-full border-2 border-white bg-[var(--primary)] text-white shadow-md"
                      role="button"
                      aria-label={t.src_sub_ocr_region_label}
                      onPointerDown={startOcrScale}
                      onPointerMove={scaleOcr}
                      onPointerUp={stopOcr}
                      onPointerCancel={stopOcr}
                    >
                      <Scaling size={13} />
                    </div>
                  </div>
                </div>
              </div>
              <div className="mt-2 flex items-center gap-3 rounded-[var(--radius-panel-sm)] bg-[var(--surface-muted)] px-3 py-2">
                <button
                  type="button"
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--surface)] text-[var(--text-primary)] hover:bg-[var(--border)]"
                  onClick={toggleSourcePreviewPlayback}
                  title={previewPlaying ? "Pause" : "Play"}
                >
                  {previewPlaying ? <Pause size={14} /> : <Play size={14} />}
                </button>
                <span className="w-10 shrink-0 font-mono text-xs tabular-nums text-[var(--text-secondary)]">
                  {formatSeconds(previewCurrentTime)}
                </span>
                <div
                  className="relative flex h-4 flex-1 cursor-pointer items-center"
                  onClick={(event) => {
                    if (!previewDuration || !previewVideoRef.current) return;
                    const rect = event.currentTarget.getBoundingClientRect();
                    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
                    previewVideoRef.current.currentTime = ratio * previewDuration;
                    setPreviewCurrentTime(previewVideoRef.current.currentTime);
                  }}
                >
                  <div className="h-1 w-full rounded-full bg-[var(--border)]" />
                  <div
                    className="absolute left-0 h-1 rounded-full bg-[var(--primary)]"
                    style={{ width: `${previewPlayheadPercent}%` }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right font-mono text-xs tabular-nums text-[var(--text-secondary)]">
                  {formatSeconds(previewDuration)}
                </span>
              </div>
              <p className="mt-2 text-xs text-[var(--text-secondary)]">{t.src_sub_ocr_region_hint}</p>
            </>
          ) : previewUrl ? (
            <video
              className="aspect-video w-full rounded-[var(--radius-panel-sm)] bg-black object-contain"
              controls
              playsInline
              preload="metadata"
              src={previewUrl}
              onLoadedMetadata={(event) => onPreviewMetadata(event.currentTarget)}
            />
          ) : isOcr ? (
            <div className="flex aspect-video w-full items-center justify-center rounded-[var(--radius-panel-sm)] border border-dashed border-[var(--border)] bg-[var(--surface-muted)] px-6 text-center text-sm text-[var(--text-secondary)]">
              {t.src_sub_ocr_preview_hint}
            </div>
          ) : (
            <VideoPreview title={sourceDisplay(draft) === "No source selected" ? t.src_no_source : sourceDisplay(draft)} />
          )}
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
            <Meta label={t.src_meta_platform} value={source.platform === "Auto detect" ? t.src_meta_auto : source.platform} />
            <Meta label={t.src_meta_duration} value={source.duration === "Available after source selection" ? t.src_meta_pending : source.duration} />
            <Meta label={t.src_meta_resolution} value={source.resolution === "Available after source selection" ? t.src_meta_pending : source.resolution} />
          </dl>
        </EditorSection>
        <EditorSection compact title={t.src_lang_title}>
          <div className="space-y-5">
            <Field label={t.src_lang_source}>
              <Select value={draft.languages.source} onChange={(event) => patch("languages", { ...draft.languages, source: event.target.value })}>
                {SOURCE_LANGUAGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </Select>
            </Field>
            <Field label={t.src_lang_target}>
              <Select value={draft.languages.target} onChange={(event) => patch("languages", { ...draft.languages, target: event.target.value })}>
                {TARGET_LANGUAGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </Select>
            </Field>
          </div>
        </EditorSection>
      </div>
    </div>
  );
}

function VoiceDesignBuilder({
  instruction,
  onChange,
  t,
}: {
  instruction: string;
  onChange: (value: string) => void;
  t: ReturnType<typeof useT>["t"];
}) {
  const parsed = useMemo(() => parseInstruct(instruction), [instruction]);
  const [showCustom, setShowCustom] = useState(false);

  function update(key: keyof InstructAttrs, value: string) {
    onChange(buildInstruct({ ...parsed, [key]: value }));
  }

  return (
    <Field label={t.voice_instruction_label} help={t.voice_instruction_help}>
      <div className="grid grid-cols-2 gap-2">
        <Select value={parsed.gender} onChange={(e) => update("gender", e.target.value)}>
          <option value="">{t.voice_instruct_auto} — {t.voice_instruct_gender}</option>
          {INSTRUCT_GENDER.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
        </Select>
        <Select value={parsed.age} onChange={(e) => update("age", e.target.value)}>
          <option value="">{t.voice_instruct_auto} — {t.voice_instruct_age}</option>
          {INSTRUCT_AGE.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
        </Select>
        <Select value={parsed.pitch} onChange={(e) => update("pitch", e.target.value)}>
          <option value="">{t.voice_instruct_auto} — {t.voice_instruct_pitch}</option>
          {INSTRUCT_PITCH.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
        </Select>
        <Select value={parsed.style} onChange={(e) => update("style", e.target.value)}>
          <option value="">{t.voice_instruct_auto} — {t.voice_instruct_style}</option>
          {INSTRUCT_STYLE.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
        </Select>
      </div>
      {instruction ? (
        <p className="mt-2 rounded bg-[var(--surface-muted)] px-3 py-1.5 text-xs text-[var(--text-secondary)]">
          <span className="font-semibold">{t.voice_instruct_result}:</span>{" "}
          <code className="font-mono">{instruction}</code>
        </p>
      ) : null}
      <button
        type="button"
        className="mt-1 text-xs text-[var(--primary-hover)] underline-offset-2 hover:underline"
        onClick={() => setShowCustom((v) => !v)}
      >
        {t.voice_instruct_custom_label}
      </button>
      {showCustom ? (
        <TextArea
          className="mt-2"
          value={instruction}
          placeholder="male, young adult, high pitch"
          onChange={(e) => onChange(e.target.value)}
        />
      ) : null}
    </Field>
  );
}

// Engine grouping for the voice grid. ZeroTTS gets its own section because its
// first-use download and quality/performance profile differ from the light voices.
type VoiceGroupKey = "edge" | "zerotts" | "omnivoice";

function voiceGroupKey(voice: VoiceProfile): VoiceGroupKey {
  if (voice.engine === "zerotts") return "zerotts";
  return voice.engine === "edge" ? "edge" : "omnivoice";
}

const VOICE_GROUP_ACCENT: Record<VoiceGroupKey, { accent: string; soft: string; ink: string }> = {
  edge: { accent: "var(--info)", soft: "var(--info-soft)", ink: "var(--info)" },
  zerotts: { accent: "var(--success)", soft: "var(--success-soft)", ink: "var(--success)" },
  omnivoice: { accent: "var(--primary)", soft: "var(--primary-soft)", ink: "var(--primary-ink)" },
};

const VOICE_GRID_COLS = "grid gap-2.5 [grid-template-columns:repeat(auto-fill,minmax(190px,1fr))]";

function VoicePicker({
  voices,
  value,
  defaultVoiceId,
  onSelect,
}: {
  voices: VoiceProfile[];
  value: string;
  defaultVoiceId: string;
  onSelect: (voiceId: string) => void;
}) {
  const { t } = useT();
  const [previewingId, setPreviewingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => () => audioRef.current?.pause(), []);

  function preview(voiceId: string) {
    if (voiceId === "none") return;
    audioRef.current?.pause();
    const audio = new Audio(voicePreviewUrl(voiceId));
    audioRef.current = audio;
    setPreviewingId(voiceId);
    audio.onended = () => setPreviewingId(null);
    audio.onerror = () => setPreviewingId(null);
    void audio.play().catch(() => setPreviewingId(null));
  }

  const noneVoice = voices.find((item) => item.id === "none");
  const edgeVoices = voices.filter((item) => item.id !== "none" && voiceGroupKey(item) === "edge");
  const zeroVoices = voices.filter((item) => item.id !== "none" && voiceGroupKey(item) === "zerotts");
  const omniVoices = voices.filter((item) => item.id !== "none" && voiceGroupKey(item) === "omnivoice");

  const renderCard = (voice: VoiceProfile, group: VoiceGroupKey) => {
    const selected = voice.id === value;
    const accent = VOICE_GROUP_ACCENT[group];
    return (
      <div
        key={voice.id}
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        onClick={() => onSelect(voice.id)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect(voice.id);
          }
        }}
        className="relative flex cursor-pointer flex-col gap-0.5 rounded-[var(--radius-panel-sm)] p-3 pr-12 transition-colors"
        style={{
          border: `${selected ? 2 : 1}px solid ${selected ? accent.accent : "var(--border)"}`,
          background: selected ? accent.soft : "var(--surface)",
        }}
      >
        <div className="flex min-w-0 items-center gap-1.5">
          {selected ? <Check size={14} strokeWidth={3} color={accent.ink} className="shrink-0" /> : null}
          <span
            className="truncate text-sm font-semibold"
            style={{ color: selected ? accent.ink : "var(--text-primary)" }}
            title={voice.name}
          >
            {voice.name}
          </span>
          {voice.id === defaultVoiceId ? (
            <span className="shrink-0 text-xs" style={{ color: accent.accent }}>★</span>
          ) : null}
        </div>
        <span
          className="truncate text-xs text-[var(--text-secondary)]"
          title={`${voice.type} · ${voice.locale}`}
        >
          {voice.type} · {voice.locale}
        </span>
        <button
          type="button"
          aria-label={t.voice_play_aria}
          className="absolute right-2.5 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full text-white"
          style={{ background: accent.accent }}
          onClick={(event) => {
            event.stopPropagation();
            preview(voice.id);
          }}
        >
          {previewingId === voice.id ? <Volume2 className="animate-pulse" size={14} /> : <Play size={14} />}
        </button>
      </div>
    );
  };

  const renderGroupHeader = (group: VoiceGroupKey, title: string, note: string) => {
    const accent = VOICE_GROUP_ACCENT[group];
    return (
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="inline-flex items-center gap-1.5 text-sm font-semibold" style={{ color: accent.ink }}>
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: accent.accent }} />
          {title}
        </span>
        <span className="text-xs text-[var(--text-secondary)]">{note}</span>
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {edgeVoices.length ? (
        <div className="space-y-2">
          {renderGroupHeader("edge", t.voice_group_edge_title, t.voice_group_edge_note)}
          <div className={VOICE_GRID_COLS}>{edgeVoices.map((voice) => renderCard(voice, "edge"))}</div>
        </div>
      ) : null}

      {zeroVoices.length ? (
        <div className="space-y-2">
          {renderGroupHeader("zerotts", t.voice_group_zerotts_title, t.voice_group_zerotts_note)}
          <div className={VOICE_GRID_COLS}>{zeroVoices.map((voice) => renderCard(voice, "zerotts"))}</div>
        </div>
      ) : null}

      {omniVoices.length ? (
        <div className="space-y-2">
          {renderGroupHeader("omnivoice", t.voice_group_omni_title, t.voice_group_omni_note)}
          <div className={VOICE_GRID_COLS}>{omniVoices.map((voice) => renderCard(voice, "omnivoice"))}</div>
        </div>
      ) : null}

      {noneVoice ? (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onSelect(noneVoice.id);
          }}
          className="flex w-full items-center justify-between gap-2 rounded-[var(--radius-panel-sm)] p-3 text-left text-sm transition-colors"
          style={{
            border: `${value === "none" ? 2 : 1}px solid ${value === "none" ? "var(--border-strong)" : "var(--border)"}`,
            background: value === "none" ? "var(--surface-muted)" : "var(--surface)",
          }}
        >
          <span className="min-w-0">
            <strong className="block">{t.voice_group_none_title}</strong>
            <span className="block text-xs text-[var(--text-secondary)]">{t.voice_none_desc}</span>
          </span>
          {value === "none" ? <Check size={16} className="shrink-0 text-[var(--text-secondary)]" /> : null}
        </button>
      ) : null}
    </div>
  );
}

function VoiceStep({
  draft,
  patch,
  voiceProfiles,
  voiceLibraryError,
  defaultVoiceId,
}: DraftStepProps & {
  voiceProfiles: VoiceProfile[];
  voiceLibraryError: string;
  defaultVoiceId: string;
}) {
  const voice = draft.voice;
  const orderedVoices = useMemo(
    () => orderVoiceProfiles(voiceProfiles, voice.voice_id),
    [voice.voice_id, voiceProfiles],
  );
  const selectedVoice = orderedVoices.find((item) => item.id === voice.voice_id);
  const voiceDisabled = voice.voice_id === "none";
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const previewAudioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(
    () => () => {
      previewAudioRef.current?.pause();
    },
    [],
  );

  const { t } = useT();

  async function playPreview() {
    if (!voice.voice_id || voiceDisabled || previewing) return;
    setPreviewError("");
    setPreviewing(true);
    try {
      previewAudioRef.current?.pause();
      const audio = new Audio(voicePreviewUrl(voice.voice_id));
      previewAudioRef.current = audio;
      audio.onended = () => setPreviewing(false);
      audio.onerror = () => { setPreviewing(false); setPreviewError(t.voice_preview_err_gen); };
      await audio.play();
    } catch (error) {
      setPreviewing(false);
      setPreviewError(error instanceof Error ? error.message : t.voice_preview_err_play);
    }
  }

  return (
    <div>
      <EditorSection title={t.voice_delivery_title} description={t.voice_delivery_desc}>
        <div className="space-y-5">
          {/* Not a <Field>: a <label> would forward every in-grid click to its
              first control, hijacking card selection. Use a plain labelled block. */}
          <div className="grid min-w-0 gap-2">
            <span className="text-sm font-bold text-[var(--text-primary)]">{t.voice_field_label}</span>
            <VoicePicker
              voices={voiceProfiles}
              value={voice.voice_id}
              defaultVoiceId={defaultVoiceId}
              onSelect={(voice_id) => {
                const nextVoice = orderedVoices.find((item) => item.id === voice_id);
                patch("voice", {
                  ...voice,
                  voice_id,
                  instruction: nextVoice?.instruction ?? "",
                });
              }}
            />
          </div>
          {voiceLibraryError ? (
            <InlineNotice title={t.voice_lib_err_title} tone="warning">
              {t.voice_lib_err_body} {voiceLibraryError}
            </InlineNotice>
          ) : null}
          <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-4">
            <button
              type="button"
              className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--primary)] text-white disabled:opacity-60"
              aria-label={t.voice_play_aria}
              disabled={previewing || voiceDisabled}
              onClick={() => void playPreview()}
            >
              {previewing ? <Volume2 className="animate-pulse" size={17} /> : <Play size={17} />}
            </button>
            <div className="min-w-0">
              <strong className="block text-sm">{selectedVoice?.name ?? t.voice_field_label}</strong>
              <span className="text-xs text-[var(--text-secondary)]">
                {voiceDisabled
                  ? t.voice_none_desc
                  : `${selectedVoice?.type ?? "Voice provider"} / ${selectedVoice?.locale ?? "custom"}`}
              </span>
            </div>
          </div>
          {previewError ? <p className="text-xs text-red-600">{previewError}</p> : null}
          {!voiceDisabled ? (
            <>
              <Slider label={t.voice_rate} value={voice.rate} min={-20} max={20} suffix="%" onChange={(rate) => patch("voice", { ...voice, rate })} />
              <VoiceDesignBuilder
                instruction={voice.instruction}
                onChange={(instruction) => patch("voice", { ...voice, instruction })}
                t={t}
              />
            </>
          ) : null}
        </div>
      </EditorSection>
    </div>
  );
}

const CONTEXT_HINTS = [
  {
    key: "hook" as const,
    text: "Dịch theo phong cách content viral. Hook câu mở đầu thật thu hút. Giữ năng lượng và cảm xúc cao suốt video.",
  },
  {
    key: "news" as const,
    text: "Phong cách thời sự, tin tức. Dịch chính xác, khách quan, giọng trung lập. Giữ nguyên tên người, địa danh, tổ chức. Không thêm bình luận hay cảm xúc cá nhân.",
  },
  {
    key: "edu" as const,
    text: "Nội dung giáo dục, khoa học hoặc công nghệ. Ưu tiên độ chính xác: giữ nguyên thuật ngữ chuyên ngành tiếng Anh hoặc phiên âm chuẩn. Giải thích rõ ràng, mạch lạc. Không đơn giản hóa quá mức làm mất nội dung.",
  },
  {
    key: "film" as const,
    text: "Dịch theo phong cách kịch bản điện ảnh. Giữ giọng điệu và cá tính riêng của từng nhân vật xuyên suốt. Thoại phải nghe tự nhiên như người thật nói, không cứng nhắc. Ưu tiên cảm xúc và ý đồ nhân vật hơn dịch từng từ.",
  },
  {
    key: "gaming" as const,
    text: "Nội dung gaming hoặc esports. Giữ nguyên tên game, tướng, kỹ năng, item và thuật ngữ cộng đồng bằng tiếng Anh. Dùng từ ngữ phổ biến trong cộng đồng game thủ Việt. Giọng hưng phấn, sống động.",
  },
  {
    key: "ads" as const,
    text: "Phong cách quảng cáo và thương mại. Ngôn ngữ thuyết phục, tạo sức hút và cảm giác cấp bách. Lợi ích sản phẩm và CTA phải nổi bật. Tránh từ kỹ thuật khô khan.",
  },
  {
    key: "concise" as const,
    text: "Dịch ngắn gọn, súc tích. Loại bỏ từ đệm và cụm từ thừa. Câu ngắn, rõ ràng. Giữ nguyên dữ kiện và ý chính, bỏ phần diễn giải dài dòng.",
  },
] as const;

function TranslationStep({ draft, patch }: DraftStepProps) {
  const { t, lang } = useT();
  const translation = draft.translation;
  const [customHints, setCustomHints] = useState<string[]>([]);
  const [addingHint, setAddingHint] = useState(false);
  const [newHintText, setNewHintText] = useState("");
  const newHintInputRef = useRef<HTMLTextAreaElement>(null);

  const toneOptions = [
    { value: "Natural and context-aware",   label: lang === "vi" ? "Tự nhiên, phù hợp ngữ cảnh"    : "Natural and context-aware" },
    { value: "Friendly and conversational", label: lang === "vi" ? "Thân thiện, gần gũi"            : "Friendly and conversational" },
    { value: "Professional and formal",     label: lang === "vi" ? "Chuyên nghiệp, trang trọng"     : "Professional and formal" },
    { value: "Humorous and witty",          label: lang === "vi" ? "Hài hước, dí dỏm"               : "Humorous and witty" },
    { value: "Inspirational and energetic", label: lang === "vi" ? "Truyền cảm hứng, năng động"     : "Inspirational and energetic" },
    { value: "Serious and concise",         label: lang === "vi" ? "Nghiêm túc, súc tích"           : "Serious and concise" },
  ];

  function applyHint(text: string) {
    const existing = translation.context.trim();
    patch("translation", {
      ...translation,
      context: existing ? `${existing}\n${text}` : text,
    });
  }

  function openAddForm() {
    setAddingHint(true);
    setNewHintText("");
    window.setTimeout(() => newHintInputRef.current?.focus(), 0);
  }

  function saveCustomHint() {
    const text = newHintText.trim();
    if (!text) return;
    setCustomHints((prev) => [...prev, text]);
    setAddingHint(false);
    setNewHintText("");
  }

  function removeCustomHint(index: number) {
    setCustomHints((prev) => prev.filter((_, i) => i !== index));
  }

  const hintLabels: Record<(typeof CONTEXT_HINTS)[number]["key"], string> = {
    hook: t.trans_hint_hook,
    news: t.trans_hint_news,
    edu: t.trans_hint_edu,
    film: t.trans_hint_film,
    gaming: t.trans_hint_gaming,
    ads: t.trans_hint_ads,
    concise: t.trans_hint_concise,
  };

  const chipClass = "rounded-full border border-[var(--border)] bg-[var(--surface-muted)] px-2.5 py-0.5 text-xs font-medium text-[var(--text-secondary)] transition hover:border-[var(--primary)] hover:bg-[var(--primary-soft)] hover:text-[var(--primary-hover)]";

  return (
    <EditorSection title={t.trans_title} description={t.trans_desc}>
      <div className="grid gap-5 lg:grid-cols-2">
        <Field label={t.trans_tone}>
          <Select
            value={toneOptions.some((o) => o.value === translation.tone) ? translation.tone : toneOptions[0].value}
            onChange={(event) => patch("translation", { ...translation, tone: event.target.value })}
          >
            {toneOptions.map(({ value, label }) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </Select>
        </Field>
        <Field label={t.trans_proper_names}>
          <Select value={translation.proper_names} onChange={(event) => patch("translation", { ...translation, proper_names: event.target.value as CreateDraft["translation"]["proper_names"] })}>
            <option value="preserve">{t.trans_preserve_names}</option>
            <option value="transliterate">{t.trans_transliterate}</option>
            <option value="localize">{t.trans_localize}</option>
          </Select>
        </Field>
        <div className="lg:col-span-2">
          <Field label={t.trans_context}>
            <TextArea value={translation.context} placeholder={t.trans_context_placeholder} onChange={(event) => patch("translation", { ...translation, context: event.target.value })} />
          </Field>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 text-xs text-[var(--text-tertiary)]">{t.trans_hints}:</span>
            {CONTEXT_HINTS.map(({ key, text }) => (
              <button key={key} type="button" onClick={() => applyHint(text)} title={text} className={chipClass}>
                {hintLabels[key]}
              </button>
            ))}
            {customHints.map((text, i) => (
              <span key={i} className="group flex items-center gap-0.5 rounded-full border border-[var(--primary-soft)] bg-[var(--primary-soft)] pl-2.5 pr-1 py-0.5">
                <button
                  type="button"
                  onClick={() => applyHint(text)}
                  title={text}
                  className="text-xs font-medium text-[var(--primary-hover)] transition group-hover:text-[var(--primary)]"
                >
                  {text.length > 28 ? text.slice(0, 26) + "…" : text}
                </button>
                <button
                  type="button"
                  aria-label="Xóa gợi ý"
                  onClick={() => removeCustomHint(i)}
                  className="ml-0.5 flex h-4 w-4 items-center justify-center rounded-full text-[var(--text-tertiary)] transition hover:bg-[var(--primary)] hover:text-white"
                >
                  ×
                </button>
              </span>
            ))}
            {!addingHint && (
              <button type="button" onClick={openAddForm} className={`${chipClass} border-dashed`}>
                + {lang === "vi" ? "Thêm" : "Add"}
              </button>
            )}
          </div>
          {addingHint && (
            <div className="mt-2 flex flex-col gap-2 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-3">
              <textarea
                ref={newHintInputRef}
                value={newHintText}
                rows={2}
                placeholder={lang === "vi" ? "Nhập nội dung gợi ý…" : "Type custom hint text…"}
                onChange={(e) => setNewHintText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) saveCustomHint();
                  if (e.key === "Escape") { setAddingHint(false); setNewHintText(""); }
                }}
                className="w-full resize-none rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--primary)]"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={saveCustomHint}
                  disabled={!newHintText.trim()}
                  className="rounded-[var(--radius-control)] bg-[var(--primary)] px-3 py-1 text-xs font-semibold text-white disabled:opacity-40 hover:bg-[var(--primary-hover)]"
                >
                  {lang === "vi" ? "Lưu" : "Save"}
                </button>
                <button
                  type="button"
                  onClick={() => { setAddingHint(false); setNewHintText(""); }}
                  className="rounded-[var(--radius-control)] border border-[var(--border)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)] hover:border-[var(--primary)] hover:text-[var(--primary-hover)]"
                >
                  {lang === "vi" ? "Hủy" : "Cancel"}
                </button>
                <span className="ml-auto self-center text-[10px] text-[var(--text-tertiary)]">
                  {lang === "vi" ? "Ctrl+Enter để lưu" : "Ctrl+Enter to save"}
                </span>
              </div>
            </div>
          )}
        </div>
        <Field label={t.trans_glossary} help={t.trans_glossary_help}>
          <TextArea value={translation.glossary} placeholder={t.trans_glossary_placeholder} onChange={(event) => patch("translation", { ...translation, glossary: event.target.value })} />
        </Field>
        <div className="space-y-4">
          <SegmentedControl label={t.trans_fidelity} value={translation.semantic_fidelity} onChange={(semantic_fidelity) => patch("translation", { ...translation, semantic_fidelity: semantic_fidelity as CreateDraft["translation"]["semantic_fidelity"] })} options={[{ value: "strict", label: t.trans_strict }, { value: "balanced", label: t.trans_balanced }, { value: "natural", label: t.trans_natural }]} />
          <Switch checked={translation.remove_sound_tags} onChange={(remove_sound_tags) => patch("translation", { ...translation, remove_sound_tags })} label={t.trans_remove_tags} description={t.trans_remove_tags_desc} />
        </div>
      </div>
    </EditorSection>
  );
}

function SubtitleStep({
  draft,
  patch,
  previewUrl,
  sourceAspectRatio,
  onPreviewMetadata,
  styles,
  activeStyleId,
  libraryError,
  previewLoading,
  previewError,
  onRequestPreview,
  onReloadStyles,
}: DraftStepProps & {
  previewUrl: string;
  sourceAspectRatio: number;
  onPreviewMetadata: (video: HTMLVideoElement) => void;
  styles: SubtitleStylePreset[];
  activeStyleId: string;
  libraryError: string;
  previewLoading: boolean;
  previewError: string;
  onRequestPreview: () => void;
  onReloadStyles: () => Promise<SubtitleStylePreset[]>;
}) {
  const { t } = useT();
  const subtitle = draft.subtitle;
  const subtitlesDisabled = subtitle.style_id === "none";
  const selectedStyle = styles.find((style) => style.id === subtitle.style_id);
  const hasSelectedStyle = styles.some((style) => style.id === subtitle.style_id);
  const effectiveStyle: Partial<SubtitleStyle> = {
    ...(selectedStyle?.style ?? {}),
    ...(subtitle.style_overrides ?? {}),
  };
  const previewRatio = resolvePreviewAspectRatio(draft.output.aspect_ratio, sourceAspectRatio);
  const previewRatioLabel = draft.output.aspect_ratio === "source"
    ? `Source · ${formatAspectRatio(sourceAspectRatio)}`
    : draft.output.aspect_ratio;
  const previewIsCropped = draft.output.aspect_ratio !== "source";
  const previewFrameRef = useRef<HTMLDivElement>(null);
  const previewVideoRef = useRef<HTMLVideoElement>(null);
  const subtitleBoxRef = useRef<HTMLDivElement>(null);
  const blurBoxRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ offsetX: number; offsetY: number } | null>(null);
  const scaleRef = useRef<{ startX: number; startY: number; fontSize: number } | null>(null);
  const blurDragRef = useRef<{ offsetX: number; offsetY: number } | null>(null);
  const blurScaleRef = useRef<{
    startX: number;
    startY: number;
    width: number;
    height: number;
  } | null>(null);
  const [interaction, setInteraction] = useState<"drag" | "scale" | null>(null);
  const [blurInteraction, setBlurInteraction] = useState<"drag" | "scale" | null>(null);
  const [previewDownloading, setPreviewDownloading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const playheadPercent = duration > 0 ? (currentTime / duration) * 100 : 0;
  const bilingualEnabled = Boolean(effectiveStyle.bilingual_enabled);
  const blurEnabled = Boolean(effectiveStyle.hard_sub_blur_enabled);
  const blurX = Number(effectiveStyle.hard_sub_blur_x ?? 0.5);
  const blurY = Number(effectiveStyle.hard_sub_blur_y ?? 0.86);
  const blurWidth = Number(effectiveStyle.hard_sub_blur_width ?? 0.72);
  const blurHeight = Number(effectiveStyle.hard_sub_blur_height ?? 0.14);
  const blurStyle = String(effectiveStyle.hard_sub_blur_style ?? "box");
  // 50 = the renderer's auto strength (1.0×); the slider scales the preview blur 0–5×.
  const blurMul = clampNumber(Number(effectiveStyle.hard_sub_blur_strength ?? 50), 0, 250) / 50;
  const previewBlurPx = Math.max(0.5, Math.round(10 * blurMul * 10) / 10);
  const previewBlurDev = Math.max(0.5, Math.round(8 * blurMul * 10) / 10);
  // CSS backdrop-filter blur() is isotropic; reference the inline SVG Gaussian filters
  // (defined in the preview frame) to preview the directional blur the renderer applies.
  const blurBackdropCss =
    blurStyle === "vertical"
      ? "url(#hardsub-blur-vertical)"
      : blurStyle === "horizontal"
      ? "url(#hardsub-blur-horizontal)"
      : `blur(${previewBlurPx}px)`;

  function updateSubtitleStyle(updates: Partial<SubtitleStyle>) {
    patch("subtitle", {
      ...subtitle,
      style_overrides: {
        ...(subtitle.style_overrides ?? {}),
        ...updates,
      },
    });
  }

  const [showSaveStyle, setShowSaveStyle] = useState(false);
  const [styleNameDraft, setStyleNameDraft] = useState("");
  const [savingStyle, setSavingStyle] = useState(false);
  const [saveStyleError, setSaveStyleError] = useState("");
  const [styleSaved, setStyleSaved] = useState(false);

  async function saveCurrentStyle() {
    const name = styleNameDraft.trim();
    if (!name || savingStyle) return;
    setSavingStyle(true);
    setSaveStyleError("");
    try {
      const preset = await createSubtitleStyle({ name, style: effectiveStyle });
      await onReloadStyles();
      // The saved preset now carries the full look; point the draft at it and
      // drop the ad-hoc overrides so the dropdown reflects the named style and
      // the live preview keeps rendering the exact same thing.
      patch("subtitle", { ...subtitle, style_id: preset.id, style_overrides: {} });
      setShowSaveStyle(false);
      setStyleNameDraft("");
      setStyleSaved(true);
      window.setTimeout(() => setStyleSaved(false), 1800);
    } catch (error) {
      setSaveStyleError(error instanceof Error ? error.message : t.sub_style_save_err);
    } finally {
      setSavingStyle(false);
    }
  }

  function moveSubtitle(event: ReactPointerEvent<HTMLDivElement>) {
    const frame = previewFrameRef.current?.getBoundingClientRect();
    const box = subtitleBoxRef.current?.getBoundingClientRect();
    const drag = dragRef.current;
    if (!frame || !box || !drag) return;
    const halfHeight = Math.min(0.45, box.height / Math.max(1, frame.height) / 2);
    // position_x marks the render anchor point; edge-anchoring (left/right) keeps
    // the box on-screen at the extremes, so it only needs the 0.05–0.95 clamp.
    const x = clampNumber(
      (event.clientX - drag.offsetX - frame.left) / Math.max(1, frame.width),
      0.05,
      0.95,
    );
    // position_y = center of text block; allow center to go near the edges
    const y = clampNumber(
      (event.clientY - drag.offsetY - frame.top) / Math.max(1, frame.height),
      Math.max(0.02, halfHeight),
      Math.min(0.98, 1 - halfHeight),
    );
    updateSubtitleStyle({
      position_x: roundPreviewValue(x),
      position_y: roundPreviewValue(y),
      alignment: alignmentFromCoordinates(x, y),
    });
  }

  function handleSubtitlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const box = subtitleBoxRef.current?.getBoundingClientRect();
    if (!box) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    // position_x is the render anchor point: the box's left edge (<=0.33), right
    // edge (>=0.67) or center otherwise. Offset from that same anchor so the
    // cursor maps straight onto position_x while dragging.
    const px = Number(effectiveStyle.position_x ?? 0.5);
    const anchorX = px <= 0.33 ? box.left : px >= 0.67 ? box.right : box.left + box.width / 2;
    dragRef.current = {
      offsetX: event.clientX - anchorX,
      offsetY: event.clientY - (box.top + box.height / 2),
    };
    setInteraction("drag");
  }

  function handleSubtitlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (interaction === "drag") moveSubtitle(event);
  }

  function stopSubtitleInteraction(event: ReactPointerEvent<HTMLElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    scaleRef.current = null;
    setInteraction(null);
  }

  function handleScalePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    scaleRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      fontSize: Number(effectiveStyle.font_size ?? 20),
    };
    setInteraction("scale");
  }

  function handleScalePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const frame = previewFrameRef.current?.getBoundingClientRect();
    const scale = scaleRef.current;
    if (interaction !== "scale" || !frame || !scale) return;
    event.stopPropagation();
    const delta = ((event.clientX - scale.startX) + (event.clientY - scale.startY)) / 2;
    const fontSize = clampNumber(scale.fontSize + delta / Math.max(1, frame.width) * 80, 10, 72);
    updateSubtitleStyle({ font_size: Math.round(fontSize) });
  }

  function handlePreviewClick() {
    if (!previewUrl) {
      onRequestPreview();
      return;
    }
    const video = previewVideoRef.current;
    if (!video) return;
    if (video.paused) void video.play();
    else video.pause();
  }

  async function downloadPreview() {
    if (!previewUrl || previewDownloading) return;
    setPreviewDownloading(true);
    try {
      const response = await fetch(previewUrl);
      if (!response.ok) throw new Error(`Video download failed: ${response.status}`);
      const blobUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = blobUrl;
      anchor.download = "aether-source-preview.mp4";
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (error) {
      console.error("Unable to download video preview.", error);
    } finally {
      setPreviewDownloading(false);
    }
  }

  function handleBlurPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const box = blurBoxRef.current?.getBoundingClientRect();
    if (!box) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    blurDragRef.current = {
      offsetX: event.clientX - (box.left + box.width / 2),
      offsetY: event.clientY - (box.top + box.height / 2),
    };
    setBlurInteraction("drag");
  }

  function handleBlurPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (blurInteraction !== "drag") return;
    const frame = previewFrameRef.current?.getBoundingClientRect();
    const drag = blurDragRef.current;
    if (!frame || !drag) return;
    const x = clampNumber(
      (event.clientX - drag.offsetX - frame.left) / Math.max(1, frame.width),
      blurWidth / 2,
      1 - blurWidth / 2,
    );
    const y = clampNumber(
      (event.clientY - drag.offsetY - frame.top) / Math.max(1, frame.height),
      blurHeight / 2,
      1 - blurHeight / 2,
    );
    updateSubtitleStyle({
      hard_sub_blur_x: roundPreviewValue(x),
      hard_sub_blur_y: roundPreviewValue(y),
    });
  }

  function handleBlurScalePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    blurScaleRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      width: blurWidth,
      height: blurHeight,
    };
    setBlurInteraction("scale");
  }

  function handleBlurScalePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (blurInteraction !== "scale") return;
    const frame = previewFrameRef.current?.getBoundingClientRect();
    const scale = blurScaleRef.current;
    if (!frame || !scale) return;
    event.stopPropagation();
    // Allow growing to the full frame, then re-center so the box stays on-screen.
    // Previously the size was hard-capped by the *nearer* edge (2 * min(x, 1-x)), so an
    // off-center box hit one wall and could never reach the far side. Letting it grow and
    // recentring means dragging out simply extends it to both edges.
    const width = clampNumber(
      scale.width + (event.clientX - scale.startX) * 2 / Math.max(1, frame.width),
      0.08,
      1,
    );
    const height = clampNumber(
      scale.height + (event.clientY - scale.startY) * 2 / Math.max(1, frame.height),
      0.05,
      0.8,
    );
    const x = clampNumber(blurX, width / 2, 1 - width / 2);
    const y = clampNumber(blurY, height / 2, 1 - height / 2);
    updateSubtitleStyle({
      hard_sub_blur_width: roundPreviewValue(width),
      hard_sub_blur_height: roundPreviewValue(height),
      hard_sub_blur_x: roundPreviewValue(x),
      hard_sub_blur_y: roundPreviewValue(y),
    });
  }

  function stopBlurInteraction(event: ReactPointerEvent<HTMLElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    blurDragRef.current = null;
    blurScaleRef.current = null;
    setBlurInteraction(null);
  }

  function applyPosition(position: CreateDraft["subtitle"]["position"]) {
    const positionY = position === "top" ? 0.16 : position === "middle" ? 0.5 : 0.88;
    const positionX = Number(effectiveStyle.position_x ?? 0.5);
    patch("subtitle", {
      ...subtitle,
      position,
      style_overrides: {
        ...(subtitle.style_overrides ?? {}),
        position_x: positionX,
        position_y: positionY,
        alignment: alignmentFromCoordinates(positionX, positionY),
      },
    });
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(520px,1.2fr)_360px]">
      <EditorSection
        title={t.sub_preview_title}
        description={t.sub_preview_desc}
        actions={(
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="secondary"
              loading={previewLoading}
              leadingIcon={previewUrl ? <Play size={15} /> : <Download size={15} />}
              onClick={handlePreviewClick}
            >
              {previewUrl ? t.sub_btn_preview : t.sub_btn_prepare}
            </Button>
            {previewUrl ? (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                loading={previewDownloading}
                leadingIcon={<Download size={15} />}
                onClick={() => void downloadPreview()}
              >
                {t.sub_btn_download}
              </Button>
            ) : null}
          </div>
        )}
      >
        {previewError ? (
          <div className="mb-4">
            <InlineNotice title={t.sub_preview_unavail} tone="warning">{previewError}</InlineNotice>
          </div>
        ) : null}
        <div className="relative flex min-h-[360px] items-center justify-center overflow-hidden rounded-[var(--radius-panel-sm)] bg-[#211f1a] p-4 sm:min-h-[420px]">
          <div
            ref={previewFrameRef}
            className="relative max-w-full overflow-hidden rounded-[var(--radius-panel-sm)] bg-black shadow-[0_18px_50px_rgba(18,14,8,.22)]"
            style={{
              aspectRatio: `${previewRatio}`,
              containerType: "size",
              width: previewRatio >= 1.3 ? "100%" : `min(100%, ${Math.round(520 * previewRatio)}px)`,
            }}
          >
            {previewUrl ? (
              <video
                ref={previewVideoRef}
                className={`h-full w-full ${previewIsCropped ? "object-cover" : "object-contain"}`}
                muted
                playsInline
                preload="metadata"
                src={previewUrl}
                onLoadedMetadata={(event) => {
                  onPreviewMetadata(event.currentTarget);
                  setDuration(event.currentTarget.duration || 0);
                }}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
              />
            ) : (
              <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_25%,#766c58,#292722_72%)]" />
            )}
            <svg aria-hidden width="0" height="0" style={{ position: "absolute" }}>
              <defs>
                <filter id="hardsub-blur-vertical" x="-20%" y="-20%" width="140%" height="140%" colorInterpolationFilters="sRGB">
                  <feGaussianBlur stdDeviation={`0 ${previewBlurDev}`} />
                </filter>
                <filter id="hardsub-blur-horizontal" x="-20%" y="-20%" width="140%" height="140%" colorInterpolationFilters="sRGB">
                  <feGaussianBlur stdDeviation={`${previewBlurDev} 0`} />
                </filter>
              </defs>
            </svg>
            {blurEnabled ? (
              <div
                ref={blurBoxRef}
                className={`absolute z-[5] touch-none select-none border-2 border-dashed border-[var(--primary)] bg-white/10 ${
                  blurInteraction === "drag" ? "cursor-grabbing" : "cursor-grab"
                }`}
                style={{
                  left: `${blurX * 100}%`,
                  top: `${blurY * 100}%`,
                  width: `${blurWidth * 100}%`,
                  height: `${blurHeight * 100}%`,
                  transform: "translate(-50%, -50%)",
                  backdropFilter: blurBackdropCss,
                  WebkitBackdropFilter: blurBackdropCss,
                }}
                onPointerDown={handleBlurPointerDown}
                onPointerMove={handleBlurPointerMove}
                onPointerUp={stopBlurInteraction}
                onPointerCancel={stopBlurInteraction}
              >
                <span className="pointer-events-none absolute left-2 top-1 rounded bg-black/65 px-2 py-0.5 text-[10px] font-bold uppercase text-white">
                  {t.sub_blur_label}
                </span>
                <span className="pointer-events-none absolute -left-3 -top-3 flex h-7 w-7 items-center justify-center rounded-full bg-[var(--primary)] text-white shadow-md">
                  <Move size={14} />
                </span>
                <div
                  className="absolute -bottom-3 -right-3 flex h-7 w-7 cursor-nwse-resize touch-none items-center justify-center rounded-full border-2 border-white bg-[var(--primary)] text-white shadow-md"
                  role="button"
                  aria-label={t.sub_blur_resize_title}
                  title={t.sub_blur_resize_title}
                  onPointerDown={handleBlurScalePointerDown}
                  onPointerMove={handleBlurScalePointerMove}
                  onPointerUp={stopBlurInteraction}
                  onPointerCancel={stopBlurInteraction}
                >
                  <Scaling size={13} />
                </div>
              </div>
            ) : null}
            {!subtitlesDisabled ? (
              <div
                ref={subtitleBoxRef}
                className={`absolute z-10 touch-none select-none ${
                  interaction === "drag"
                    ? "cursor-grabbing ring-2 ring-[var(--primary)]"
                    : "cursor-grab ring-1 ring-white/35"
                }`}
                style={subtitlePreviewStyle(
                  effectiveStyle,
                  undefined,
                  bilingualEnabled ? subtitle.max_lines * 2 : subtitle.max_lines,
                )}
                onPointerDown={handleSubtitlePointerDown}
                onPointerMove={handleSubtitlePointerMove}
                onPointerUp={stopSubtitleInteraction}
                onPointerCancel={stopSubtitleInteraction}
              >
                <span className="box-decoration-clone px-[0.45em] py-[0.18em]">
                  {t.sub_sample_text}
                </span>
                {bilingualEnabled ? (
                  <>
                    {/* Mirrors the renderer's \N break. Both spans are inline
                        inside the -webkit-box, so without an explicit break the
                        source line continues on the translated line, not under it. */}
                    <br />
                    <span
                      className="box-decoration-clone px-[0.45em] py-[0.18em]"
                      style={subtitleSecondaryPreviewStyle(effectiveStyle)}
                    >
                      {t.sub_sample_text_secondary}
                    </span>
                  </>
                ) : null}
                <span className="pointer-events-none absolute -left-3 -top-3 flex h-7 w-7 items-center justify-center rounded-full bg-[var(--primary)] text-white shadow-md">
                  <Move size={14} />
                </span>
                <div
                  className={`absolute -bottom-3 -right-3 flex h-7 w-7 touch-none items-center justify-center rounded-full border-2 border-white bg-[var(--primary)] text-white shadow-md ${
                    interaction === "scale" ? "cursor-nwse-resize ring-2 ring-[var(--primary-soft)]" : "cursor-nwse-resize"
                  }`}
                  role="button"
                  aria-label="Resize subtitle"
                  title="Drag to resize subtitle"
                  onPointerDown={handleScalePointerDown}
                  onPointerMove={handleScalePointerMove}
                  onPointerUp={stopSubtitleInteraction}
                  onPointerCancel={stopSubtitleInteraction}
                >
                  <Scaling size={13} />
                </div>
              </div>
            ) : (
              <div className="absolute inset-x-0 bottom-5 text-center text-xs font-semibold text-white/70">
                {t.sub_disabled_label}
              </div>
            )}
            <span className="pointer-events-none absolute left-3 top-3 rounded-full bg-black/65 px-2.5 py-1 text-[11px] font-bold text-white backdrop-blur-sm">
              {previewRatioLabel}
            </span>
            {!previewUrl ? (
              <span className="pointer-events-none absolute inset-x-4 top-1/2 -translate-y-1/2 text-center text-xs font-semibold text-white/65">
                {t.sub_no_video_hint}
              </span>
            ) : null}
          </div>
        </div>
        {previewUrl ? (
          <div className="mt-2 flex items-center gap-3 rounded-[var(--radius-panel-sm)] bg-[var(--surface-muted)] px-3 py-2">
            <button
              type="button"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--surface)] text-[var(--text-primary)] hover:bg-[var(--border)]"
              onClick={handlePreviewClick}
              title={playing ? "Pause" : "Play"}
            >
              {playing ? <Pause size={14} /> : <Play size={14} />}
            </button>
            <span className="w-10 shrink-0 font-mono text-xs tabular-nums text-[var(--text-secondary)]">
              {formatSeconds(currentTime)}
            </span>
            <div
              className="relative flex h-4 flex-1 cursor-pointer items-center"
              onClick={(e) => {
                if (!duration) return;
                const rect = e.currentTarget.getBoundingClientRect();
                const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                if (previewVideoRef.current) previewVideoRef.current.currentTime = ratio * duration;
              }}
            >
              <div className="h-1 w-full overflow-hidden rounded-full bg-[var(--border)]">
                <div className="h-full rounded-full bg-[var(--primary)]" style={{ width: `${playheadPercent}%` }} />
              </div>
              <div
                className="pointer-events-none absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow ring-1 ring-[var(--border)]"
                style={{ left: `${playheadPercent}%` }}
              />
            </div>
            <span className="w-10 shrink-0 text-right font-mono text-xs tabular-nums text-[var(--text-secondary)]">
              {formatSeconds(duration)}
            </span>
          </div>
        ) : null}
        {blurEnabled ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--text-secondary)]">
            <span>{t.sub_blur_hint}</span>
            <span className="font-mono">
              x {blurX.toFixed(2)} · y {blurY.toFixed(2)} · w {blurWidth.toFixed(2)} · h {blurHeight.toFixed(2)}
              {effectiveStyle.hard_sub_blur_style && effectiveStyle.hard_sub_blur_style !== "box"
                ? ` · ${effectiveStyle.hard_sub_blur_style}`
                : ""}
            </span>
          </div>
        ) : null}
        {!subtitlesDisabled ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--text-secondary)]">
            <span>{t.sub_drag_hint}</span>
            <span className="font-mono">
              x {Number(effectiveStyle.position_x ?? 0.5).toFixed(2)} · y {Number(effectiveStyle.position_y ?? 0.88).toFixed(2)} · {Math.round(Number(effectiveStyle.font_size ?? 20))}px
            </span>
          </div>
        ) : null}
        {!subtitlesDisabled ? (
          <div className="mt-4 border-t border-[var(--border)] pt-4">
            {showSaveStyle ? (
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <TextField
                  className="flex-1"
                  value={styleNameDraft}
                  autoFocus
                  maxLength={60}
                  placeholder={t.sub_style_name_ph}
                  onChange={(event) => setStyleNameDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void saveCurrentStyle();
                    if (event.key === "Escape") { setShowSaveStyle(false); setSaveStyleError(""); }
                  }}
                />
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="primary"
                    loading={savingStyle}
                    disabled={!styleNameDraft.trim()}
                    leadingIcon={<Save size={16} />}
                    onClick={() => void saveCurrentStyle()}
                  >
                    {t.sub_style_save_action}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => { setShowSaveStyle(false); setSaveStyleError(""); }}
                  >
                    {t.sub_style_cancel}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="min-w-0 text-xs text-[var(--text-secondary)]">
                  {styleSaved ? (
                    <span className="font-semibold text-emerald-600">✓ {t.sub_style_saved}</span>
                  ) : (
                    t.sub_style_save_hint
                  )}
                </p>
                <Button
                  type="button"
                  variant="primary"
                  leadingIcon={<Save size={16} />}
                  onClick={() => { setStyleNameDraft(""); setShowSaveStyle(true); }}
                >
                  {t.sub_style_save_btn}
                </Button>
              </div>
            )}
            {saveStyleError ? <p className="mt-2 text-xs text-red-600">{saveStyleError}</p> : null}
          </div>
        ) : null}
      </EditorSection>
      <EditorSection title={t.sub_settings_title}>
        <div className="space-y-3.5">
          <Field label={t.sub_ratio_label}>
            <Select
              value={draft.output.aspect_ratio}
              onChange={(event) => patch("output", {
                ...draft.output,
                aspect_ratio: event.target.value as CreateDraft["output"]["aspect_ratio"],
              })}
            >
              <option value="source">{tt(t.sub_ratio_source, { r: formatAspectRatio(sourceAspectRatio) })}</option>
              <option value="16:9">16:9 landscape</option>
              <option value="9:16">9:16 vertical</option>
              <option value="1:1">1:1 square</option>
            </Select>
          </Field>
          <Field label={t.sub_style_label}>
            <Select value={subtitle.style_id} onChange={(event) => patch("subtitle", {
              ...subtitle,
              style_id: event.target.value,
              style_overrides: {},
            })}>
              <option value="none">{t.sub_style_none}</option>
              {!hasSelectedStyle && subtitle.style_id && !subtitlesDisabled ? (
                <option value={subtitle.style_id}>{subtitle.style_id} {t.sub_style_snapshot}</option>
              ) : null}
              {styles.length ? styles.map((style) => (
                <option key={style.id} value={style.id}>
                  {style.name}{style.id === activeStyleId ? ` ${t.sub_style_active}` : ""}
                </option>
              )) : <option value={subtitle.style_id || "default"}>Default Bottom Box</option>}
            </Select>
          </Field>
          {libraryError ? (
            <InlineNotice title={t.sub_style_lib_err_title} tone="warning">
              {t.sub_style_lib_err_body} {libraryError}
            </InlineNotice>
          ) : null}
          {!subtitlesDisabled ? (
            <>
              <SegmentedControl label={t.sub_position_label} value={subtitle.position} onChange={(position) => applyPosition(position as CreateDraft["subtitle"]["position"])} options={[{ value: "top", label: t.sub_pos_top }, { value: "middle", label: t.sub_pos_middle }, { value: "bottom", label: t.sub_pos_bottom }]} />
              <div className="grid grid-cols-2 items-end gap-3">
                <Slider
                  label={t.sub_outline_width}
                  value={Number(effectiveStyle.outline ?? 0)}
                  min={0}
                  max={4}
                  step={0.25}
                  suffix="px"
                  onChange={(outline) => updateSubtitleStyle({ outline })}
                />
                <SubtitleColorField
                  label={t.sub_outline_color}
                  value={String(effectiveStyle.outline_color ?? "#000000")}
                  fallback="#000000"
                  onChange={(outline_color) => updateSubtitleStyle({ outline_color })}
                />
              </div>
              <div className="grid grid-cols-[1fr_auto] items-center gap-3 border-t border-[var(--border)] pt-3.5">
                <div>
                  <p className="text-sm font-bold text-[var(--text-primary)]">{t.sub_bilingual_label}</p>
                  <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{t.sub_bilingual_desc}</p>
                </div>
                <CompactToggle
                  checked={bilingualEnabled}
                  onChange={(bilingual_enabled) => updateSubtitleStyle({ bilingual_enabled })}
                  label={t.sub_bilingual_label}
                />
                {bilingualEnabled ? (
                  <div className="col-span-2 grid grid-cols-2 items-end gap-3">
                    <Slider
                      label={t.sub_bilingual_size}
                      value={Math.round(Number(effectiveStyle.bilingual_font_scale ?? 0.72) * 100)}
                      min={40}
                      max={100}
                      step={2}
                      suffix="%"
                      onChange={(percent) => updateSubtitleStyle({ bilingual_font_scale: percent / 100 })}
                    />
                    <SubtitleColorField
                      label={t.sub_bilingual_color}
                      value={String(effectiveStyle.bilingual_color ?? "#444444")}
                      fallback="#444444"
                      onChange={(bilingual_color) => updateSubtitleStyle({ bilingual_color })}
                    />
                  </div>
                ) : null}
              </div>
              <div className="grid grid-cols-[1fr_auto] items-center gap-3 border-t border-[var(--border)] pt-3.5">
                <div>
                  <p className="text-sm font-bold text-[var(--text-primary)]">{t.sub_solid_box_label}</p>
                  <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{t.sub_solid_box_desc}</p>
                </div>
                <CompactToggle
                  checked={Number(effectiveStyle.box_opacity ?? 1) > 0}
                  onChange={(enabled) => updateSubtitleStyle({ box_opacity: enabled ? 1 : 0 })}
                  label={t.sub_solid_box_label}
                />
                <Button
                  type="button"
                  className="col-span-2 w-full"
                  variant="secondary"
                  leadingIcon={<ScanLine size={16} />}
                  onClick={() => {
                    const enabling = !blurEnabled;
                    updateSubtitleStyle({
                      hard_sub_blur_enabled: enabling,
                      // When first enabling, snap blur box center to subtitle center
                      // so both use the same (y1+y2)/2 reference point.
                      hard_sub_blur_x: enabling ? Number(effectiveStyle.position_x ?? 0.5) : blurX,
                      hard_sub_blur_y: enabling ? Number(effectiveStyle.position_y ?? 0.86) : blurY,
                      hard_sub_blur_width: blurWidth,
                      hard_sub_blur_height: blurHeight,
                    });
                  }}
                >
                  {blurEnabled ? t.sub_remove_blur : t.sub_add_blur}
                </Button>
                {blurEnabled ? (
                  <div className="col-span-2 space-y-2.5">
                    <SegmentedControl
                      label={t.sub_blur_style_label}
                      value={String(effectiveStyle.hard_sub_blur_style ?? "box")}
                      onChange={(style) => updateSubtitleStyle({
                        hard_sub_blur_style: style as NonNullable<SubtitleStyle["hard_sub_blur_style"]>,
                      })}
                      options={[
                        { value: "box", label: t.sub_blur_style_box },
                        { value: "vertical", label: t.sub_blur_style_vertical },
                        { value: "horizontal", label: t.sub_blur_style_horizontal },
                      ]}
                    />
                    <Slider
                      label={t.sub_blur_strength_label}
                      value={Number(effectiveStyle.hard_sub_blur_strength ?? 50)}
                      min={0}
                      max={250}
                      suffix="%"
                      onChange={(strength) => updateSubtitleStyle({ hard_sub_blur_strength: strength })}
                    />
                  </div>
                ) : null}
              </div>
              <div className="space-y-3 border-t border-[var(--border)] pt-3.5">
                <p className="text-xs font-extrabold uppercase tracking-[0.08em] text-[var(--text-muted)]">
                  {t.sub_typography}
                </p>
                <Field label={t.sub_font_label}>
                  <Select
                    value={String(effectiveStyle.font_name ?? "Arial")}
                    onChange={(event) => updateSubtitleStyle({ font_name: event.target.value })}
                  >
                    {effectiveStyle.font_name && !SUBTITLE_FONT_OPTIONS.includes(
                      effectiveStyle.font_name as (typeof SUBTITLE_FONT_OPTIONS)[number],
                    ) ? (
                      <option value={effectiveStyle.font_name}>{effectiveStyle.font_name} (current)</option>
                    ) : null}
                    {SUBTITLE_FONT_OPTIONS.map((font) => (
                      <option key={font} value={font} style={{ fontFamily: font }}>
                        {font}
                      </option>
                    ))}
                  </Select>
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <SubtitleColorField
                    label={t.sub_color_text}
                    value={String(effectiveStyle.text_color ?? "#FFFFFF")}
                    fallback="#FFFFFF"
                    onChange={(text_color) => updateSubtitleStyle({ text_color })}
                  />
                  <SubtitleColorField
                    label={t.sub_color_box}
                    value={String(effectiveStyle.box_color ?? "#000000")}
                    fallback="#000000"
                    onChange={(box_color) => updateSubtitleStyle({ box_color })}
                  />
                </div>
              </div>
            </>
          ) : (
            <InlineNotice title={t.sub_no_sub_title} tone="info">
              {t.sub_no_sub_body}
            </InlineNotice>
          )}
        </div>
      </EditorSection>
    </div>
  );
}

function CompactToggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full border transition ${
        checked
          ? "border-[var(--primary)] bg-[var(--primary)]"
          : "border-[var(--border-strong)] bg-[var(--surface-muted)]"
      }`}
    >
      <span
        className={`absolute top-[3px] h-4 w-4 rounded-full bg-white shadow-sm transition ${
          checked ? "left-[22px]" : "left-[3px]"
        }`}
      />
    </button>
  );
}

function SubtitleColorField({
  label,
  value,
  fallback,
  onChange,
}: {
  label: string;
  value: string;
  fallback: string;
  onChange: (value: string) => void;
}) {
  const normalizedValue = normalizeHexColor(value, fallback);
  const [draftValue, setDraftValue] = useState(normalizedValue);

  useEffect(() => {
    setDraftValue(normalizedValue);
  }, [normalizedValue]);

  return (
    <label className="space-y-2">
      <span className="block text-sm font-bold text-[var(--text-primary)]">{label}</span>
      <span className="flex h-12 items-center gap-2 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface)] px-2.5 transition focus-within:border-[var(--primary)]">
        <input
          type="color"
          value={normalizedValue}
          onChange={(event) => {
            const nextValue = event.target.value.toUpperCase();
            setDraftValue(nextValue);
            onChange(nextValue);
          }}
          className="h-8 w-8 shrink-0 cursor-pointer rounded border-0 bg-transparent p-0"
          aria-label={`${label} color picker`}
        />
        <input
          type="text"
          value={draftValue}
          maxLength={7}
          spellCheck={false}
          onChange={(event) => {
            const nextValue = event.target.value.toUpperCase();
            setDraftValue(nextValue);
            if (/^#[0-9A-F]{6}$/.test(nextValue)) onChange(nextValue);
          }}
          onBlur={() => setDraftValue(normalizedValue)}
          className="min-w-0 flex-1 bg-transparent font-mono text-sm uppercase text-[var(--text-primary)] outline-none"
          aria-label={`${label} hex color`}
        />
      </span>
    </label>
  );
}

function OutputStep({ draft, patch }: DraftStepProps) {
  const { t } = useT();
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <EditorSection title={t.out_audio_title} description={t.out_audio_desc}>
        <div className="space-y-6">
          <Slider label={t.out_orig_vol} value={draft.audio.original_volume} min={0} max={100} suffix="%" onChange={(original_volume) => patch("audio", { ...draft.audio, original_volume })} />
          <Slider label={t.out_dub_vol} value={draft.audio.dubbed_volume} min={0} max={120} suffix="%" onChange={(dubbed_volume) => patch("audio", { ...draft.audio, dubbed_volume })} />
          <Switch checked={draft.audio.ducking} onChange={(ducking) => patch("audio", { ...draft.audio, ducking })} label={t.out_duck_label} description={t.out_duck_desc} />
          <div className="flex items-center gap-3 rounded-[var(--radius-panel-sm)] bg-[var(--surface-muted)] p-4">
            <Volume2 size={18} className="text-[var(--primary)]" />
            <span className="text-sm">{tt(t.out_orig_voice, { o: draft.audio.original_volume, d: draft.audio.dubbed_volume })}</span>
          </div>
        </div>
      </EditorSection>
      <EditorSection title={t.out_title}>
        <div className="grid gap-5 sm:grid-cols-2">
<Field label={t.out_render_quality}><Select value={draft.output.render_quality} onChange={(event) => patch("output", { ...draft.output, render_quality: event.target.value as CreateDraft["output"]["render_quality"] })}><option value="fast">{t.out_render_fast}</option><option value="balanced">{t.out_render_balanced}</option><option value="quality">{t.out_render_hq}</option></Select></Field>
          <Field label={t.out_aspect_ratio}><Select value={draft.output.aspect_ratio} onChange={(event) => patch("output", { ...draft.output, aspect_ratio: event.target.value as CreateDraft["output"]["aspect_ratio"] })}><option value="source">{t.out_aspect_source}</option><option value="16:9">16:9 landscape</option><option value="9:16">9:16 vertical</option><option value="1:1">1:1 square</option></Select></Field>
          <Field label={t.out_platform}><Select value={draft.output.platform} onChange={(event) => patch("output", { ...draft.output, platform: event.target.value })}><option>YouTube</option><option>Facebook</option><option>TikTok</option><option>Douyin</option><option>Vimeo</option></Select></Field>
          <Field label={t.out_format}><Select value={draft.output.format} onChange={(event) => patch("output", { ...draft.output, format: event.target.value as "mp4" | "webm" })}><option value="mp4">MP4</option><option value="webm">WebM</option></Select></Field>
        </div>
      </EditorSection>
    </div>
  );
}

function ReviewStep({
  draft,
  completed,
  submitting,
  submitError,
  onCreate,
}: {
  draft: CreateDraft;
  completed: "draft" | "run" | null;
  submitting: "draft" | "run" | null;
  submitError: string;
  onCreate: (run: boolean) => void;
}) {
  const { t } = useT();

  const groups = useMemo(
    () => {
      const fidelity: Record<string, string> = { strict: t.trans_strict, balanced: t.trans_balanced, natural: t.trans_natural };
      const pos: Record<string, string> = { top: t.sub_pos_top, middle: t.sub_pos_middle, bottom: t.sub_pos_bottom };
      const quality: Record<string, string> = { fast: t.out_render_fast, balanced: t.out_render_balanced, quality: t.out_render_hq };
      const aspect: Record<string, string> = { source: t.out_aspect_source };
      const sourceName = draft.source.method === "url"
        ? (draft.source.title || draft.source.url || t.rev_no_source)
        : (draft.source.upload_name || t.rev_no_source);
      const testClip: Record<number, string> = { 30: t.src_test_30s, 60: t.src_test_1m, 120: t.src_test_2m, 300: t.src_test_5m };
      const clipSeconds = Number(draft.source.test_clip_seconds ?? 0);
      const sourceDetail = clipSeconds > 0
        ? `${draft.languages.source} → ${draft.languages.target} · ${testClip[clipSeconds] ?? `${clipSeconds}s`}`
        : `${draft.languages.source} → ${draft.languages.target}`;
      return [
        [t.rev_label_source, sourceName, sourceDetail],
        [t.rev_label_voice, draft.voice.voice_id, `${draft.voice.rate >= 0 ? "+" : ""}${draft.voice.rate}%`],
        [t.rev_label_translation, draft.translation.tone, `${fidelity[draft.translation.semantic_fidelity] ?? draft.translation.semantic_fidelity} · ${draft.translation.remove_sound_tags ? t.rev_tags_removed : t.rev_tags_kept}`],
        [t.rev_label_subtitle, draft.subtitle.style_id, `${pos[draft.subtitle.position] ?? draft.subtitle.position} · ${t.sub_outline_width} ${Number(draft.subtitle.style_overrides.outline ?? 0)}px`],
        [t.rev_label_audio_output, `${draft.audio.original_volume}% / ${draft.audio.dubbed_volume}%`, `${aspect[draft.output.aspect_ratio] ?? draft.output.aspect_ratio} · ${quality[draft.output.render_quality] ?? draft.output.render_quality} · ${draft.output.platform}`],
      ];
    },
    [draft, t],
  );
  return (
    <EditorSection title={t.rev_title} description={t.rev_desc}>
      {completed ? (
        <div className="mb-5"><InlineNotice title={t.rev_draft_ok_title} tone="success">{t.rev_draft_ok_body}</InlineNotice></div>
      ) : null}
      {submitError ? <div className="mb-5"><InlineNotice title={t.rev_err_title} tone="danger">{submitError}</InlineNotice></div> : null}
      <div className="grid gap-3 md:grid-cols-2">
        {groups.map(([label, value, detail]) => (
          <div key={label} className="rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-4">
            <span className="text-[11px] font-extrabold uppercase tracking-[0.1em] text-[var(--text-tertiary)]">{label}</span>
            <strong className="mt-2 block text-sm">{value}</strong>
            <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">{detail}</span>
          </div>
        ))}
      </div>
      <div className="mt-6 flex flex-wrap gap-3">
        <Button disabled={Boolean(submitting)} variant="secondary" leadingIcon={<Save size={16} />} onClick={() => onCreate(false)}>
          {submitting === "draft" ? t.rev_creating : t.rev_create_draft}
        </Button>
        <Button disabled={Boolean(submitting)} variant="primary" leadingIcon={<Check size={16} />} onClick={() => onCreate(true)}>
          {submitting === "run" ? t.rev_creating_run : t.rev_create_run}
        </Button>
      </div>
    </EditorSection>
  );
}

function resolveBrowserVideoUrl(value?: string | null) {
  const source = (value ?? "").trim();
  if (!source) return "";
  if (source.startsWith("blob:")) return source;
  if (source.startsWith("storage/")) return outputUrl(`/${source}`) ?? "";
  if (source.startsWith("/storage/")) return outputUrl(source) ?? "";
  if (!source.startsWith("http://") && !source.startsWith("https://")) return "";

  try {
    const pathname = new URL(source).pathname.toLowerCase();
    return /\.(mp4|m4v|mov|webm|ogg|ogv)$/i.test(pathname) ? source : "";
  } catch {
    return "";
  }
}

function resolvePreviewAspectRatio(
  outputRatio: CreateDraft["output"]["aspect_ratio"],
  sourceRatio: number,
) {
  if (outputRatio === "16:9") return 16 / 9;
  if (outputRatio === "9:16") return 9 / 16;
  if (outputRatio === "1:1") return 1;
  return Number.isFinite(sourceRatio) && sourceRatio > 0 ? sourceRatio : 16 / 9;
}

function formatAspectRatio(ratio: number) {
  const knownRatios: Array<[number, string]> = [
    [16 / 9, "16:9"],
    [9 / 16, "9:16"],
    [4 / 3, "4:3"],
    [3 / 4, "3:4"],
    [1, "1:1"],
  ];
  const match = knownRatios.find(([value]) => Math.abs(value - ratio) < 0.03);
  return match?.[1] ?? `${ratio.toFixed(2)}:1`;
}

function alignmentFromCoordinates(positionX: number, positionY: number) {
  const vertical = positionY <= 0.33 ? "top" : positionY < 0.67 ? "middle" : "bottom";
  const horizontal = positionX <= 0.33 ? "left" : positionX < 0.67 ? "center" : "right";
  return `${vertical}_${horizontal}`;
}

function clampNumber(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function normalizeHexColor(value: string, fallback: string) {
  const normalized = value.trim().toUpperCase();
  return /^#[0-9A-F]{6}$/.test(normalized) ? normalized : fallback;
}

function roundPreviewValue(value: number) {
  return Math.round(value * 1000) / 1000;
}

function formatSeconds(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

type DraftStepProps = {
  draft: CreateDraft;
  patch: <K extends keyof CreateDraft>(key: K, value: CreateDraft[K]) => void;
};

function NumberField({ label, value, onChange, min = 1, max = 20 }: { label: string; value: number | null; onChange: (value: number | null) => void; min?: number; max?: number }) {
  return (
    <Field label={label}>
      <TextField type="number" min={min} max={max} placeholder="Auto" value={value ?? ""} onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)} />
    </Field>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-[var(--text-tertiary)]">{label}</dt><dd className="mt-1 font-semibold">{value}</dd></div>;
}

function mergeVoiceProfiles(primary: VoiceProfile[], fallback: VoiceProfile[]) {
  const profiles = new Map<string, VoiceProfile>();
  for (const profile of [...primary, ...fallback]) {
    if (!profiles.has(profile.id)) profiles.set(profile.id, profile);
  }
  return [...profiles.values()];
}
