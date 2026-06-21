import { invoke } from "@tauri-apps/api/core";

/** Open a URL in the system default browser via Tauri, with window.open fallback. */
export async function openUrl(url: string): Promise<void> {
  try {
    await invoke("open_external_url", { url });
  } catch {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}
