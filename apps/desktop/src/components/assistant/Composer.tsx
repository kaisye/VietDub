import clsx from "clsx";
import { ArrowUp, Film, Paperclip, Pencil, Sparkles, Square, Target, X } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useT } from "../../i18n";
import type { AssistantMode, ChatOptions, Option } from "../../lib/assistant-api";
import type { Job } from "../../types";
import { JobPicker, jobTitle } from "./JobPicker";

export type ComposerHandle = { focus: () => void; setText: (text: string) => void };

function PillSelect({
  label,
  value,
  options,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <label className="relative inline-flex items-center">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        title={label}
        className={clsx(
          "h-7 min-h-0! max-w-[150px] cursor-pointer appearance-none truncate rounded-full! border bg-[var(--surface)]! pl-2.5 pr-7! text-[12px] font-medium outline-none transition [background-position:right_9px_center]! [background-size:12px]! focus-visible:shadow-[var(--shadow-focus)]",
          value
            ? "border-[var(--primary-soft-strong)]! text-[var(--primary-hover)]!"
            : "border-[var(--border)]! text-[var(--text-secondary)]! hover:border-[var(--border-strong)]!",
        )}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export const Composer = forwardRef<
  ComposerHandle,
  {
    mode: AssistantMode;
    onModeChange: (mode: AssistantMode) => void;
    options: ChatOptions;
    onOptionsChange: (options: ChatOptions) => void;
    lists: { platforms: Option[]; tones: Option[]; lengths: Option[] };
    job: Job | null;
    jobId: string | null;
    onJobChange: (job: Job | null) => void;
    jobs: Job[];
    jobsLoading: boolean;
    jobsError: string | null;
    onReloadJobs: () => void;
    pickerOpen: boolean;
    onPickerOpenChange: (open: boolean) => void;
    streaming: boolean;
    disabled: boolean;
    onSend: (text: string) => void;
    onStop: () => void;
    banner?: ReactNode;
  }
>(function Composer(
  {
    mode,
    onModeChange,
    options,
    onOptionsChange,
    lists,
    job,
    jobId,
    onJobChange,
    jobs,
    jobsLoading,
    jobsError,
    onReloadJobs,
    pickerOpen,
    onPickerOpenChange,
    streaming,
    disabled,
    onSend,
    onStop,
    banner,
  },
  ref,
) {
  const { t } = useT();
  const [text, setText] = useState("");
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);

  useImperativeHandle(ref, () => ({
    focus: () => areaRef.current?.focus(),
    setText: (value: string) => {
      setText(value);
      requestAnimationFrame(() => {
        const el = areaRef.current;
        if (el) {
          el.focus();
          el.setSelectionRange(value.length, value.length);
        }
      });
    },
  }));

  // Auto-grow up to ~8 lines.
  useLayoutEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  useEffect(() => {
    if (!disabled) areaRef.current?.focus();
  }, [disabled]);

  const canSend = !disabled && !streaming && text.trim().length > 0;
  const submit = () => {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  };

  const modes: Array<{ id: AssistantMode; label: string; icon: typeof Sparkles; hint: string }> = [
    { id: "chat", label: t.as_mode_chat, icon: Sparkles, hint: t.as_mode_chat_hint },
    { id: "write", label: t.as_mode_write, icon: Pencil, hint: t.as_mode_write_hint },
    { id: "strategy", label: t.as_mode_strategy, icon: Target, hint: t.as_mode_strategy_hint },
  ];
  const writePlatforms = lists.platforms;
  const placeholder = mode === "write" ? t.as_ph_write : mode === "strategy" ? t.as_ph_strategy : t.as_ph_chat;

  return (
    <div className="mx-auto w-full max-w-[820px]">
      {banner}
      <div
        className={clsx(
          "relative rounded-[18px] border bg-[var(--surface)] shadow-[var(--shadow-float)] transition-[border-color,box-shadow]",
          disabled
            ? "border-[var(--border)]"
            : "border-[var(--border-strong)] focus-within:border-[var(--primary)] focus-within:shadow-[0_0_0_4px_var(--primary-ring),var(--shadow-float)]",
        )}
      >
        {job || jobId ? (
          <div className="flex px-3 pt-3">
            <span className="inline-flex max-w-full items-center gap-2 rounded-[10px] border border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] py-1 pl-1 pr-1.5 text-[12.5px] text-[var(--primary-ink)]">
              <span className="flex h-6 w-6 items-center justify-center overflow-hidden rounded-md bg-[var(--navy-900)] text-white/80">
                {job?.thumbnail_url ? <img src={job.thumbnail_url} alt="" className="h-full w-full object-cover" /> : <Film size={12} />}
              </span>
              <span className="truncate font-medium">{job ? jobTitle(job) : t.as_attached_job}</span>
              <button
                type="button"
                onClick={() => onJobChange(null)}
                aria-label={t.as_attach_remove}
                title={t.as_attach_remove}
                className="rounded p-0.5 text-[var(--primary)] hover:bg-[var(--primary-soft-strong)]"
              >
                <X size={13} />
              </button>
            </span>
          </div>
        ) : null}
        <textarea
          ref={areaRef}
          value={text}
          rows={1}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onCompositionStart={() => (composing.current = true)}
          onCompositionEnd={() => (composing.current = false)}
          onKeyDown={(e) => {
            // Vietnamese IMEs (Telex/VNI) compose text: never submit mid-composition.
            if (e.key === "Enter" && !e.shiftKey && !composing.current && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={disabled ? t.as_ph_disabled : placeholder}
          aria-label={t.as_composer_label}
          className="block max-h-[220px] min-h-[52px] w-full resize-none bg-transparent px-4 pb-1 pt-3.5 text-[14.5px] leading-6 text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)] disabled:cursor-not-allowed"
        />
        <div className="flex flex-wrap items-center gap-2 px-2.5 pb-2.5 pt-1">
          <div className="flex items-center gap-0.5 rounded-full bg-[var(--surface-sunken)] p-0.5" role="radiogroup" aria-label={t.as_mode}>
            {modes.map((m) => {
              const Icon = m.icon;
              const active = mode === m.id;
              return (
                <button
                  key={m.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  title={m.hint}
                  onClick={() => onModeChange(m.id)}
                  className={clsx(
                    "inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-semibold transition",
                    active
                      ? "bg-[var(--surface)] text-[var(--primary-hover)] shadow-[var(--shadow-sm)]"
                      : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
                  )}
                >
                  <Icon size={13} />
                  {m.label}
                </button>
              );
            })}
          </div>

          {mode === "write" ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <PillSelect
                label={t.as_platform}
                value={options.platform ?? ""}
                options={writePlatforms}
                onChange={(platform) => onOptionsChange({ ...options, platform: platform || undefined })}
                placeholder={t.as_platform_any}
              />
              <PillSelect
                label={t.as_tone}
                value={options.tone ?? ""}
                options={lists.tones}
                onChange={(tone) => onOptionsChange({ ...options, tone: tone || undefined })}
                placeholder={t.as_tone_auto}
              />
              <PillSelect
                label={t.as_length}
                value={options.length ?? ""}
                options={lists.lengths}
                onChange={(length) => onOptionsChange({ ...options, length: length || undefined })}
                placeholder={t.as_length_auto}
              />
            </div>
          ) : null}

          <div>
            <button
              type="button"
              data-job-picker-anchor
              onClick={() => onPickerOpenChange(!pickerOpen)}
              aria-expanded={pickerOpen}
              className={clsx(
                "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[12px] font-medium transition",
                jobId
                  ? "border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] text-[var(--primary-hover)]"
                  : "border-dashed border-[var(--border-strong)] text-[var(--text-secondary)] hover:border-[var(--primary)] hover:text-[var(--primary)]",
              )}
            >
              <Paperclip size={13} />
              {jobId ? t.as_attach_change : t.as_attach}
            </button>
            <JobPicker
              open={pickerOpen}
              onClose={() => onPickerOpenChange(false)}
              jobs={jobs}
              loading={jobsLoading}
              error={jobsError}
              onReload={onReloadJobs}
              selectedId={jobId}
              onSelect={(selected) => {
                onJobChange(selected);
                onPickerOpenChange(false);
                areaRef.current?.focus();
              }}
            />
          </div>

          <div className="ml-auto">
            {streaming ? (
              <button
                type="button"
                onClick={onStop}
                aria-label={t.as_stop}
                title={t.as_stop}
                className="inline-flex h-9 items-center gap-1.5 rounded-full border border-[var(--border-strong)] bg-[var(--surface)] px-3.5 text-[13px] font-semibold text-[var(--text-primary)] shadow-[var(--shadow-xs)] transition hover:bg-[var(--surface-hover)]"
              >
                <Square size={11} fill="currentColor" />
                {t.as_stop}
              </button>
            ) : (
              <button
                type="button"
                onClick={submit}
                disabled={!canSend}
                aria-label={t.as_send}
                title={`${t.as_send} (Enter)`}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--primary)] text-white shadow-[var(--shadow-primary)] transition hover:bg-[var(--primary-hover)] disabled:cursor-not-allowed disabled:bg-[var(--surface-sunken)] disabled:text-[var(--text-tertiary)] disabled:shadow-none"
              >
                <ArrowUp size={17} strokeWidth={2.4} />
              </button>
            )}
          </div>
        </div>
      </div>
      <p className="mt-2 text-center text-[11.5px] text-[var(--text-tertiary)]">{t.as_composer_hint}</p>
    </div>
  );
});
