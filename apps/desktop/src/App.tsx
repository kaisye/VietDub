import { useCallback, useEffect, useState } from "react";
import logoUrl from "./assets/vietdub-icon.svg";
import { API_BASE_URL, getRuntimeSettings, pingWithRetry } from "./api";
import ConfigScreen from "./screens/Config";
import NewVideoScreen from "./screens/NewVideo";
import ProgressScreen from "./screens/Progress";
import HistoryScreen from "./screens/History";
import VoiceConfigScreen from "./screens/VoiceConfig";
import FeaturesScreen from "./screens/Features";
import { I18nProvider, useT, tt } from "./i18n";
import { createDefaultDraft, saveCreateDraft } from "./lib/create-draft";
import { openUrl } from "./lib/open-url";
import { useUpdater } from "./lib/use-updater";
import type { Job } from "./types";

type View = "new" | "history" | "config" | "voices" | "features" | "progress";

export type ToolDefaults = { targetLanguage: string; voice: string };

const DEFAULTS_KEY = "vd.defaults";
const SETUP_SEEN_KEY = "vd.setup.seen";

// Keep in sync with package.json
const APP_VERSION = "0.1.7";
const CONTACT_EMAIL = "vietdub.contact@gmail.com";

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

function AppInner() {
  const { t, lang, setLang } = useT();
  const [view, setView] = useState<View>("new");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [backendReady, setBackendReady] = useState<boolean | null>(null);
  const [connectAttempt, setConnectAttempt] = useState(0);
  const updater = useUpdater();

  const connectBackend = useCallback(async () => {
    setBackendReady(null);
    setConnectAttempt(0);
    const ok = await pingWithRetry(20, 1000, (remaining) =>
      setConnectAttempt(20 - remaining + 1),
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
      /* stay on new */
    }
  }, []);

  useEffect(() => {
    void connectBackend();
  }, [connectBackend]);

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

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          <img src={logoUrl} className="brand-logo" alt="" aria-hidden="true" />
          {t.app_title}<span className="dot">.</span>
        </span>
        <nav className="nav">
          <button className={view === "new" ? "active" : ""} onClick={() => setView("new")}>
            {t.nav_create}
          </button>
          <button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>
            {t.nav_history}
          </button>
          <button className={view === "voices" ? "active" : ""} onClick={() => setView("voices")}>
            {t.nav_voices}
          </button>
          <button className={view === "features" ? "active" : ""} onClick={() => setView("features")}>
            {t.nav_features}
          </button>
        </nav>
        <span className="spacer" />
        <button
          className="lang-toggle"
          title="Switch language / Đổi ngôn ngữ"
          onClick={() => setLang(lang === "vi" ? "en" : "vi")}
        >
          {lang === "vi" ? "EN" : "VI"}
        </button>
        <button
          className={`iconbtn ${view === "config" ? "active" : ""}`}
          title={t.nav_config}
          onClick={() => setView("config")}
        >
          ⚙ {t.nav_config}
        </button>
      </header>

      {(updater.status === "available" || updater.status === "downloading" || updater.status === "error") && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.75rem",
            padding: "0.55rem 1rem",
            background: updater.status === "error" ? "#7f1d1d" : "#16324f",
            color: "#eaf2fb",
            fontSize: "0.85rem",
            borderBottom: "1px solid rgba(255,255,255,0.08)",
          }}
        >
          {updater.status === "available" ? (
            <>
              <span>
                {lang === "vi"
                  ? `Đã có bản cập nhật ${updater.version ?? ""}.`
                  : `Update ${updater.version ?? ""} is available.`}
              </span>
              <button className="btn primary" onClick={() => void updater.install()}>
                {lang === "vi" ? "Cập nhật & khởi động lại" : "Update & restart"}
              </button>
            </>
          ) : updater.status === "downloading" ? (
            <span>
              {lang === "vi" ? "Đang tải bản cập nhật" : "Downloading update"} {updater.progress}%
            </span>
          ) : (
            <>
              <span>
                {lang === "vi" ? "Cập nhật thất bại." : "Update failed."}{" "}
                {updater.error ?? ""}
              </span>
              <button className="btn primary" onClick={() => void updater.check()}>
                {lang === "vi" ? "Kiểm tra lại" : "Check again"}
              </button>
            </>
          )}
        </div>
      )}

      <main className="content">
        {backendReady === null ? (
          <div className="page">
            <div className="connecting">
              <div className="spinner" />
              <p>{tt(t.connecting_count, { n: connectAttempt })}</p>
            </div>
          </div>
        ) : backendReady === false ? (
          <div className="page">
            <div className="banner err">
              {t.backend_not_found}{" "}
              <span className="mono">{API_BASE_URL}</span>{" "}
              {lang === "vi" ? "sau 20 giây." : "after 20 seconds."}
            </div>
            <div className="backend-help card">
              <p><strong>{t.backend_help_title}</strong></p>
              <ul>
                <li>
                  {t.backend_help_1}{" "}
                  <span className="mono">launch.bat</span>{" "}
                  {t.backend_help_2}{" "}
                  <span className="mono">npm run desktop:dev</span>
                </li>
                <li>{t.backend_help_3}</li>
                <li>
                  {t.backend_help_4}{" "}
                  <span className="mono">pip install -r apps/api/requirements.txt</span>
                </li>
              </ul>
              <button className="btn primary" onClick={() => void connectBackend()}>
                {t.retry_connect}
              </button>
            </div>
          </div>
        ) : view === "config" ? (
          <ConfigScreen onSaved={() => setView("new")} />
        ) : view === "voices" ? (
          <VoiceConfigScreen onBack={() => setView("new")} />
        ) : view === "new" ? (
          <NewVideoScreen onStarted={openJob} />
        ) : view === "history" ? (
          <HistoryScreen onOpen={openJob} onEdit={editJob} />
        ) : view === "features" ? (
          <FeaturesScreen />
        ) : view === "progress" && activeJobId ? (
          <ProgressScreen jobId={activeJobId} onBack={() => setView("new")} />
        ) : (
          <NewVideoScreen onStarted={openJob} />
        )}
      </main>

      <footer className="appfooter">
        © {new Date().getFullYear()} {t.app_title} · v{APP_VERSION} · {t.footer_rights}
        {" · "}
        {t.footer_contact}:{" "}
        <button
          className="footer-email"
          onClick={() => void openUrl(`mailto:${CONTACT_EMAIL}`)}
        >
          {CONTACT_EMAIL}
        </button>
      </footer>
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
