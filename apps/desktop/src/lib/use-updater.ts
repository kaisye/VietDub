import { useCallback, useEffect, useState } from "react";

export type UpdaterStatus =
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "uptodate"
  | "error";

type DownloadEvent =
  | { event: "Started"; data: { contentLength?: number } }
  | { event: "Progress"; data: { chunkLength: number } }
  | { event: "Finished" };

type Update = {
  version: string;
  available: boolean;
  downloadAndInstall: (onEvent?: (event: DownloadEvent) => void) => Promise<void>;
};

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * Checks the Tauri updater endpoint once on mount. In a plain browser (dev
 * `vite`) it stays idle. When an update is published it exposes `install()`,
 * which downloads + installs the signed bundle and relaunches the app.
 */
export function useUpdater() {
  const [status, setStatus] = useState<UpdaterStatus>("idle");
  const [version, setVersion] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [update, setUpdate] = useState<Update | null>(null);

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    void (async () => {
      setStatus("checking");
      try {
        const { check } = await import("@tauri-apps/plugin-updater");
        const found = (await check()) as unknown as Update | null;
        if (cancelled) return;
        if (found && found.available) {
          setUpdate(found);
          setVersion(found.version);
          setStatus("available");
        } else {
          setStatus("uptodate");
        }
      } catch {
        if (!cancelled) setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const install = useCallback(async () => {
    if (!update) return;
    setStatus("downloading");
    setProgress(0);
    try {
      let total = 0;
      let done = 0;
      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? 0;
        } else if (event.event === "Progress") {
          done += event.data.chunkLength;
          if (total > 0) {
            setProgress(Math.min(100, Math.round((done / total) * 100)));
          }
        } else if (event.event === "Finished") {
          setProgress(100);
        }
      });
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch {
      setStatus("error");
    }
  }, [update]);

  return { status, version, progress, install };
}
