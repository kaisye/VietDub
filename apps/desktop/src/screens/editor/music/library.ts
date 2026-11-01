import { useEffect, useState, useSyncExternalStore } from "react";
import { getMusicPeaks, listMusic } from "../../../api";
import type { MusicAsset } from "../../../types";

/**
 * Shared music library state: the Music tab, the timeline lanes and the inspector all read
 * the same list (one request), and refresh it after imports / edits.
 */
type LibraryState = { items: MusicAsset[]; tags: string[]; loading: boolean; error: string | null; loaded: boolean };

let state: LibraryState = { items: [], tags: [], loading: false, error: null, loaded: false };
let byId = new Map<string, MusicAsset>();
const listeners = new Set<() => void>();
let inflight: Promise<void> | null = null;

function set(next: Partial<LibraryState>) {
  state = { ...state, ...next };
  if (next.items) byId = new Map(next.items.map((item) => [item.id, item]));
  listeners.forEach((listener) => listener());
}

export function refreshLibrary(): Promise<void> {
  if (inflight) return inflight;
  set({ loading: true });
  inflight = listMusic()
    .then((data) => set({ items: data.items, tags: data.tags, loading: false, error: null, loaded: true }))
    .catch((e: unknown) => set({ loading: false, error: e instanceof Error ? e.message : String(e), loaded: true }))
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Puts freshly imported / edited assets in place without waiting for a full reload. */
export function upsertAssets(assets: MusicAsset[]) {
  const ids = new Set(assets.map((asset) => asset.id));
  set({ items: [...assets, ...state.items.filter((item) => !ids.has(item.id))] });
}

export function removeAsset(id: string) {
  set({ items: state.items.filter((item) => item.id !== id) });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useMusicLibrary() {
  const snapshot = useSyncExternalStore(subscribe, () => state);
  useEffect(() => {
    if (!state.loaded && !inflight) void refreshLibrary();
  }, []);
  return { ...snapshot, byId, refresh: refreshLibrary };
}

export function assetById(id: string): MusicAsset | undefined {
  return byId.get(id);
}

// ---- waveform peaks (per asset, cached for the session) ---------------------------------
export type Peaks = { rate: number; peaks: number[] };
const peaksCache = new Map<string, Peaks | null>();
const peaksLoading = new Map<string, Promise<Peaks | null>>();

export function loadPeaks(id: string): Promise<Peaks | null> {
  if (peaksCache.has(id)) return Promise.resolve(peaksCache.get(id) ?? null);
  let pending = peaksLoading.get(id);
  if (!pending) {
    pending = getMusicPeaks(id)
      .then((data) => data, () => null)
      .then((data) => {
        peaksCache.set(id, data);
        peaksLoading.delete(id);
        return data;
      });
    peaksLoading.set(id, pending);
  }
  return pending;
}

export function usePeaks(id: string): Peaks | null {
  const [peaks, setPeaks] = useState<Peaks | null>(() => peaksCache.get(id) ?? null);
  useEffect(() => {
    let alive = true;
    void loadPeaks(id).then((data) => alive && setPeaks(data));
    return () => {
      alive = false;
    };
  }, [id]);
  return peaks;
}
