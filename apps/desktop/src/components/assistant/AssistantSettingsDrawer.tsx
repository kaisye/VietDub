import clsx from "clsx";
import { CheckCircle2, ChevronDown, Cpu, KeyRound, PlugZap, RefreshCw, Server, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useT } from "../../i18n";
import {
  getAssistantProviders,
  getAssistantSettings,
  listAssistantModels,
  testAssistantConnection,
  toAssistantError,
  updateAssistantSettings,
} from "../../lib/assistant-api";
import type {
  AssistantSettings,
  AssistantSettingsPatch,
  ConnectionTestResult,
  Effort,
  ProviderId,
  ProviderInfo,
  ToolsMode,
} from "../../lib/assistant-api";
import { Badge, Button, Drawer, Field, InlineNotice, Select, Skeleton, TextField } from "../ui";
import { describeError } from "./shared";

type KeyEdit = { mode: "keep" } | { mode: "replace"; value: string } | { mode: "clear" };

type Form = {
  provider: ProviderId;
  anthropic: { model: string; effort: Effort; key: KeyEdit };
  openai: { preset: string; base_url: string; model: string; tools_mode: ToolsMode; key: KeyEdit };
};

function formFrom(settings: AssistantSettings): Form {
  return {
    provider: settings.active_provider,
    anthropic: { model: settings.anthropic.model, effort: settings.anthropic.effort, key: { mode: "keep" } },
    openai: {
      preset: settings.openai_compatible.preset || "custom",
      base_url: settings.openai_compatible.base_url,
      model: settings.openai_compatible.model,
      tools_mode: settings.openai_compatible.tools_mode,
      key: { mode: "keep" },
    },
  };
}

/** API-key semantics from the contract: omit = keep, "" = clear, other = replace. */
function keyValue(edit: KeyEdit): string | undefined {
  if (edit.mode === "replace") return edit.value.trim() || undefined;
  if (edit.mode === "clear") return "";
  return undefined;
}

function patchFrom(form: Form): AssistantSettingsPatch {
  const patch: AssistantSettingsPatch = {
    active_provider: form.provider,
    anthropic: { model: form.anthropic.model.trim(), effort: form.anthropic.effort },
    openai_compatible: {
      preset: form.openai.preset,
      base_url: form.openai.base_url.trim(),
      model: form.openai.model.trim(),
      tools_mode: form.openai.tools_mode,
    },
  };
  const ak = keyValue(form.anthropic.key);
  if (ak !== undefined) patch.anthropic!.api_key = ak;
  const ok = keyValue(form.openai.key);
  if (ok !== undefined) patch.openai_compatible!.api_key = ok;
  return patch;
}

/** Text input + dropdown of known models (free text allowed). */
function ModelCombobox({
  value,
  onChange,
  models,
  loading,
  onRefresh,
  placeholder,
  refreshLabel,
  emptyLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  models: string[];
  loading: boolean;
  onRefresh: () => void;
  placeholder: string;
  refreshLabel: string;
  emptyLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);
  const q = value.trim().toLowerCase();
  const filtered = models.filter((m) => !q || m.toLowerCase().includes(q) || models.includes(value));
  return (
    <div ref={wrapRef} className="relative">
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <TextField
            value={value}
            onChange={(e) => {
              onChange(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setOpen(false);
              }
            }}
            placeholder={placeholder}
            className="pr-9 font-mono text-[13px]"
            spellCheck={false}
            autoComplete="off"
          />
          <button
            type="button"
            tabIndex={-1}
            aria-label="models"
            onClick={() => setOpen((v) => !v)}
            className="absolute right-1 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-[var(--text-tertiary)] hover:bg-[var(--surface-hover)]"
          >
            <ChevronDown size={15} />
          </button>
        </div>
        <Button variant="secondary" onClick={onRefresh} loading={loading} leadingIcon={<RefreshCw size={14} />} title={refreshLabel}>
          {refreshLabel}
        </Button>
      </div>
      {open ? (
        <div className="absolute left-0 right-0 top-[calc(100%+4px)] z-10 max-h-56 overflow-y-auto rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] p-1 shadow-[var(--shadow-float)]">
          {filtered.length ? (
            filtered.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => {
                  onChange(m);
                  setOpen(false);
                }}
                className={clsx(
                  "flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left font-mono text-[12.5px] hover:bg-[var(--surface-hover)]",
                  m === value && "bg-[var(--primary-soft)] text-[var(--primary-hover)]",
                )}
              >
                {m}
                {m === value ? <CheckCircle2 size={13} /> : null}
              </button>
            ))
          ) : (
            <div className="px-2.5 py-2 text-[12.5px] text-[var(--text-tertiary)]">{emptyLabel}</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function KeyInput({
  edit,
  onChange,
  masked,
  isSet,
  placeholder,
  clearLabel,
  undoLabel,
  willClearLabel,
}: {
  edit: KeyEdit;
  onChange: (edit: KeyEdit) => void;
  masked: string;
  isSet: boolean;
  placeholder: string;
  clearLabel: string;
  undoLabel: string;
  willClearLabel: string;
}) {
  if (edit.mode === "clear") {
    return (
      <div className="flex items-center justify-between gap-3 rounded-[var(--radius-control)] border border-dashed border-[var(--danger-border)] bg-[var(--danger-soft)] px-3 py-2 text-[13px] text-[var(--danger)]">
        {willClearLabel}
        <Button size="xs" variant="ghost" onClick={() => onChange({ mode: "keep" })}>
          {undoLabel}
        </Button>
      </div>
    );
  }
  return (
    <div className="flex gap-2">
      <div className="relative min-w-0 flex-1">
        <KeyRound size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" />
        <TextField
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={edit.mode === "replace" ? edit.value : ""}
          // Never show the key: the masked value is only a placeholder.
          placeholder={isSet ? masked : placeholder}
          onChange={(e) => onChange(e.target.value ? { mode: "replace", value: e.target.value } : { mode: "keep" })}
          className="pl-8 font-mono text-[13px]"
        />
      </div>
      {isSet ? (
        <Button variant="ghost" leadingIcon={<Trash2 size={14} />} onClick={() => onChange({ mode: "clear" })}>
          {clearLabel}
        </Button>
      ) : null}
    </div>
  );
}

/**
 * Assistant provider settings: provider (Anthropic / OpenAI-compatible + presets),
 * API key (masked), base URL, model (list + free text), connection test, save.
 */
export function AssistantSettingsDrawer({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: (settings: AssistantSettings) => void;
}) {
  const { t } = useT();
  const [settings, setSettings] = useState<AssistantSettings | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [models, setModels] = useState<Record<ProviderId, string[]>>({ anthropic: [], openai_compatible: [] });
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoadError(null);
    setTestResult(null);
    setSaveError(null);
    setSavedFlash(false);
    setModelsError(null);
    Promise.all([getAssistantSettings(), getAssistantProviders()])
      .then(([s, p]) => {
        if (!alive) return;
        setSettings(s);
        setProviders(p.providers);
        setForm(formFrom(s));
      })
      .catch((error) => alive && setLoadError(toAssistantError(error).message));
    return () => {
      alive = false;
    };
  }, [open]);

  const anthropicInfo = providers.find((p) => p.id === "anthropic");
  const openaiInfo = providers.find((p) => p.id === "openai_compatible");
  const presets = openaiInfo?.presets ?? [];
  const currentPreset = presets.find((p) => p.id === form?.openai.preset);

  const modelOptions = useMemo(() => {
    if (!form) return [];
    const fetched = models[form.provider];
    const suggested = form.provider === "anthropic" ? anthropicInfo?.suggested_models ?? [] : [];
    return Array.from(new Set([...fetched, ...suggested]));
  }, [form, models, anthropicInfo]);

  if (!open) return null;

  const connectionDirty =
    !!form &&
    !!settings &&
    (form.provider === "anthropic"
      ? form.anthropic.key.mode !== "keep"
      : form.openai.key.mode !== "keep" ||
        form.openai.base_url.trim() !== settings.openai_compatible.base_url ||
        form.openai.preset !== settings.openai_compatible.preset);

  const update = (fn: (f: Form) => Form) => {
    setForm((f) => (f ? fn(f) : f));
    setTestResult(null);
    setSavedFlash(false);
  };

  const refreshModels = async () => {
    if (!form) return;
    setModelsLoading(true);
    setModelsError(null);
    try {
      // GET /models reads the *saved* connection settings (see connectionDirty hint below).
      const provider = form.provider;
      const res = await listAssistantModels(provider);
      setModels((m) => ({ ...m, [provider]: res.models.map((x) => x.id) }));
      if (!res.models.length) setModelsError(t.as_set_models_empty);
    } catch (error) {
      const e = toAssistantError(error);
      setModelsError(describeError(t, e.code, e.message).title + (e.message ? ` — ${e.message}` : ""));
    } finally {
      setModelsLoading(false);
    }
  };

  const test = async () => {
    if (!form) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testAssistantConnection({ provider: form.provider, ...patchFrom(form) });
      setTestResult(res);
      if (res.models?.length) setModels((m) => ({ ...m, [form.provider]: res.models! }));
    } catch (error) {
      setTestResult({ ok: false, message: toAssistantError(error).message, error_code: toAssistantError(error).code });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await updateAssistantSettings(patchFrom(form));
      setSettings(saved);
      setForm(formFrom(saved));
      setSavedFlash(true);
      onSaved?.(saved);
    } catch (error) {
      setSaveError(toAssistantError(error).message);
    } finally {
      setSaving(false);
    }
  };

  const providerCards: Array<{ id: ProviderId; title: string; desc: string; icon: typeof Sparkles }> = [
    { id: "anthropic", title: "Anthropic Claude", desc: t.as_set_anthropic_desc, icon: Sparkles },
    { id: "openai_compatible", title: t.as_set_openai_title, desc: t.as_set_openai_desc, icon: Server },
  ];

  const configuredFor = (id: ProviderId) =>
    settings ? (id === "anthropic" ? settings.anthropic.configured : settings.openai_compatible.configured) : false;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={500}
      title={t.as_settings_title}
      description={t.as_settings_desc}
      footer={
        <>
          {savedFlash ? (
            <span className="mr-auto flex items-center gap-1.5 text-[13px] font-medium text-[var(--success)]">
              <CheckCircle2 size={15} /> {t.as_set_saved}
            </span>
          ) : null}
          <Button variant="ghost" onClick={onClose}>
            {t.as_close}
          </Button>
          <Button variant="secondary" onClick={test} loading={testing} disabled={!form} leadingIcon={<PlugZap size={15} />}>
            {t.as_set_test}
          </Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!form}>
            {t.as_set_save}
          </Button>
        </>
      }
    >
      {loadError ? (
        <InlineNotice tone="danger" title={t.as_set_load_error}>
          {loadError}
        </InlineNotice>
      ) : !form || !settings ? (
        <div className="grid gap-4">
          <Skeleton className="h-20 w-full" rounded="lg" />
          <Skeleton className="h-20 w-full" rounded="lg" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : (
        <div className="grid gap-5">
          <div className="grid gap-2" role="radiogroup" aria-label={t.as_set_provider}>
            <span className="text-[13px] font-semibold">{t.as_set_provider}</span>
            {providerCards.map((card) => {
              const active = form.provider === card.id;
              const Icon = card.icon;
              return (
                <button
                  key={card.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => update((f) => ({ ...f, provider: card.id }))}
                  className={clsx(
                    "flex items-start gap-3 rounded-[var(--radius-panel-sm)] border p-3 text-left transition",
                    active
                      ? "border-[var(--primary)] bg-[var(--primary-soft)] shadow-[0_0_0_3px_var(--primary-ring)]"
                      : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--border-strong)]",
                  )}
                >
                  <span
                    className={clsx(
                      "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
                      active ? "bg-[var(--primary)] text-white" : "bg-[var(--surface-sunken)] text-[var(--text-secondary)]",
                    )}
                  >
                    <Icon size={16} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <strong className="text-[13.5px] font-semibold">{card.title}</strong>
                      {configuredFor(card.id) ? (
                        <Badge tone="success" size="sm" dot>
                          {t.as_set_ready}
                        </Badge>
                      ) : (
                        <Badge tone="neutral" size="sm">
                          {t.as_set_not_ready}
                        </Badge>
                      )}
                    </span>
                    <span className="mt-0.5 block text-xs leading-5 text-[var(--text-secondary)]">{card.desc}</span>
                  </span>
                </button>
              );
            })}
          </div>

          {form.provider === "anthropic" ? (
            <>
              <Field label={t.as_set_api_key} help={t.as_set_anthropic_key_help} required={!settings.anthropic.api_key_set}>
                <KeyInput
                  edit={form.anthropic.key}
                  onChange={(key) => update((f) => ({ ...f, anthropic: { ...f.anthropic, key } }))}
                  masked={settings.anthropic.api_key_masked}
                  isSet={settings.anthropic.api_key_set}
                  placeholder="sk-ant-…"
                  clearLabel={t.as_set_key_clear}
                  undoLabel={t.as_set_key_undo}
                  willClearLabel={t.as_set_key_will_clear}
                />
              </Field>
              <Field label={t.as_set_model} help={t.as_set_model_help}>
                <ModelCombobox
                  value={form.anthropic.model}
                  onChange={(model) => update((f) => ({ ...f, anthropic: { ...f.anthropic, model } }))}
                  models={modelOptions}
                  loading={modelsLoading}
                  onRefresh={refreshModels}
                  placeholder={anthropicInfo?.default_model || "claude-sonnet-5-5"}
                  refreshLabel={t.as_set_models_load}
                  emptyLabel={t.as_set_models_none}
                />
              </Field>
              <Field label={t.as_set_effort} help={t.as_set_effort_help}>
                <Select
                  value={form.anthropic.effort}
                  onChange={(e) => update((f) => ({ ...f, anthropic: { ...f.anthropic, effort: e.target.value as Effort } }))}
                >
                  <option value="auto">{t.as_set_effort_auto}</option>
                  <option value="low">{t.as_set_effort_low}</option>
                  <option value="medium">{t.as_set_effort_medium}</option>
                  <option value="high">{t.as_set_effort_high}</option>
                </Select>
              </Field>
            </>
          ) : (
            <>
              <div className="grid gap-1.5">
                <span className="text-[13px] font-semibold">{t.as_set_preset}</span>
                <div className="grid grid-cols-4 gap-1 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-sunken)] p-1">
                  {presets.map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      aria-pressed={form.openai.preset === preset.id}
                      onClick={() =>
                        update((f) => ({
                          ...f,
                          openai: {
                            ...f.openai,
                            preset: preset.id,
                            base_url: preset.id === "custom" ? f.openai.base_url : preset.base_url,
                            model: preset.default_model || (f.openai.preset === preset.id ? f.openai.model : ""),
                          },
                        }))
                      }
                      className={clsx(
                        "min-h-8 rounded-md px-2 text-[13px] font-semibold transition",
                        form.openai.preset === preset.id
                          ? "bg-[var(--surface)] text-[var(--primary-hover)] shadow-[var(--shadow-sm)]"
                          : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
                      )}
                    >
                      {preset.id === "custom" ? t.as_set_preset_custom : preset.label}
                    </button>
                  ))}
                </div>
                {form.openai.preset === "ollama" || form.openai.preset === "lmstudio" || form.openai.preset === "openrouter" ? (
                  <span className="text-xs leading-5 text-[var(--text-secondary)]">
                    {form.openai.preset === "ollama"
                      ? t.as_set_ollama_hint
                      : form.openai.preset === "lmstudio"
                        ? t.as_set_lmstudio_hint
                        : t.as_set_openrouter_hint}
                  </span>
                ) : null}
              </div>
              <Field label={t.as_set_base_url} help={t.as_set_base_url_help}>
                <TextField
                  value={form.openai.base_url}
                  onChange={(e) => update((f) => ({ ...f, openai: { ...f.openai, base_url: e.target.value } }))}
                  placeholder={currentPreset?.base_url || "http://localhost:11434/v1"}
                  className="font-mono text-[13px]"
                  spellCheck={false}
                />
              </Field>
              <Field
                label={t.as_set_api_key}
                help={currentPreset?.requires_key ? t.as_set_openai_key_required : t.as_set_openai_key_optional}
                required={!!currentPreset?.requires_key && !settings.openai_compatible.api_key_set}
              >
                <KeyInput
                  edit={form.openai.key}
                  onChange={(key) => update((f) => ({ ...f, openai: { ...f.openai, key } }))}
                  masked={settings.openai_compatible.api_key_masked}
                  isSet={settings.openai_compatible.api_key_set}
                  placeholder={form.openai.preset === "openrouter" ? "sk-or-…" : currentPreset?.requires_key ? "sk-…" : t.as_set_key_optional_ph}
                  clearLabel={t.as_set_key_clear}
                  undoLabel={t.as_set_key_undo}
                  willClearLabel={t.as_set_key_will_clear}
                />
              </Field>
              <Field label={t.as_set_model} help={t.as_set_model_help_openai}>
                <ModelCombobox
                  value={form.openai.model}
                  onChange={(model) => update((f) => ({ ...f, openai: { ...f.openai, model } }))}
                  models={modelOptions}
                  loading={modelsLoading}
                  onRefresh={refreshModels}
                  placeholder={form.openai.preset === "ollama" ? "qwen3:8b" : form.openai.preset === "openrouter" ? "qwen/qwen3.7-flash" : "gpt-4o-mini"}
                  refreshLabel={t.as_set_models_load}
                  emptyLabel={t.as_set_models_none}
                />
              </Field>
              <Field label={t.as_set_tools} help={t.as_set_tools_help}>
                <Select
                  value={form.openai.tools_mode}
                  onChange={(e) => update((f) => ({ ...f, openai: { ...f.openai, tools_mode: e.target.value as ToolsMode } }))}
                >
                  <option value="auto">{t.as_set_tools_auto}</option>
                  <option value="on">{t.as_set_tools_on}</option>
                  <option value="off">{t.as_set_tools_off}</option>
                </Select>
              </Field>
            </>
          )}

          {connectionDirty ? <p className="-mt-3 text-xs text-[var(--text-tertiary)]">{t.as_set_models_saved_hint}</p> : null}

          {modelsError ? (
            <InlineNotice tone="warning" title={t.as_set_models_error}>
              {modelsError}
            </InlineNotice>
          ) : null}

          {testResult ? (
            <InlineNotice
              tone={testResult.ok ? "success" : testResult.error_code === "model_missing" ? "warning" : "danger"}
              title={
                testResult.ok
                  ? t.as_set_test_ok + (testResult.latency_ms != null ? ` · ${testResult.latency_ms} ms` : "")
                  : describeError(t, testResult.error_code || "", "").title
              }
            >
              {testResult.message}
              {testResult.error_code === "model_missing" && testResult.models?.length ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {testResult.models.slice(0, 12).map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() =>
                        update((f) =>
                          f.provider === "anthropic"
                            ? { ...f, anthropic: { ...f.anthropic, model: m } }
                            : { ...f, openai: { ...f.openai, model: m } },
                        )
                      }
                      className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 font-mono text-[11.5px] text-[var(--text-primary)] hover:border-[var(--primary)]"
                    >
                      {m}
                    </button>
                  ))}
                </div>
              ) : null}
            </InlineNotice>
          ) : null}

          {saveError ? (
            <InlineNotice tone="danger" title={t.as_set_save_error}>
              {saveError}
            </InlineNotice>
          ) : null}

          <p className="flex items-start gap-2 text-xs leading-5 text-[var(--text-tertiary)]">
            <Cpu size={14} className="mt-0.5 shrink-0" />
            {t.as_set_privacy}
          </p>
        </div>
      )}
    </Drawer>
  );
}
