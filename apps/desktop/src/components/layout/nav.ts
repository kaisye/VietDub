import {
  AudioLines,
  Bot,
  CalendarDays,
  Flame,
  Clapperboard,
  Cpu,
  FolderKanban,
  LayoutDashboard,
  Scissors,
  Settings,
  Tv,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { T } from "../../i18n";

/** Every top-level screen of the app. "progress" is reached from New/Projects and has no sidebar entry. */
export type View =
  | "dashboard"
  | "trends"
  | "channels"
  | "calendar"
  | "new"
  | "history"
  | "progress"
  | "editor"
  | "voices"
  | "assistant"
  | "features"
  | "config";

export type NavItem = {
  view: View;
  icon: LucideIcon;
  label: (t: T) => string;
  /** Needs an active job (editor). */
  requiresJob?: boolean;
};

export type NavGroup = {
  id: string;
  label: (t: T) => string;
  items: NavItem[];
  /** Rendered in the sidebar's bottom area instead of the scrolling list. */
  pinnedBottom?: boolean;
};

export const NAV_GROUPS: NavGroup[] = [
  {
    id: "overview",
    label: (t) => t.shell_group_overview,
    items: [
      { view: "dashboard", icon: LayoutDashboard, label: (t) => t.nav_dashboard },
      { view: "trends", icon: Flame, label: (t) => t.nav_trends },
    ],
  },
  {
    id: "channels",
    label: (t) => t.shell_group_channels,
    items: [
      { view: "channels", icon: Tv, label: (t) => t.nav_channels },
      { view: "calendar", icon: CalendarDays, label: (t) => t.nav_calendar },
    ],
  },
  {
    id: "studio",
    label: (t) => t.shell_group_studio,
    items: [
      { view: "new", icon: Clapperboard, label: (t) => t.nav_new },
      { view: "history", icon: FolderKanban, label: (t) => t.nav_projects },
      { view: "editor", icon: Scissors, label: (t) => t.nav_editor, requiresJob: true },
      { view: "voices", icon: AudioLines, label: (t) => t.nav_voices },
    ],
  },
  {
    id: "ai",
    label: (t) => t.shell_group_ai,
    items: [{ view: "assistant", icon: Bot, label: (t) => t.nav_assistant }],
  },
  {
    id: "system",
    label: (t) => t.shell_group_system,
    pinnedBottom: true,
    items: [
      { view: "features", icon: Cpu, label: (t) => t.nav_features },
      { view: "config", icon: Settings, label: (t) => t.nav_config },
    ],
  },
];

/** Which sidebar entry is highlighted for a view (progress lives under Projects). */
export function navOwner(view: View): View {
  return view === "progress" ? "history" : view;
}

export function findNav(view: View): { group: NavGroup; item: NavItem } | null {
  const owner = navOwner(view);
  for (const group of NAV_GROUPS) {
    const item = group.items.find((entry) => entry.view === owner);
    if (item) return { group, item };
  }
  return null;
}
