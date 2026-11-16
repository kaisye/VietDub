import clsx from "clsx";
import { Check, ChevronDown, Cpu, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useT } from "../../i18n";
import { listAssistantModels, toAssistantError } from "../../lib/assistant-api";
import type { AssistantMode, ModelInfo, ProviderId } from "../../lib/assistant-api";

// --- Per-mode model choice --------------------------------------------------------------
// The saved provider model stays the default; a mode (e.g. "Viết content") can use another
// model of the same provider. Kept per provider so switching provider never sends a model
// id the other one does not know.

const storageKey = (provider: ProviderId, mode: AssistantMode) => `vd.assistant.model.${provider}.${mode}`;

export function readModeModel(provider: ProviderId | null | undefined, mode: AssistantMode): string {
  if (!provider) return "";
  try {
    return localStorage.getItem(storageKey(provider, mode)) ?? "";
  } catch {
    return "";
  }
}

export function writeModeModel(provider: ProviderId, mode: AssistantMode, model: string): void {
  try {
    if (model) localStorage.setItem(storageKey(provider, mode), model);
    else localStorage.removeItem(storageKey(provider, mode));
  } catch {
    // Storage unavailable: the choice lasts for this session only.
  }
}

const ACTIVE_PROVIDER_KEY = "vd.assistant.activeProvider";

/** The Assistant screen records its active provider so other screens can find the per-mode model. */
export function rememberActiveProvider(provider: ProviderId): void {
  try {
    localStorage.setItem(ACTIVE_PROVIDER_KEY, provider);
  } catch {
    // ignore
  }
}

/** Model chosen for "Viết content", for content generated outside the chat (e.g. a job's titles). */
export function contentModelFor(provider: ProviderId | null | undefined): string {
  let active = provider ?? null;
  if (!active) {
    try {
      active = (localStorage.getItem(ACTIVE_PROVIDER_KEY) as ProviderId | null) ?? null;
    } catch {
      active = null;
    }
  }
  return readModeModel(active, "write");
}

// The model list rarely changes; fetch once per provider per session.
const modelCache = new Map<ProviderId, Promise<ModelInfo[]>>();

function loadModels(provider: ProviderId): Promise<ModelInfo[]> {
  let pending = modelCache.get(provider);
  if (!pending) {
    pending = listAssistantModels(provider)
      .then((res) => res.models)
      .catch((error) => {
        modelCache.delete(provider);
        throw error;
      });
    modelCache.set(provider, pending);
  }
  return pending;
}

const MAX_SHOWN = 80;

export function ModelPicker({
  provider,
  defaultModel,
  value,
  onChange,
  disabled,
}: {
  provider: ProviderId | null;
  defaultModel: string;
  /** "" = use the provider's saved model. */
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
}) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [error, setError] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open || !provider) return;
    let alive = true;
    setError("");
    loadModels(provider)
      .then((list) => alive && setModels(list))
      .catch((err) => alive && setError(toAssistantError(err).message));
    requestAnimationFrame(() => inputRef.current?.focus());
    return () => {
      alive = false;
    };
  }, [open, provider]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const list = (models ?? []).filter((m) => {
      const hay = `${m.id} ${m.name}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
    return list.slice(0, MAX_SHOWN);
  }, [models, query]);

  const pick = (model: string) => {
    onChange(model === defaultModel ? "" : model);
    setOpen(false);
    setQuery("");
  };

  const custom = query.trim();
  const showCustom = !!custom && !shown.some((m) => m.id === custom);
  const current = value || defaultModel;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled || !provider}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={value ? t.as_model_custom_title : t.as_model_default_title}
        className={clsx(
          "inline-flex h-7 max-w-[220px] items-center gap-1.5 rounded-full border px-2.5 text-[12px] font-medium transition disabled:opacity-55",
          value
            ? "border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] text-[var(--primary-hover)]"
            : "border-[var(--border)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]",
        )}
      >
        <Cpu size={13} className="shrink-0" />
        <span className="truncate font-mono text-[11.5px]">{current || t.as_model}</span>
        <ChevronDown size={12} className="shrink-0" />
      </button>
      {open ? (
        <div className="absolute bottom-full left-0 z-30 mb-2 w-[340px] overflow-hidden rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-float)]">
          <div className="border-b border-[var(--border-subtle)] p-2">
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpen(false);
                if (e.key === "Enter" && custom) {
                  e.preventDefault();
                  pick(shown.find((m) => m.id === custom)?.id ?? (shown.length === 1 ? shown[0].id : custom));
                }
              }}
              placeholder={t.as_model_search}
              spellCheck={false}
              className="h-8 w-full rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 text-[12.5px] outline-none focus:border-[var(--primary)]"
            />
          </div>
          <div className="max-h-[280px] overflow-y-auto py-1">
            {value ? (
              <button
                type="button"
                onClick={() => pick("")}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]"
              >
                <RotateCcw size={12} />
                <span className="truncate">
                  {t.as_model_use_default} <span className="font-mono">{defaultModel}</span>
                </span>
              </button>
            ) : null}
            {showCustom ? (
              <button
                type="button"
                onClick={() => pick(custom)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] hover:bg-[var(--surface-hover)]"
              >
                <span className="text-[var(--text-secondary)]">{t.as_model_use_custom}</span>
                <span className="truncate font-mono">{custom}</span>
              </button>
            ) : null}
            {error ? <div className="px-3 py-2 text-[12px] text-[var(--danger)]">{error}</div> : null}
            {!models && !error ? <div className="px-3 py-2 text-[12px] text-[var(--text-tertiary)]">{t.as_model_loading}</div> : null}
            {shown.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => pick(m.id)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-[var(--surface-hover)]"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] text-[var(--text-primary)]">{m.name}</span>
                  {m.name !== m.id ? <span className="block truncate font-mono text-[11px] text-[var(--text-tertiary)]">{m.id}</span> : null}
                </span>
                {m.id === current ? <Check size={13} className="shrink-0 text-[var(--primary)]" /> : null}
              </button>
            ))}
            {models && !shown.length && !showCustom ? (
              <div className="px-3 py-2 text-[12px] text-[var(--text-tertiary)]">{t.as_model_none}</div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
