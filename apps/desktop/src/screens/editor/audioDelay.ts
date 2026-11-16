/**
 * Output latency of the preview's Web Audio (voice takes and music clips).
 *
 * Chrome reports Bluetooth delay in `outputLatency`; WebKit (the macOS Tauri webview)
 * does not, while it still delays the <video> picture to match the headphones. Takes
 * would then be heard late, so the user can add the missing delay by hand. It is
 * remembered on this device only.
 */
const KEY = "vd.editor.audioDelayMs";
const MAX_MS = 600;

let extraMs = read();
const listeners = new Set<(ms: number) => void>();

function read(): number {
  try {
    const value = Number(localStorage.getItem(KEY));
    return Number.isFinite(value) ? Math.min(MAX_MS, Math.max(0, value)) : 0;
  } catch {
    return 0;
  }
}

export const AUDIO_DELAY_MAX_MS = MAX_MS;

export function audioDelayMs(): number {
  return extraMs;
}

export function setAudioDelayMs(ms: number): void {
  extraMs = Math.min(MAX_MS, Math.max(0, Math.round(ms)));
  try {
    localStorage.setItem(KEY, String(extraMs));
  } catch {
    // Private mode: keep it for this session only.
  }
  listeners.forEach((listener) => listener(extraMs));
}

export function onAudioDelayChange(listener: (ms: number) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Whether the browser reports output latency itself (false in WebKit). */
export function reportsOutputLatency(): boolean {
  return typeof AudioContext !== "undefined" && "outputLatency" in AudioContext.prototype;
}

/** Seconds between scheduling a sound and hearing it: what the browser reports plus the manual delay. */
export function outputLatency(ctx: AudioContext | null): number {
  if (!ctx) return extraMs / 1000;
  const reported = (ctx.baseLatency || 0) + ((ctx as AudioContext & { outputLatency?: number }).outputLatency || 0);
  return Math.min(0.5, reported) + extraMs / 1000;
}
