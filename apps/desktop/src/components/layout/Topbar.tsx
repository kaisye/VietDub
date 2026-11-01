import clsx from "clsx";
import { Bell, ChevronRight, CornerDownLeft, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useT } from "../../i18n";
import { StatusDot } from "../ui/badge";
import { SearchInput } from "../ui/form";
import { Tooltip } from "../ui/tooltip";
import { NAV_GROUPS } from "./nav";
import type { View } from "./nav";

export type BackendState = "connecting" | "online" | "offline";

function BackendStatus({ state, onRetry }: { state: BackendState; onRetry: () => void }) {
  const { t } = useT();
  const label =
    state === "online" ? t.shell_backend_online : state === "connecting" ? t.shell_backend_connecting : t.shell_backend_offline;
  const tone = state === "online" ? "success" : state === "connecting" ? "warning" : "danger";
  return (
    <button
      type="button"
      onClick={state === "offline" ? onRetry : undefined}
      className={clsx(
        "inline-flex h-8 items-center gap-2 rounded-full border px-3 text-xs font-medium transition",
        state === "online" && "cursor-default border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)]",
        state === "connecting" && "cursor-default border-[var(--warning-border)] bg-[var(--warning-soft)] text-[var(--warning)]",
        state === "offline" && "border-[var(--danger-border)] bg-[var(--danger-soft)] text-[var(--danger)] hover:border-[var(--danger)]",
      )}
    >
      <StatusDot tone={tone} pulse={state !== "online"} />
      {label}
      {state === "offline" ? <RefreshCw size={12} /> : null}
    </button>
  );
}

function GlobalSearch({ onNavigate, hasActiveJob }: { onNavigate: (view: View) => void; hasActiveJob: boolean }) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Cmd/Ctrl+K focuses the search box.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = NAV_GROUPS.flatMap((group) =>
      group.items
        .filter((item) => !item.requiresJob || hasActiveJob)
        .map((item) => ({ view: item.view, label: item.label(t), group: group.label(t), icon: item.icon })),
    );
    if (!q) return all;
    return all.filter((entry) => `${entry.label} ${entry.group}`.toLowerCase().includes(q));
  }, [query, t, hasActiveJob]);

  const choose = (view: View) => {
    onNavigate(view);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  };

  const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);

  return (
    <div className="relative w-full">
      <SearchInput
        ref={inputRef}
        value={query}
        placeholder={t.shell_search_placeholder}
        hint={isMac ? "⌘K" : "Ctrl K"}
        className="h-9 bg-[var(--surface-muted)] shadow-none"
        onChange={(event) => {
          setQuery(event.target.value);
          setCursor(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setCursor((c) => Math.min(c + 1, results.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setCursor((c) => Math.max(c - 1, 0));
          } else if (event.key === "Enter" && results[cursor]) {
            choose(results[cursor].view);
          } else if (event.key === "Escape") {
            setOpen(false);
            inputRef.current?.blur();
          }
        }}
      />
      {open ? (
        <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-50 overflow-hidden rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] p-1 shadow-[var(--shadow-float)] [animation:vd-pop-in_120ms_ease]">
          {results.length ? (
            results.map((entry, index) => {
              const Icon = entry.icon;
              return (
                <button
                  key={entry.view}
                  type="button"
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setCursor(index)}
                  onClick={() => choose(entry.view)}
                  className={clsx(
                    "flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left text-[13px]",
                    index === cursor ? "bg-[var(--primary-soft)] text-[var(--primary-ink)]" : "text-[var(--text-primary)]",
                  )}
                >
                  <Icon size={15} className={index === cursor ? "text-[var(--primary)]" : "text-[var(--text-tertiary)]"} />
                  <span className="flex-1 truncate font-medium">{entry.label}</span>
                  <span className="text-[11px] text-[var(--text-tertiary)]">{entry.group}</span>
                  {index === cursor ? <CornerDownLeft size={12} className="text-[var(--text-tertiary)]" /> : null}
                </button>
              );
            })
          ) : (
            <div className="px-3 py-4 text-center text-[13px] text-[var(--text-secondary)]">{t.shell_search_empty}</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function Notifications() {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <Tooltip content={t.shell_notifications} side="bottom" disabled={open}>
        <button
          type="button"
          aria-label={t.shell_notifications}
          onClick={() => setOpen((v) => !v)}
          className={clsx(
            "inline-flex h-9 w-9 items-center justify-center rounded-lg text-[var(--text-secondary)] transition hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]",
            open && "bg-[var(--surface-hover)] text-[var(--text-primary)]",
          )}
        >
          <Bell size={17} />
        </button>
      </Tooltip>
      {open ? (
        <div className="absolute right-0 top-[calc(100%+6px)] z-50 w-80 overflow-hidden rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-float)] [animation:vd-pop-in_120ms_ease]">
          <div className="border-b border-[var(--border-subtle)] px-4 py-3 text-[13px] font-semibold">{t.shell_notifications}</div>
          <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--primary-soft)] text-[var(--primary)]">
              <Bell size={18} />
            </span>
            <span className="text-[13px] text-[var(--text-secondary)]">{t.shell_notifications_empty}</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function Topbar({
  view,
  backend,
  onRetryBackend,
  onNavigate,
  hasActiveJob,
  actions,
}: {
  view: View;
  backend: BackendState;
  onRetryBackend: () => void;
  onNavigate: (view: View) => void;
  hasActiveJob: boolean;
  actions?: ReactNode;
}) {
  const { t } = useT();
  const crumbs = useMemo(() => {
    for (const group of NAV_GROUPS) {
      const target = view === "progress" ? "history" : view;
      const item = group.items.find((entry) => entry.view === target);
      if (item) {
        const list = [group.label(t), item.label(t)];
        if (view === "progress") list.push(t.nav_progress);
        return list;
      }
    }
    return [t.app_title];
  }, [view, t]);

  return (
    <header className="relative z-20 grid h-[var(--topbar-height)] shrink-0 grid-cols-[minmax(0,1fr)_minmax(240px,380px)_minmax(0,1fr)] items-center gap-4 border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--surface)_88%,transparent)] px-6 backdrop-blur-md">
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 overflow-hidden text-[13px]">
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1;
          return (
            <span key={`${crumb}-${index}`} className="flex min-w-0 items-center gap-1.5">
              {index > 0 ? <ChevronRight size={14} className="shrink-0 text-[var(--text-tertiary)]" /> : null}
              <span className={clsx("truncate", last ? "font-semibold text-[var(--text-primary)]" : "text-[var(--text-tertiary)]")}>
                {crumb}
              </span>
            </span>
          );
        })}
      </nav>
      <GlobalSearch onNavigate={onNavigate} hasActiveJob={hasActiveJob} />
      <div className="flex min-w-0 items-center justify-end gap-2">
        {actions}
        <BackendStatus state={backend} onRetry={onRetryBackend} />
        <Notifications />
      </div>
    </header>
  );
}
