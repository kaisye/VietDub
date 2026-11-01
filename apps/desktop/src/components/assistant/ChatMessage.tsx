import clsx from "clsx";
import { AlertCircle, Check, ChevronDown, CircleStop, Info, Loader2, RefreshCw, Settings2, Sparkles } from "lucide-react";
import { memo, useState } from "react";
import { useT } from "../../i18n";
import type { AssistantMode, Message } from "../../lib/assistant-api";
import { Button, InlineNotice } from "../ui";
import { CopyButton } from "./CopyButton";
import { Markdown } from "./Markdown";
import { describeError, toolDoneLabel, toolRunningLabel } from "./shared";

export type ToolActivity = {
  id: string;
  name: string;
  input: Record<string, unknown>;
  status: "running" | "ok" | "error";
  summary?: string;
  /** Raw JSON result (only available for conversations loaded from history). */
  raw?: string;
};

export type ChatNotice = { code: string; message: string };

export type UiMessage = {
  key: string;
  role: "user" | "assistant";
  content: string;
  mode?: AssistantMode | null;
  tools: ToolActivity[];
  notices: ChatNotice[];
  error?: { code: string; message: string } | null;
  streaming?: boolean;
  model?: string | null;
};

/**
 * Folds persisted rows into chat bubbles: consecutive assistant rows (one per tool round)
 * become a single answer; tool rows are attached to their tool call chips.
 */
export function buildUiMessages(messages: Message[]): UiMessage[] {
  const out: UiMessage[] = [];
  let current: UiMessage | null = null;
  for (const row of messages) {
    if (row.role === "user") {
      current = null;
      out.push({ key: row.id, role: "user", content: row.content, mode: row.mode, tools: [], notices: [] });
    } else if (row.role === "assistant") {
      if (!current) {
        current = { key: row.id, role: "assistant", content: "", mode: row.mode, tools: [], notices: [], model: row.model };
        out.push(current);
      }
      current.content += row.content || "";
      current.model = row.model || current.model;
      for (const call of row.tool_calls ?? []) {
        current.tools.push({ id: call.id, name: call.name, input: call.input ?? {}, status: "ok" });
      }
      if (row.error) current.error = { code: row.error, message: "" };
    } else if (row.role === "tool" && current) {
      const tool = current.tools.find((x) => x.id === row.tool_call_id);
      if (tool) {
        tool.raw = row.content;
        tool.status = row.is_error ? "error" : "ok";
      }
    }
  }
  return out;
}

function prettyJson(value: unknown): string {
  try {
    const text = typeof value === "string" ? JSON.stringify(JSON.parse(value), null, 2) : JSON.stringify(value, null, 2);
    return text.length > 6000 ? `${text.slice(0, 6000)}\n…` : text;
  } catch {
    return String(value).slice(0, 6000);
  }
}

function ToolChip({ tool }: { tool: ToolActivity }) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const running = tool.status === "running";
  const hasInput = tool.input && Object.keys(tool.input).length > 0;
  return (
    <div className={clsx("min-w-0", open && "basis-full")}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={clsx(
          "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-medium transition",
          running
            ? "border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] text-[var(--primary-hover)]"
            : tool.status === "error"
              ? "border-[var(--warning-border)] bg-[var(--warning-soft)] text-[var(--warning)]"
              : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]",
        )}
      >
        {running ? (
          <Loader2 size={12} className="animate-spin" />
        ) : tool.status === "error" ? (
          <AlertCircle size={12} />
        ) : (
          <Check size={12} className="text-[var(--success)]" />
        )}
        <span className="truncate">
          {running ? toolRunningLabel(t, tool.name) : toolDoneLabel(t, tool.name)}
          {!running && tool.summary ? <span className="text-[var(--text-tertiary)]"> · {tool.summary}</span> : null}
        </span>
        <ChevronDown size={12} className={clsx("shrink-0 transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <div className="mt-1.5 grid gap-2 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-2.5 text-[12px]">
          <div className="flex items-center gap-2 font-mono text-[11.5px] text-[var(--text-tertiary)]">
            <span className="rounded bg-[var(--surface-sunken)] px-1.5 py-0.5">{tool.name}</span>
          </div>
          {hasInput ? (
            <div>
              <div className="mb-1 font-semibold text-[var(--text-secondary)]">{t.as_tool_input}</div>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[var(--surface)] p-2 font-mono text-[11.5px] leading-4">
                {prettyJson(tool.input)}
              </pre>
            </div>
          ) : null}
          <div>
            <div className="mb-1 font-semibold text-[var(--text-secondary)]">{t.as_tool_result}</div>
            {tool.raw ? (
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[var(--surface)] p-2 font-mono text-[11.5px] leading-4">
                {prettyJson(tool.raw)}
              </pre>
            ) : (
              <p className="text-[var(--text-secondary)]">{running ? t.as_tool_waiting : tool.summary || t.as_tool_no_detail}</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// Blinking caret appended to the last rendered block while text streams in.
const STREAM_CARET =
  "[&>div>:last-child]:after:ml-0.5 [&>div>:last-child]:after:inline-block [&>div>:last-child]:after:h-[1.05em] [&>div>:last-child]:after:w-[7px] [&>div>:last-child]:after:translate-y-[3px] [&>div>:last-child]:after:animate-pulse [&>div>:last-child]:after:rounded-[2px] [&>div>:last-child]:after:bg-[var(--primary)] [&>div>:last-child]:after:content-['']";

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 py-2" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--primary)] opacity-70"
          style={{ animationDelay: `${i * 140}ms`, animationDuration: "900ms" }}
        />
      ))}
    </span>
  );
}

export const ChatMessage = memo(function ChatMessage({
  message,
  isLast,
  canRegenerate,
  onRegenerate,
  onOpenSettings,
  onRetry,
}: {
  message: UiMessage;
  isLast: boolean;
  canRegenerate: boolean;
  onRegenerate: () => void;
  onOpenSettings: () => void;
  onRetry: () => void;
}) {
  const { t } = useT();

  if (message.role === "user") {
    return (
      <div className="group flex justify-end">
        <div className="flex max-w-[78%] flex-col items-end gap-1">
          <div className="whitespace-pre-wrap break-words rounded-[18px] rounded-br-md bg-[var(--primary)] px-4 py-2.5 text-[14px] leading-6 text-white shadow-[var(--shadow-xs)]">
            {message.content}
          </div>
          <div className="opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            <CopyButton text={message.content} label={t.as_copy} copiedLabel={t.as_copied} size="xs" iconOnly />
          </div>
        </div>
      </div>
    );
  }

  const cancelled = message.error?.code === "cancelled";
  const err = message.error && !cancelled ? describeError(t, message.error.code, message.error.message) : null;
  const runningTool = message.tools.some((x) => x.status === "running");
  const thinking = message.streaming && !message.content && !runningTool;

  return (
    <div className="group flex gap-3.5">
      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-[var(--primary)] to-[var(--accent-violet)] text-white shadow-[var(--shadow-sm)]">
        <Sparkles size={15} />
      </div>
      <div className="min-w-0 flex-1 pt-0.5">
        {message.tools.length ? (
          <div className="mb-2.5 flex flex-wrap gap-1.5">
            {message.tools.map((tool) => (
              <ToolChip key={tool.id} tool={tool} />
            ))}
          </div>
        ) : null}

        {message.notices.map((n, i) => (
          <div
            key={`${n.code}-${i}`}
            className="mb-2.5 flex items-start gap-2 rounded-[var(--radius-control)] bg-[var(--surface-muted)] px-3 py-2 text-[12.5px] leading-5 text-[var(--text-secondary)]"
          >
            <Info size={14} className="mt-0.5 shrink-0 text-[var(--info)]" />
            {n.message}
          </div>
        ))}

        {message.content ? (
          <div className={clsx(message.streaming && STREAM_CARET)}>
            <Markdown source={message.content} copyLabel={t.as_copy} copiedLabel={t.as_copied} />
          </div>
        ) : null}
        {thinking ? (
          <div className="flex items-center gap-2 text-[13px] text-[var(--text-tertiary)]">
            <TypingDots />
            {t.as_thinking}
          </div>
        ) : null}

        {cancelled ? (
          <div className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-[var(--surface-sunken)] px-2.5 py-1 text-[11.5px] font-medium text-[var(--text-secondary)]">
            <CircleStop size={12} /> {t.as_stopped}
          </div>
        ) : null}

        {err ? (
          <InlineNotice
            tone="danger"
            title={err.title}
            className="mt-3"
            action={
              err.action === "settings" ? (
                <Button size="sm" leadingIcon={<Settings2 size={14} />} onClick={onOpenSettings}>
                  {t.as_open_settings}
                </Button>
              ) : err.action === "retry" && isLast ? (
                <Button size="sm" leadingIcon={<RefreshCw size={14} />} onClick={onRetry}>
                  {t.retry}
                </Button>
              ) : undefined
            }
          >
            {err.body}
          </InlineNotice>
        ) : null}

        {!message.streaming && (message.content || isLast) ? (
          <div
            className={clsx(
              "mt-2 flex items-center gap-0.5 transition-opacity",
              isLast ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
            )}
          >
            {message.content ? <CopyButton text={message.content} label={t.as_copy} copiedLabel={t.as_copied} size="xs" /> : null}
            {isLast && canRegenerate ? (
              <button
                type="button"
                onClick={onRegenerate}
                className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11.5px] font-medium text-[var(--text-tertiary)] transition-colors hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
              >
                <RefreshCw size={12} />
                {t.as_regenerate}
              </button>
            ) : null}
            {message.model ? <span className="ml-2 font-mono text-[11px] text-[var(--text-tertiary)]">{message.model}</span> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
});
