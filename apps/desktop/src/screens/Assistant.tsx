import clsx from "clsx";
import { ArrowDown, Film, Pencil, Settings2, Sparkles, Target } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listJobs } from "../api";
import { AssistantSettingsDrawer } from "../components/assistant/AssistantSettingsDrawer";
import { ChatMessage, buildUiMessages } from "../components/assistant/ChatMessage";
import type { ToolActivity, UiMessage } from "../components/assistant/ChatMessage";
import { Composer } from "../components/assistant/Composer";
import type { ComposerHandle } from "../components/assistant/Composer";
import { ConversationList, MODE_ICON } from "../components/assistant/ConversationList";
import { ModelPicker, readModeModel, rememberActiveProvider, writeModeModel } from "../components/assistant/ModelPicker";
import { notConfiguredReason, useQuickActions } from "../components/assistant/shared";
import type { View } from "../components/layout/nav";
import { Badge, Button, InlineNotice, Modal, Skeleton } from "../components/ui";
import { useT } from "../i18n";
import {
  deleteConversation,
  getAssistantSettings,
  getConversation,
  listConversations,
  streamChat,
  toAssistantError,
  updateConversation,
} from "../lib/assistant-api";
import type {
  AssistantMode,
  AssistantSettings,
  ChatOptions,
  ChatStreamEvent,
  Conversation,
  QuickAction,
} from "../lib/assistant-api";
import type { Job } from "../types";

let uid = 0;
const localKey = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(uid++).toString(36)}`;

const MODE_ACCENT: Record<AssistantMode, string> = {
  chat: "bg-[var(--primary-soft)] text-[var(--primary)]",
  write: "bg-[var(--accent-violet-soft)] text-[var(--accent-violet)]",
  strategy: "bg-[var(--accent-cyan-soft)] text-[var(--accent-cyan)]",
};

export default function AssistantScreen(_props: { onNavigate?: (view: View) => void }) {
  const { t } = useT();
  const quick = useQuickActions();

  // --- Settings ---------------------------------------------------------------------
  const [settings, setSettings] = useState<AssistantSettings | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const loadSettings = useCallback(() => {
    setSettingsError(null);
    getAssistantSettings()
      .then(setSettings)
      .catch((error) => setSettingsError(toAssistantError(error).message || t.as_err_backend_offline));
  }, [t]);
  useEffect(loadSettings, [loadSettings]);
  const activeProvider = settings ? (settings.active_provider === "anthropic" ? settings.anthropic : settings.openai_compatible) : null;
  const configured = !!activeProvider?.configured;
  const providerId = settings?.active_provider ?? null;
  useEffect(() => {
    if (providerId) rememberActiveProvider(providerId);
  }, [providerId]);
  const providerLabel =
    settings?.active_provider === "anthropic"
      ? "Claude"
      : settings
        ? ({ openai: "OpenAI", ollama: "Ollama", lmstudio: "LM Studio" } as Record<string, string>)[settings.openai_compatible.preset] ??
          t.as_set_preset_custom
        : "";

  // --- Conversations ---------------------------------------------------------------------
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const refreshList = useCallback(async () => {
    try {
      const res = await listConversations({ limit: 200 });
      setConversations(res.items);
      setListError(null);
    } catch (error) {
      setListError(toAssistantError(error).message);
    } finally {
      setListLoading(false);
    }
  }, []);
  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadError, setThreadError] = useState<string | null>(null);
  const activeConv = conversations.find((c) => c.id === activeId) ?? null;

  // --- Composer state ------------------------------------------------------------------------
  const [mode, setMode] = useState<AssistantMode>("chat");
  const [modelTick, setModelTick] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const modeModel = useMemo(() => readModeModel(providerId, mode), [providerId, mode, modelTick]);
  const [options, setOptions] = useState<ChatOptions>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<QuickAction | null>(null);
  const composerRef = useRef<ComposerHandle>(null);

  // Dubbing jobs (for the "attach project" picker).
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const reloadJobs = useCallback(() => {
    setJobsLoading(true);
    setJobsError(null);
    listJobs()
      .then((list) =>
        setJobs([...list].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))),
      )
      .catch((error) => setJobsError(error instanceof Error ? error.message : String(error)))
      .finally(() => setJobsLoading(false));
  }, []);
  useEffect(reloadJobs, [reloadJobs]);
  const job = jobs.find((j) => j.id === jobId) ?? null;

  // --- Streaming ---------------------------------------------------------------------------
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const streamConvRef = useRef<string | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const patchLast = useCallback((fn: (m: UiMessage) => UiMessage) => {
    setMessages((list) => {
      const idx = list.length - 1;
      if (idx < 0 || list[idx].role !== "assistant") return list;
      const next = list.slice();
      next[idx] = fn(list[idx]);
      return next;
    });
  }, []);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  // --- Thread loading --------------------------------------------------------------------
  const openConversation = useCallback(
    async (id: string) => {
      if (streaming) stop();
      setActiveId(id);
      setThreadError(null);
      setThreadLoading(true);
      setMessages([]);
      setPickerOpen(false);
      try {
        const detail = await getConversation(id, true);
        setMessages(buildUiMessages(detail.messages));
        setMode(detail.mode || "chat");
        setOptions(detail.options ?? {});
        setJobId(detail.job_id);
      } catch (error) {
        const e = toAssistantError(error);
        setThreadError(e.code === "not_found" ? t.as_err_conv_missing : e.message);
        if (e.code === "not_found") void refreshList();
      } finally {
        setThreadLoading(false);
        requestAnimationFrame(() => composerRef.current?.focus());
      }
    },
    [refreshList, stop, streaming, t],
  );

  const newChat = useCallback(() => {
    if (streaming) stop();
    setActiveId(null);
    setMessages([]);
    setThreadError(null);
    setMode("chat");
    setOptions({});
    setJobId(null);
    requestAnimationFrame(() => composerRef.current?.focus());
  }, [stop, streaming]);

  // --- Sending ---------------------------------------------------------------------------
  const send = useCallback(
    async (
      text: string,
      override?: { mode?: AssistantMode; options?: ChatOptions; jobId?: string | null; regenerate?: boolean },
    ) => {
      if (!text.trim() || streaming) return;
      const sendMode = override?.mode ?? mode;
      const sendOptions = override?.options ?? options;
      const sendJob = override?.jobId !== undefined ? override.jobId : jobId;
      const assistantKey = localKey("a");
      setMessages((list) => [
        ...(override?.regenerate ? list : [...list, { key: localKey("u"), role: "user" as const, content: text, mode: sendMode, tools: [], notices: [] }]),
        { key: assistantKey, role: "assistant", content: "", mode: sendMode, tools: [], notices: [], streaming: true },
      ]);
      setThreadError(null);
      setStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;
      streamConvRef.current = activeId;
      let gotTerminal = false;

      const onEvent = (ev: ChatStreamEvent) => {
        switch (ev.event) {
          case "conversation":
            streamConvRef.current = ev.data.conversation_id;
            if (ev.data.created) {
              setActiveId(ev.data.conversation_id);
              const now = new Date().toISOString().replace("Z", "");
              setConversations((list) => [
                {
                  id: ev.data.conversation_id,
                  title: ev.data.title,
                  mode: sendMode,
                  job_id: sendJob,
                  provider: null,
                  model: null,
                  options: sendOptions,
                  created_at: now,
                  updated_at: now,
                  message_count: 1,
                },
                ...list.filter((c) => c.id !== ev.data.conversation_id),
              ]);
            }
            break;
          case "text":
            patchLast((m) => ({ ...m, content: m.content + ev.data.delta }));
            break;
          case "tool_call":
            patchLast((m) => ({
              ...m,
              tools: [...m.tools, { id: ev.data.id, name: ev.data.name, input: ev.data.input ?? {}, status: "running" } as ToolActivity],
            }));
            break;
          case "tool_result":
            patchLast((m) => ({
              ...m,
              tools: m.tools.map((x) =>
                x.id === ev.data.id ? { ...x, status: ev.data.ok ? "ok" : "error", summary: ev.data.summary } : x,
              ),
            }));
            break;
          case "notice":
            patchLast((m) => ({ ...m, notices: [...m.notices, ev.data] }));
            break;
          case "error":
            gotTerminal = true;
            patchLast((m) => ({
              ...m,
              streaming: false,
              error: { code: ev.data.code, message: ev.data.message },
              tools: m.tools.map((x) => (x.status === "running" ? { ...x, status: "error" } : x)),
            }));
            break;
          case "done":
            gotTerminal = true;
            patchLast((m) => ({ ...m, streaming: false, model: ev.data.model, key: m.key }));
            break;
        }
      };

      try {
        await streamChat(
          {
            message: text,
            conversation_id: activeId,
            mode: sendMode,
            options: sendOptions,
            job_id: sendJob,
            model: readModeModel(providerId, sendMode) || null,
          },
          onEvent,
          controller.signal,
        );
        if (!gotTerminal) {
          patchLast((m) => ({ ...m, streaming: false, error: m.error ?? { code: "provider_error", message: t.as_err_stream_cut } }));
        }
      } catch (error) {
        const e = toAssistantError(error);
        patchLast((m) => ({
          ...m,
          streaming: false,
          error: { code: e.code, message: e.message },
          tools: m.tools.map((x) => (x.status === "running" ? { ...x, status: "error" } : x)),
        }));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setStreaming(false);
        void refreshList();
      }
    },
    [activeId, jobId, mode, options, patchLast, providerId, refreshList, streaming, t],
  );

  const lastUserText = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === "user") return messages[i].content;
    return "";
  }, [messages]);

  /** Regenerate = resend the last question; the previous answer is replaced in the view. */
  const regenerate = useCallback(() => {
    if (!lastUserText || streaming) return;
    setMessages((list) => (list.length && list[list.length - 1].role === "assistant" ? list.slice(0, -1) : list));
    void send(lastUserText, { regenerate: true });
  }, [lastUserText, send, streaming]);

  const runQuickAction = useCallback(
    (action: QuickAction, attachJob?: string | null) => {
      const nextJob = attachJob !== undefined ? attachJob : jobId;
      setMode(action.mode);
      setOptions(action.options ?? {});
      if (action.needs_job && !nextJob) {
        setPendingAction(action);
        setPickerOpen(true);
        return;
      }
      void send(action.prompt, { mode: action.mode, options: action.options ?? {}, jobId: nextJob });
    },
    [jobId, send],
  );

  const onJobChange = useCallback(
    (selected: Job | null) => {
      setJobId(selected?.id ?? null);
      if (pendingAction && selected) {
        const action = pendingAction;
        setPendingAction(null);
        runQuickAction(action, selected.id);
      }
    },
    [pendingAction, runQuickAction],
  );

  // --- Rename / delete ----------------------------------------------------------------------
  const rename = useCallback(async (id: string, title: string) => {
    setConversations((list) => list.map((c) => (c.id === id ? { ...c, title } : c)));
    try {
      const updated = await updateConversation(id, { title });
      setConversations((list) => list.map((c) => (c.id === id ? { ...c, ...updated } : c)));
    } catch {
      void refreshList();
    }
  }, [refreshList]);

  const [toDelete, setToDelete] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState(false);
  const confirmDelete = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await deleteConversation(toDelete.id);
    } catch (error) {
      if (toAssistantError(error).code !== "not_found") {
        setDeleting(false);
        setListError(toAssistantError(error).message);
        setToDelete(null);
        return;
      }
    }
    setConversations((list) => list.filter((c) => c.id !== toDelete.id));
    if (toDelete.id === activeId) newChat();
    setDeleting(false);
    setToDelete(null);
  };

  // --- Auto-scroll -------------------------------------------------------------------------
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickRef.current = near;
    setShowJump(!near);
  };
  // The empty state (welcome + quick actions) reads top-down: never pin it to the bottom,
  // or the hero gets cut off on short windows.
  const hasMessagesRef = useRef(false);
  hasMessagesRef.current = messages.length > 0;
  const jumpToBottom = (smooth = true) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: hasMessagesRef.current ? el.scrollHeight : 0, behavior: smooth ? "smooth" : "auto" });
  };
  useEffect(() => {
    if (stickRef.current) jumpToBottom(false);
  }, [messages]);
  useEffect(() => {
    stickRef.current = true;
    setShowJump(false);
    requestAnimationFrame(() => jumpToBottom(false));
  }, [activeId]);

  // --- Render ---------------------------------------------------------------------------------
  const lists = {
    platforms: quick?.platforms ?? [],
    tones: quick?.tones ?? [],
    lengths: quick?.lengths ?? [],
  };
  const hasMessages = messages.length > 0;
  const notConfiguredBanner =
    settings && !configured ? (
      <InlineNotice
        tone="warning"
        className="mb-3"
        title={t.as_not_configured_title}
        action={
          <Button size="sm" variant="primary" leadingIcon={<Settings2 size={14} />} onClick={() => setSettingsOpen(true)}>
            {t.as_open_settings}
          </Button>
        }
      >
        {(settings && activeProvider && notConfiguredReason(t, settings.active_provider, activeProvider)) ||
          t.as_not_configured_body}
      </InlineNotice>
    ) : settingsError ? (
      <InlineNotice
        tone="danger"
        className="mb-3"
        title={t.as_err_backend_offline}
        action={
          <Button size="sm" onClick={loadSettings}>
            {t.retry}
          </Button>
        }
      >
        {settingsError}
      </InlineNotice>
    ) : null;

  const pickerBanner = pendingAction ? (
    <div className="mb-2 flex items-center justify-between gap-3 rounded-[var(--radius-control)] bg-[var(--primary-soft)] px-3 py-2 text-[12.5px] text-[var(--primary-ink)]">
      <span className="flex items-center gap-2">
        <Film size={14} /> {t.as_pick_job_for.replace("{action}", pendingAction.label)}
      </span>
      <button type="button" className="font-semibold hover:underline" onClick={() => setPendingAction(null)}>
        {t.cancel}
      </button>
    </div>
  ) : null;

  const headerMode = activeConv?.mode ?? mode;
  const HeaderIcon = MODE_ICON[headerMode] ?? Sparkles;

  return (
    <div className="flex h-full min-h-0 bg-[var(--surface)]">
      <ConversationList
        items={conversations}
        loading={listLoading}
        activeId={activeId}
        onSelect={(id) => void openConversation(id)}
        onNew={newChat}
        onRename={rename}
        onDelete={setToDelete}
      />

      <section className="relative flex min-w-0 flex-1 flex-col bg-[var(--canvas)]">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-[var(--border)] bg-[var(--surface)] px-5">
          <span className={clsx("flex h-7 w-7 items-center justify-center rounded-lg", MODE_ACCENT[headerMode])}>
            <HeaderIcon size={15} />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[14.5px] font-semibold text-[var(--text-primary)]">
              {activeConv?.title || (activeId ? t.as_untitled : t.as_new_chat)}
            </h1>
          </div>
          {listError ? (
            <Badge tone="danger" size="sm" title={listError}>
              {t.as_list_error}
            </Badge>
          ) : null}
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="inline-flex h-8 items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--surface)] pl-2.5 pr-3 text-[12.5px] font-medium text-[var(--text-secondary)] shadow-[var(--shadow-xs)] transition hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
            title={t.as_settings_title}
          >
            <span className={clsx("h-2 w-2 rounded-full", !settings ? "bg-[var(--border-strong)]" : configured ? "bg-[var(--success)]" : "bg-[var(--warning)]")} />
            {settings ? (
              <>
                <span className="text-[var(--text-primary)]">{providerLabel}</span>
                {activeProvider && "model" in activeProvider && activeProvider.model ? (
                  <span className="max-w-[160px] truncate font-mono text-[11.5px] text-[var(--text-tertiary)]">{activeProvider.model}</span>
                ) : null}
              </>
            ) : (
              t.as_settings_short
            )}
            <Settings2 size={14} />
          </button>
        </header>

        <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
          {threadLoading ? (
            <div className="mx-auto grid max-w-[820px] gap-6 px-6 py-8">
              <Skeleton className="ml-auto h-10 w-1/2" rounded="lg" />
              <div className="grid gap-2">
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-5/6" />
                <Skeleton className="h-3 w-2/3" />
              </div>
              <Skeleton className="ml-auto h-10 w-1/3" rounded="lg" />
            </div>
          ) : threadError ? (
            <div className="mx-auto max-w-[820px] px-6 py-8">
              <InlineNotice
                tone="danger"
                title={t.as_err_load_conv}
                action={
                  activeId ? (
                    <Button size="sm" onClick={() => void openConversation(activeId)}>
                      {t.retry}
                    </Button>
                  ) : undefined
                }
              >
                {threadError}
              </InlineNotice>
            </div>
          ) : hasMessages ? (
            <div className="mx-auto grid max-w-[820px] gap-7 px-6 pb-6 pt-8">
              {messages.map((message, index) => (
                <ChatMessage
                  key={message.key}
                  message={message}
                  isLast={index === messages.length - 1}
                  canRegenerate={!streaming && !!lastUserText && configured}
                  onRegenerate={regenerate}
                  onRetry={regenerate}
                  onOpenSettings={() => setSettingsOpen(true)}
                />
              ))}
            </div>
          ) : (
            <div className="mx-auto flex min-h-full max-w-[860px] flex-col justify-center px-6 py-10">
              <div className="mb-7 text-center">
                <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-[var(--primary)] to-[var(--accent-violet)] text-white shadow-[var(--shadow-primary)]">
                  <Sparkles size={24} />
                </div>
                <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">{t.as_empty_title}</h2>
                <p className="mx-auto mt-1.5 max-w-lg text-[14px] leading-6 text-[var(--text-secondary)]">{t.as_empty_desc}</p>
              </div>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(230px,1fr))] gap-3">
                {quick
                  ? quick.actions.map((action) => {
                      const Icon = action.mode === "write" ? Pencil : action.mode === "strategy" ? Target : Sparkles;
                      return (
                        <button
                          key={action.id}
                          type="button"
                          disabled={!configured || streaming}
                          onClick={() => runQuickAction(action)}
                          className="group flex flex-col gap-2 rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] p-3.5 text-left shadow-[var(--shadow-xs)] transition hover:-translate-y-px hover:border-[var(--primary-soft-strong)] hover:shadow-[var(--shadow-float)] disabled:pointer-events-none disabled:opacity-55"
                        >
                          <span className="flex items-center gap-2">
                            <span className={clsx("flex h-7 w-7 items-center justify-center rounded-lg", MODE_ACCENT[action.mode])}>
                              <Icon size={14} />
                            </span>
                            <strong className="min-w-0 flex-1 truncate text-[13.5px] font-semibold text-[var(--text-primary)]">
                              {action.label}
                            </strong>
                          </span>
                          <span className="line-clamp-2 text-[12.5px] leading-[1.45] text-[var(--text-secondary)]">{action.prompt}</span>
                          {action.needs_job ? (
                            <span className="mt-auto inline-flex items-center gap-1 text-[11.5px] font-medium text-[var(--text-tertiary)]">
                              <Film size={12} /> {t.as_needs_job}
                            </span>
                          ) : null}
                        </button>
                      );
                    })
                  : [0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-[104px]" rounded="lg" />)}
              </div>
            </div>
          )}
        </div>

        {showJump && hasMessages ? (
          <button
            type="button"
            onClick={() => jumpToBottom()}
            aria-label={t.as_jump_bottom}
            className="absolute bottom-[150px] left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] shadow-[var(--shadow-float)] hover:text-[var(--text-primary)]"
          >
            <ArrowDown size={15} />
          </button>
        ) : null}

        <div className="shrink-0 bg-gradient-to-t from-[var(--canvas)] via-[var(--canvas)] to-transparent px-6 pb-3 pt-2">
          <Composer
            ref={composerRef}
            mode={mode}
            onModeChange={setMode}
            options={options}
            onOptionsChange={setOptions}
            lists={lists}
            job={job}
            jobId={jobId}
            onJobChange={onJobChange}
            jobs={jobs}
            jobsLoading={jobsLoading}
            jobsError={jobsError}
            onReloadJobs={reloadJobs}
            pickerOpen={pickerOpen}
            onPickerOpenChange={(open) => {
              setPickerOpen(open);
              if (!open) setPendingAction(null);
            }}
            streaming={streaming}
            disabled={!configured}
            onSend={(text) => void send(text)}
            onStop={stop}
            modelSlot={
              <ModelPicker
                provider={providerId}
                defaultModel={
                  (mode === "write" && settings?.active_provider === "openai_compatible" && settings.openai_compatible.preset === "openrouter"
                    ? settings.openai_compatible.content_model
                    : "") || (activeProvider && "model" in activeProvider ? activeProvider.model : "")
                }
                value={modeModel}
                disabled={!configured || streaming}
                onChange={(model) => {
                  if (!providerId) return;
                  writeModeModel(providerId, mode, model);
                  setModelTick((n) => n + 1);
                }}
              />
            }
            banner={
              <>
                {notConfiguredBanner}
                {pickerBanner}
              </>
            }
          />
        </div>
      </section>

      <AssistantSettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={(saved) => setSettings(saved)}
      />

      <Modal
        open={!!toDelete}
        onClose={() => !deleting && setToDelete(null)}
        size="sm"
        title={t.as_delete_title}
        footer={
          <>
            <Button variant="ghost" onClick={() => setToDelete(null)} disabled={deleting}>
              {t.cancel}
            </Button>
            <Button variant="danger" onClick={confirmDelete} loading={deleting}>
              {t.delete}
            </Button>
          </>
        }
      >
        <p className="text-[13.5px] leading-6 text-[var(--text-secondary)]">
          {t.as_delete_desc.replace("{title}", toDelete?.title || t.as_untitled)}
        </p>
      </Modal>
    </div>
  );
}
