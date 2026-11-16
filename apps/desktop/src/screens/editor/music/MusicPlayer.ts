import { outputUrl } from "../../../api";
import type { EditorAudioClip, EditorAudioTrack } from "../../../types";
import { outputLatency } from "../audioDelay";
import { clipEnvelope, filePosition, type DuckingCache } from "./model";

const LOOKAHEAD = 12; // seconds of upcoming music clips decoded ahead of the playhead
const MAX_DRIFT = 0.12; // same hysteresis as the dub player (EditorDubPlayer.ts)…
const DRIFT_FRAMES = 4;
const JUMP = 0.35;
const CACHE_SIZE = 8; // decoded songs are large (≈10 MB per stereo minute)

type Playing = {
  key: string;
  source: AudioBufferSourceNode;
  gain: GainNode;
  ctxStart: number;
  /** Clip-local timeline position (s) at ctxStart. */
  local: number;
  driftFrames: number;
};

export type MusicSyncInput = {
  tracks: EditorAudioTrack[];
  /** Saved mute + preview-only solo, decided by the screen. */
  audible: (track: EditorAudioTrack) => boolean;
  ducking: DuckingCache;
  /** Asset id → file URL (storage path) or null when unknown / missing. */
  assetUrl: (id: string) => string | null;
};

/**
 * Plays the editor's music / sound-effect clips in step with the video through Web Audio,
 * like DubPlayer does for the voice: buffers are decoded ahead, clips are picked at
 * heardTime(t) (output latency compensation), small clock jitter is tolerated before a
 * re-sync, and each clip's GainNode follows the same fade + ducking formula as the render.
 */
export class MusicPlayer {
  private ctx: AudioContext | null = null;
  private rate = 1;
  private buffers = new Map<string, AudioBuffer | null>();
  private loading = new Map<string, Promise<void>>();
  private playing = new Map<string, Playing>();
  private lastPrefetch = -1;
  private lastClock = { ctx: 0, wall: 0 };

  unlock(): void {
    if (!this.ctx || this.ctx.state === "closed") this.ctx = new AudioContext();
    if (this.ctx.state === "suspended") void this.ctx.resume();
  }

  setRate(rate: number): void {
    if (rate === this.rate) return;
    this.rate = rate;
    this.stopAll();
  }

  heardTime(t: number): number {
    return t + outputLatency(this.ctx) * this.rate;
  }

  sync(input: MusicSyncInput, t: number, isPlaying: boolean): void {
    if (!isPlaying || !this.ctx) {
      this.stopAll();
      return;
    }
    if (this.ctx.state !== "running") void this.ctx.resume();
    if (this.clockStalled()) {
      this.rebuild();
      return;
    }
    const heard = this.heardTime(t);
    this.prefetch(input, t);
    const active = new Map<string, { track: EditorAudioTrack; clip: EditorAudioClip; url: string }>();
    for (const track of input.tracks) {
      if (!input.audible(track)) continue;
      for (const clip of track.clips) {
        if (heard < clip.start || heard >= clip.start + clip.duration) continue;
        const url = outputUrl(input.assetUrl(clip.asset_id));
        if (url) active.set(clip.id, { track, clip, url });
      }
    }
    for (const id of [...this.playing.keys()]) if (!active.has(id)) this.stopClip(id);

    const ctx = this.ctx;
    for (const [id, { track, clip, url }] of active) {
      const local = heard - clip.start;
      const target = (clip.volume / 100) * (track.volume / 100) * clipEnvelope(clip, heard, input.ducking);
      const key = `${url}|${clip.start}|${clip.in}|${clip.duration}|${clip.loop}`;
      const current = this.playing.get(id);
      if (current?.key === key) {
        const position = (ctx.currentTime - current.ctxStart) * this.rate + current.local;
        const drift = Math.abs(position - local);
        if (drift <= MAX_DRIFT || (drift < JUMP && ++current.driftFrames < DRIFT_FRAMES)) {
          if (drift <= MAX_DRIFT) current.driftFrames = 0;
          current.gain.gain.setTargetAtTime(target, ctx.currentTime, 0.012);
          continue;
        }
      }
      this.stopClip(id);
      const buffer = this.buffers.get(url);
      if (buffer === undefined) {
        void this.load(url);
        continue;
      }
      if (!buffer) continue;
      const offset = filePosition(clip, heard, buffer.duration);
      if (offset === null) continue;
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.playbackRate.value = this.rate;
      if (clip.loop) {
        source.loop = true;
        source.loopStart = 0;
        source.loopEnd = buffer.duration;
      }
      const gain = ctx.createGain();
      gain.gain.value = target;
      source.connect(gain);
      gain.connect(ctx.destination);
      source.start(0, Math.max(0, offset));
      source.stop(ctx.currentTime + Math.max(0, clip.duration - local) / this.rate);
      this.playing.set(id, { key, source, gain, ctxStart: ctx.currentTime, local, driftFrames: 0 });
    }
  }

  stopAll(): void {
    for (const id of [...this.playing.keys()]) this.stopClip(id);
  }

  dispose(): void {
    this.stopAll();
    void this.ctx?.close();
    this.ctx = null;
    this.buffers.clear();
  }

  private stopClip(id: string): void {
    const entry = this.playing.get(id);
    if (!entry) return;
    try {
      entry.source.stop();
    } catch {
      // Already ended.
    }
    entry.source.disconnect();
    entry.gain.disconnect();
    this.playing.delete(id);
  }

  private clockStalled(): boolean {
    const ctx = this.ctx!;
    const wall = performance.now() / 1000;
    if (ctx.state !== "running" || ctx.currentTime !== this.lastClock.ctx || !this.lastClock.wall) {
      this.lastClock = { ctx: ctx.currentTime, wall };
      return false;
    }
    return wall - this.lastClock.wall > 0.6;
  }

  private rebuild(): void {
    this.stopAll();
    const old = this.ctx;
    this.ctx = null;
    this.lastClock = { ctx: 0, wall: 0 };
    void old?.close().catch(() => undefined);
    this.unlock();
  }

  private prefetch(input: MusicSyncInput, t: number): void {
    const bucket = Math.floor(t * 2);
    if (bucket === this.lastPrefetch) return;
    this.lastPrefetch = bucket;
    for (const track of input.tracks) {
      if (!input.audible(track)) continue;
      for (const clip of track.clips) {
        if (clip.start + clip.duration >= t && clip.start <= t + LOOKAHEAD) {
          const url = outputUrl(input.assetUrl(clip.asset_id));
          if (url) void this.load(url);
        }
      }
    }
  }

  private load(url: string): Promise<void> {
    if (this.buffers.has(url)) return Promise.resolve();
    const pending = this.loading.get(url);
    if (pending) return pending;
    const ctx = this.ctx;
    if (!ctx) return Promise.resolve();
    const task = fetch(url)
      .then((response) => (response.ok ? response.arrayBuffer() : Promise.reject(new Error(String(response.status)))))
      .then((data) => ctx.decodeAudioData(data))
      .then(
        (buffer) => this.remember(url, buffer),
        () => this.remember(url, null),
      )
      .finally(() => this.loading.delete(url));
    this.loading.set(url, task);
    return task;
  }

  private remember(url: string, buffer: AudioBuffer | null): void {
    this.buffers.set(url, buffer);
    while (this.buffers.size > CACHE_SIZE) {
      const oldest = this.buffers.keys().next().value as string;
      if ([...this.playing.values()].some((entry) => entry.key.startsWith(`${oldest}|`))) break;
      this.buffers.delete(oldest);
    }
  }
}
