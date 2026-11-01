import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Sparkles, TriangleAlert } from "lucide-react";
import { API_BASE_URL, getRuntimeSettings, pingWithRetry } from "./api";
import ConfigScreen from "./screens/Config";
import NewVideoScreen from "./screens/NewVideo";
import ProgressScreen from "./screens/Progress";
import HistoryScreen from "./screens/History";
import VoiceConfigScreen from "./screens/VoiceConfig";
import FeaturesScreen from "./screens/Features";
import EditorScreen from "./screens/Editor";
import DashboardScreen from "./screens/Dashboard";
import ChannelsScreen from "./screens/Channels";
import CalendarScreen from "./screens/Calendar";
import TrendsScreen from "./screens/Trends";
import AssistantScreen from "./screens/Assistant";
import { Sidebar } from "./components/layout/Sidebar";
import { Topbar } from "./components/layout/Topbar";
import type { BackendState } from "./components/layout/Topbar";
import type { View } from "./components/layout/nav";
import { Button } from "./components/ui/button";
import { ProgressBar } from "./components/ui/feedback";
import { I18nProvider, useT, tt } from "./i18n";
import { createDefaultDraft, saveCreateDraft } from "./lib/create-draft";
import { openUrl } from "./lib/open-url";
import { useUpdater } from "./lib/use-updater";
import type { Job } from "./types";

export type { View } from "./components/layout/nav";

export type ToolDefaults = { targetLanguage: string; voice: string };

const DEFAULTS_KEY = "vd.defaults";
const SETUP_SEEN_KEY = "vd.setup.seen";
const SIDEBAR_KEY = "vd.sidebar.collapsed";

const CONTACT_EMAIL = "vietdub.contact@gmail.com";

// The packaged backend is a one-file PyInstaller bundle that self-extracts on
// every launch; with the bundled OCR native libs it can take well over
// 20s to come up on a cold first launch (macOS also scans the freshly extracted
// payload). Give it a generous budget so a slow start isn't reported as a
// failure. Keep the displayed timeout in sync with these constants.
const CONNECT_ATTEMPTS = 90;
const CONNECT_INTERVAL_MS = 1000;
const CONNECT_TIMEOUT_SECONDS = Math.round((CONNECT_ATTEMPTS * CONNECT_INTERVAL_MS) / 1000);

const ALL_VIEWS: View[] = [
  "dashboard",
  "trends",
  "channels",
  "calendar",
  "new",
  "history",
  "progress",
  "editor",
  "voices",
  "assistant",
  "features",
  "config",
];

export function loadDefaults(): ToolDefaults {
  try {
    const raw = localStorage.getItem(DEFAULTS_KEY);
    if (raw) return { targetLanguage: "VI", voice: "", ...JSON.parse(raw) };
  } catch {
    /* ignore */
  }
  return { targetLanguage: "VI", voice: "" };
}

export function saveDefaults(defaults: ToolDefaults) {
  localStorage.setItem(DEFAULTS_KEY, JSON.stringify(defaults));
}

/** Dev-only deep link (`?view=channels`) so layouts can be reviewed/screenshotted directly. */
function initialView(): View {
  if (import.meta.env.DEV) {
    try {
      const requested = new URLSearchParams(window.location.search).get("view") as View | null;
      if (requested && ALL_VIEWS.includes(requested)) {
        // A deep link means "show me this view": skip the first-run redirect to config.
        localStorage.setItem(SETUP_SEEN_KEY, "1");
        return requested;
      }
    } catch {
      /* ignore */
    }
  }
  return "dashboard";
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

function UpdateBanner() {
  const { t } = useT();
  const updater = useUpdater();
  if (updater.status !== "available" && updater.status !== "downloading" && updater.status !== "error") return null;
  const isError = updater.status === "error";
  return (
    <div
      className={`flex shrink-0 items-center gap-3 border-b px-6 py-2 text-[13px] ${
        isError
          ? "border-[var(--danger-border)] bg-[var(--danger-soft)] text-[var(--danger)]"
          : "border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] text-[var(--primary-ink)]"
      }`}
    >
      {isError ? <TriangleAlert size={15} /> : <Sparkles size={15} className="text-[var(--primary)]" />}
      {updater.status === "available" ? (
        <>
          <span className="flex-1">{tt(t.shell_update_available, { v: updater.version ?? "" })}</span>
          <Button size="sm" variant="primary" onClick={() => void updater.install()}>
            {t.shell_update_install}
          </Button>
        </>
      ) : updater.status === "downloading" ? (
        <>
          <span>{tt(t.shell_update_downloading, { p: updater.progress })}</span>
          <ProgressBar value={updater.progress} size="sm" className="max-w-56" />
        </>
      ) : (
        <>
          <span className="flex-1">
            {t.shell_update_failed} {updater.error ?? ""}
          </span>
          <Button size="sm" variant="secondary" onClick={() => void updater.check()}>
            {t.shell_update_retry}
          </Button>
        </>
      )}
    </div>
  );
}

function AppInner() {
  const { t } = useT();
  const [view, setView] = useState<View>(initialView);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [backendReady, setBackendReady] = useState<boolean | null>(null);
  const [connectAttempt, setConnectAttempt] = useState(0);
  const [appVersion, setAppVersion] = useState("0.1.9");
  const [collapsed, setCollapsed] = useState(readCollapsed);

  useEffect(() => {
    if ("__TAURI_INTERNALS__" in window) {
      import("@tauri-apps/api/app")
        .then(({ getVersion }) => getVersion())
        .then(setAppVersion)
        .catch(() => {});
    }
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const connectBackend = useCallback(async () => {
    setBackendReady(null);
    setConnectAttempt(0);
    const ok = await pingWithRetry(CONNECT_ATTEMPTS, CONNECT_INTERVAL_MS, (remaining) =>
      setConnectAttempt(CONNECT_ATTEMPTS - remaining + 1),
    );
    setBackendReady(ok);
    if (!ok) return;
    try {
      await getRuntimeSettings();
      if (!localStorage.getItem(SETUP_SEEN_KEY)) {
        localStorage.setItem(SETUP_SEEN_KEY, "1");
        setView("config");
      }
    } catch {
      /* stay on the current view */
    }
  }, []);

  useEffect(() => {
    void connectBackend();
  }, [connectBackend]);

  const navigate = useCallback(
    (next: View) => {
      if (next === "editor" && !activeJobId) return;
      if (next === "progress" && !activeJobId) {
        setView("history");
        return;
      }
      setView(next);
    },
    [activeJobId],
  );

  const openJob = useCallback((jobId: string) => {
    setActiveJobId(jobId);
    setView("progress");
  }, []);

  const editJob = useCallback((job: Job) => {
    const draft = createDefaultDraft("quick_video");
    draft.source.method = "url";
    draft.source.url = job.video_url;
    draft.source.title = job.source_title || job.video_url;
    draft.languages.source = job.source_language || "auto";
    draft.languages.target = job.target_language || "VI";
    draft.voice.voice_id = job.voice || draft.voice.voice_id;
    if (job.download_quality === "best" || job.download_quality === "reliable") {
      draft.source.download_quality = job.download_quality;
    }
    if (job.render_quality === "fast" || job.render_quality === "balanced" || job.render_quality === "quality") {
      draft.output.render_quality = job.render_quality;
    }
    if (typeof job.test_clip_seconds === "number" && job.test_clip_seconds > 0) {
      draft.source.test_clip_seconds = job.test_clip_seconds;
    }
    saveCreateDraft(draft);
    setView("new");
  }, []);

  const backendState: BackendState = backendReady === null ? "connecting" : backendReady ? "online" : "offline";

  const renderView = () => {
    switch (view) {
      case "dashboard":
        return (
          <DashboardScreen
            onNavigate={navigate}
            onOpenJob={openJob}
            onOpenEditor={(jobId) => {
              setActiveJobId(jobId);
              setView("editor");
            }}
          />
        );
      case "channels":
        return <ChannelsScreen onNavigate={navigate} />;
      case "trends":
        return <TrendsScreen onNavigate={navigate} />;
      case "calendar":
        return <CalendarScreen onNavigate={navigate} />;
      case "assistant":
        return <AssistantScreen onNavigate={navigate} />;
      case "config":
        return <ConfigScreen onSaved={() => setView("new")} />;
      case "voices":
        return <VoiceConfigScreen onBack={() => setView("new")} />;
      case "history":
        return <HistoryScreen onOpen={openJob} onEdit={editJob} />;
      case "features":
        return <FeaturesScreen onOpenVoiceSetup={() => setView("voices")} />;
      case "progress":
        return activeJobId ? (
          <ProgressScreen jobId={activeJobId} onBack={() => setView("new")} onOpenEditor={() => setView("editor")} />
        ) : (
          <HistoryScreen onOpen={openJob} onEdit={editJob} />
        );
      case "editor":
        return activeJobId ? (
          <EditorScreen
            jobId={activeJobId}
            onBack={() => setView("progress")}
            onOpenAssistant={() => setView("assistant")}
          />
        ) : (
          <HistoryScreen onOpen={openJob} onEdit={editJob} />
        );
      case "new":
      default:
        return <NewVideoScreen onStarted={openJob} />;
    }
  };

  return (
    <div className="app">
      <Sidebar
        view={view}
        onNavigate={navigate}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        hasActiveJob={Boolean(activeJobId)}
        appVersion={appVersion}
        contactEmail={CONTACT_EMAIL}
        onContact={() => void openUrl(`mailto:${CONTACT_EMAIL}`)}
      />

      <div className="app-main">
        <Topbar
          view={view}
          backend={backendState}
          onRetryBackend={() => void connectBackend()}
          onNavigate={navigate}
          hasActiveJob={Boolean(activeJobId)}
        />
        <UpdateBanner />

        <main className="content">
          {backendReady === null ? (
            <div className="page">
              <div className="connecting">
                <div className="spinner" />
                <p className="m-0 text-[15px] font-semibold text-[var(--text-primary)]">
                  {tt(t.connecting_count, { n: connectAttempt, total: CONNECT_ATTEMPTS })}
                </p>
                <p className="m-0 text-[13px] text-[var(--text-secondary)]">{t.shell_first_launch_hint}</p>
                <ProgressBar value={connectAttempt} max={CONNECT_ATTEMPTS} size="sm" className="mt-2 max-w-72" />
              </div>
            </div>
          ) : backendReady === false ? (
            <div className="page" style={{ maxWidth: 760 }}>
              <div className="banner err">
                {t.backend_not_found} <span className="mono">{API_BASE_URL}</span>{" "}
                {tt(t.shell_backend_after, { n: CONNECT_TIMEOUT_SECONDS })}
              </div>
              <div className="backend-help card">
                <p>
                  <strong>{t.backend_help_title}</strong>
                </p>
                <ul>
                  <li>
                    {t.backend_help_1} <span className="mono">launch.bat</span> {t.backend_help_2}{" "}
                    <span className="mono">npm run desktop:dev</span>
                  </li>
                  <li>{t.backend_help_3}</li>
                  <li>
                    {t.backend_help_4} <span className="mono">pip install -r apps/api/requirements.txt</span>
                  </li>
                </ul>
                <Button variant="primary" leadingIcon={<RefreshCw size={15} />} onClick={() => void connectBackend()}>
                  {t.retry_connect}
                </Button>
              </div>
            </div>
          ) : (
            renderView()
          )}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <I18nProvider>
      <AppInner />
    </I18nProvider>
  );
}
