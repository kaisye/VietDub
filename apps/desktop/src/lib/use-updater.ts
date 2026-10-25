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
  download: (onEvent?: (event: DownloadEvent) => void) => Promise<void>;
  install: () => Promise<void>;
};

// Retry delays for the update check. The automatic check fires right at app
// launch, often before Wi-Fi/DNS (e.g. Tailscale) is ready, and the updater then
// fails with a bare "error sending request for url".
const CHECK_RETRY_DELAYS_MS = [3000, 10000];

function isNetworkError(message: string): boolean {
  return /error sending request|connection|timed out|dns|network|tls|certificate/i.test(message);
}

function friendlyError(message: string): string {
  if (isNetworkError(message)) {
    return "Không kết nối được máy chủ cập nhật (GitHub). Kiểm tra mạng/VPN rồi bấm Kiểm tra lại.";
  }
  return message;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

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
  const [error, setError] = useState<string | null>(null);

  /**
   * `manual` is true when the user pressed "Check again". The automatic launch
   * check stays silent on network failures: being offline for a moment is not
   * worth a red banner, and the next launch checks again.
   */
  const checkForUpdates = useCallback(async (manual = false) => {
    if (!isTauri()) return;
    setStatus("checking");
    setError(null);
    const { check } = await import("@tauri-apps/plugin-updater");
    let lastError = "";
    for (let attempt = 0; attempt <= CHECK_RETRY_DELAYS_MS.length; attempt += 1) {
      try {
        const found = (await check()) as unknown as Update | null;
        if (found && found.available) {
          setUpdate(found);
          setVersion(found.version);
          setStatus("available");
        } else {
          setUpdate(null);
          setVersion(null);
          setStatus("uptodate");
        }
        return;
      } catch (cause) {
        lastError = cause instanceof Error ? cause.message : String(cause);
        if (!isNetworkError(lastError) || attempt === CHECK_RETRY_DELAYS_MS.length) break;
        await sleep(CHECK_RETRY_DELAYS_MS[attempt]);
      }
    }
    if (!manual && isNetworkError(lastError)) {
      console.warn("Update check skipped (network):", lastError);
      setStatus("idle");
      return;
    }
    setError(friendlyError(lastError));
    setStatus("error");
  }, []);

  useEffect(() => {
    void checkForUpdates(false);
  }, [checkForUpdates]);

  const install = useCallback(async () => {
    if (!update) return;
    setStatus("downloading");
    setProgress(0);
    setError(null);
    try {
      let total = 0;
      let done = 0;
      await update.download((event) => {
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
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("prepare_for_update");
      await update.install();
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch (cause) {
      setError(friendlyError(cause instanceof Error ? cause.message : String(cause)));
      setStatus("error");
    }
  }, [update]);

  const checkManually = useCallback(() => checkForUpdates(true), [checkForUpdates]);

  return { status, version, progress, error, install, check: checkManually };
}
