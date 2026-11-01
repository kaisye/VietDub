import clsx from "clsx";
import { Languages, Mail, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useT } from "../../i18n";
import { Tooltip } from "../ui/tooltip";
import { BrandMark } from "./BrandMark";
import { NAV_GROUPS, navOwner } from "./nav";
import type { NavItem, View } from "./nav";

function NavButton({
  item,
  active,
  collapsed,
  disabled,
  disabledHint,
  onClick,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
  disabled: boolean;
  disabledHint?: string;
  onClick: () => void;
}) {
  const { t } = useT();
  const Icon = item.icon;
  const label = item.label(t);
  const button = (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-current={active ? "page" : undefined}
      aria-label={collapsed ? label : undefined}
      className={clsx(
        "group relative flex h-9 w-full items-center gap-3 rounded-lg text-[13.5px] font-medium transition-colors",
        collapsed ? "justify-center px-0" : "px-3",
        active
          ? "bg-[var(--primary-soft)] text-[var(--primary-hover)]"
          : "text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]",
        disabled && "cursor-not-allowed opacity-45 hover:bg-transparent hover:text-[var(--text-secondary)]",
      )}
    >
      {active ? (
        <span className="absolute -left-3 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-[var(--primary)]" aria-hidden="true" />
      ) : null}
      <Icon size={17} strokeWidth={active ? 2.2 : 1.9} className="shrink-0" />
      {!collapsed ? <span className="truncate">{label}</span> : null}
    </button>
  );
  const tip = collapsed ? (disabled && disabledHint ? `${label} — ${disabledHint}` : label) : disabled ? disabledHint : undefined;
  return tip ? (
    <Tooltip content={tip} side="right" className="w-full">
      {button}
    </Tooltip>
  ) : (
    button
  );
}

export function Sidebar({
  view,
  onNavigate,
  collapsed,
  onToggleCollapsed,
  hasActiveJob,
  appVersion,
  contactEmail,
  onContact,
}: {
  view: View;
  onNavigate: (view: View) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  hasActiveJob: boolean;
  appVersion: string;
  contactEmail: string;
  onContact: () => void;
}) {
  const { t, lang, setLang } = useT();
  const owner = navOwner(view);
  const mainGroups = NAV_GROUPS.filter((group) => !group.pinnedBottom);
  const bottomGroups = NAV_GROUPS.filter((group) => group.pinnedBottom);

  const renderGroup = (group: (typeof NAV_GROUPS)[number]) => (
    <div key={group.id} className="grid gap-0.5">
      {collapsed ? (
        <div className="mx-auto my-1.5 h-px w-6 bg-[var(--border)]" aria-hidden="true" />
      ) : (
        <div className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
          {group.label(t)}
        </div>
      )}
      {group.items.map((item) => (
        <NavButton
          key={item.view}
          item={item}
          active={owner === item.view}
          collapsed={collapsed}
          disabled={Boolean(item.requiresJob && !hasActiveJob)}
          disabledHint={item.requiresJob ? t.shell_editor_hint : undefined}
          onClick={() => onNavigate(item.view)}
        />
      ))}
    </div>
  );

  return (
    <aside
      className="relative z-30 flex h-full shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)] transition-[width] duration-200"
      style={{ width: collapsed ? "var(--sidebar-width-collapsed)" : "var(--sidebar-width)" }}
    >
      {/* Brand */}
      <div className={clsx("flex h-[var(--topbar-height)] shrink-0 items-center gap-2.5 border-b border-[var(--border-subtle)]", collapsed ? "justify-center px-2" : "px-5")}>
        <BrandMark size={30} />
        {!collapsed ? (
          <div className="min-w-0 leading-tight">
            <div className="text-[15px] font-bold tracking-[-0.02em] text-[var(--text-primary)]">
              {t.app_title}
              <span className="ml-1 rounded bg-[var(--primary-soft)] px-1.5 py-px align-middle text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--primary)]">
                Studio
              </span>
            </div>
          </div>
        ) : null}
      </div>

      {/* Main nav */}
      <nav className={clsx("min-h-0 flex-1 overflow-y-auto overflow-x-hidden py-2", collapsed ? "px-2.5" : "px-3")} aria-label="Main">
        {mainGroups.map(renderGroup)}
      </nav>

      {/* Bottom: system nav + language + version */}
      <div className={clsx("shrink-0 border-t border-[var(--border-subtle)] pb-3", collapsed ? "px-2.5" : "px-3")}>
        {bottomGroups.map(renderGroup)}

        <div className={clsx("mt-3 flex items-center gap-1.5", collapsed ? "flex-col" : "")}>
          <Tooltip content={t.shell_language} side={collapsed ? "right" : "top"}>
            <button
              type="button"
              onClick={() => setLang(lang === "vi" ? "en" : "vi")}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2.5 text-xs font-semibold text-[var(--text-secondary)] transition hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
              aria-label={t.shell_language}
            >
              <Languages size={14} />
              {lang === "vi" ? "VI" : "EN"}
            </button>
          </Tooltip>
          {!collapsed ? <span className="flex-1" /> : null}
          <Tooltip content={collapsed ? t.shell_expand : t.shell_collapse} side={collapsed ? "right" : "top"}>
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label={collapsed ? t.shell_expand : t.shell_collapse}
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-[var(--text-tertiary)] transition hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
            >
              {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
          </Tooltip>
        </div>

        {!collapsed ? (
          <div className="mt-2 flex items-center justify-between gap-2 px-1 text-[11px] text-[var(--text-tertiary)]">
            <span className="tabular-nums">
              {t.shell_version} {appVersion}
            </span>
            <button
              type="button"
              onClick={onContact}
              title={contactEmail}
              className="inline-flex items-center gap-1 rounded px-1 transition hover:text-[var(--primary)]"
            >
              <Mail size={12} />
              {t.footer_contact}
            </button>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
