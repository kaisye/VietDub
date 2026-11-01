import type { EditorAudioClip, EditorAudioTrack, EditorDucking, EditorWorkspace, MusicAsset } from "../../../types";

/**
 * Audio-track math shared by the timeline, the inspector and the preview player.
 * Gains mirror the server render (services/music_mix.py): linear fades (ffmpeg afade "tri"),
 * and ducking = 1 − (1 − 10^(dB/20)) · d(t), where d ramps 0→1 over [s − attack, s], holds
 * through the line and ramps back over [e, e + release]; lines closer than attack + release merge.
 */

export const DEFAULT_DUCKING: EditorDucking = { enabled: true, amount_db: -12, attack: 0.3, release: 0.6 };
export const MIN_CLIP = 0.1;
export const ASSET_MIME = "application/x-vietdub-music";

export type Interval = [number, number];

export const clipEnd = (clip: EditorAudioClip) => clip.start + clip.duration;

export function newId(prefix: "atr" | "clip"): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Where the dub voice is heard: voiced, non-muted segments (the render's rule). */
export function voiceIntervals(workspace: EditorWorkspace): Interval[] {
  if (workspace.audio?.dubbed_enabled === false) return [];
  const out: Interval[] = [];
  for (const seg of workspace.segments) {
    if (seg.muted || !seg.audio_url || !(seg.audio_duration > 0)) continue;
    out.push([seg.audio_start, seg.audio_start + seg.audio_duration]);
  }
  return out.sort((a, b) => a[0] - b[0]);
}

export function mergeIntervals(intervals: Interval[], gap: number): Interval[] {
  const merged: Interval[] = [];
  for (const [start, end] of intervals) {
    const last = merged[merged.length - 1];
    if (last && start - last[1] <= gap) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

/** 0–1 ducking depth at t (binary search: intervals are sorted and non-overlapping). */
export function duckDepth(t: number, merged: Interval[], attack: number, release: number): number {
  let lo = 0;
  let hi = merged.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (merged[mid][0] - attack <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  let depth = 0;
  for (const i of [found, found - 1]) {
    if (i < 0) continue;
    const [s, e] = merged[i];
    depth = Math.max(depth, Math.max(0, Math.min(1, (t - (s - attack)) / attack, (e + release - t) / release)));
  }
  return depth;
}

/** Memoized merged intervals per ducking timing (clips usually share the defaults). */
export class DuckingCache {
  private cache = new Map<string, Interval[]>();
  constructor(private intervals: Interval[]) {}
  merged(ducking: EditorDucking): Interval[] {
    const key = `${ducking.attack}|${ducking.release}`;
    let hit = this.cache.get(key);
    if (!hit) {
      hit = mergeIntervals(this.intervals, ducking.attack + ducking.release);
      this.cache.set(key, hit);
    }
    return hit;
  }
}

/** Fade × ducking multiplier (0–1) of a clip at timeline time t (0 outside the clip). */
export function clipEnvelope(clip: EditorAudioClip, t: number, ducking: DuckingCache | null): number {
  const local = t - clip.start;
  if (local < 0 || local > clip.duration) return 0;
  let gain = 1;
  if (clip.fade_in > 0) gain = Math.min(gain, local / clip.fade_in);
  if (clip.fade_out > 0) gain = Math.min(gain, (clip.duration - local) / clip.fade_out);
  gain = Math.max(0, Math.min(1, gain));
  if (ducking && clip.ducking.enabled && clip.ducking.amount_db < 0) {
    const g = Math.pow(10, clip.ducking.amount_db / 20);
    gain *= 1 - (1 - g) * duckDepth(t, ducking.merged(clip.ducking), clip.ducking.attack, clip.ducking.release);
  }
  return gain;
}

/** Position in the file heard at timeline time t (loops restart the whole file, like -stream_loop). */
export function filePosition(clip: EditorAudioClip, t: number, assetDuration: number): number | null {
  const pos = clip.in + (t - clip.start);
  if (pos < 0) return null;
  if (clip.loop && assetDuration > 0) return pos % assetDuration;
  return pos < assetDuration || !assetDuration ? pos : null;
}

/** Longest the clip may last from its `in` point (unbounded when looping, up to the video end). */
export function maxClipLength(clip: Pick<EditorAudioClip, "in" | "loop" | "start">, asset: MusicAsset | undefined, videoDuration: number) {
  const room = videoDuration - clip.start;
  if (clip.loop || !asset) return Math.max(MIN_CLIP, room);
  return Math.max(MIN_CLIP, Math.min(room, asset.duration - clip.in));
}

export function makeClip(asset: Pick<MusicAsset, "id" | "title" | "duration" | "kind">, start: number, videoDuration: number): EditorAudioClip {
  const s = Math.max(0, Math.min(start, videoDuration - MIN_CLIP));
  const duration = Math.max(MIN_CLIP, Math.min(asset.duration, videoDuration - s));
  return {
    id: newId("clip"),
    asset_id: asset.id,
    title: asset.title,
    start: round3(s),
    in: 0,
    duration: round3(duration),
    volume: asset.kind === "sfx" ? 100 : 60,
    fade_in: asset.kind === "sfx" ? 0 : Math.min(1, round3(duration / 4)),
    fade_out: asset.kind === "sfx" ? 0 : Math.min(2, round3(duration / 4)),
    loop: false,
    ducking: { ...DEFAULT_DUCKING, enabled: asset.kind !== "sfx" },
  };
}

export function makeTrack(kind: "music" | "sfx", index: number, names: { music: string; sfx: string }): EditorAudioTrack {
  return {
    id: newId("atr"),
    name: `${kind === "music" ? names.music : names.sfx} ${index}`,
    kind,
    muted: false,
    locked: false,
    volume: 100,
    clips: [],
  };
}

/** Two halves at timeline time t; fades stay on the outer ends. */
export function splitClip(clip: EditorAudioClip, t: number): [EditorAudioClip, EditorAudioClip] | null {
  const local = t - clip.start;
  if (local < MIN_CLIP || clip.duration - local < MIN_CLIP) return null;
  const left: EditorAudioClip = {
    ...clip,
    duration: round3(local),
    fade_in: Math.min(clip.fade_in, round3(local)),
    fade_out: 0,
  };
  const rightLength = round3(clip.duration - local);
  const right: EditorAudioClip = {
    ...clip,
    id: newId("clip"),
    start: round3(t),
    in: round3(clip.in + local),
    duration: rightLength,
    fade_in: 0,
    fade_out: Math.min(clip.fade_out, rightLength),
  };
  return [left, right];
}

/** True when [start, start+length) does not overlap another clip of the track. */
export function fits(track: EditorAudioTrack, start: number, length: number, ignore: Set<string> = new Set()): boolean {
  return track.clips.every((clip) => ignore.has(clip.id) || clip.start >= start + length - 1e-6 || clipEnd(clip) <= start + 1e-6);
}

export function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

export function allClips(tracks: EditorAudioTrack[]): { track: EditorAudioTrack; clip: EditorAudioClip }[] {
  return tracks.flatMap((track) => track.clips.map((clip) => ({ track, clip })));
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds && seconds !== 0) return "—";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
