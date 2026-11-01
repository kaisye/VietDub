import { outputUrl } from "../api";
import type { EditorSegment } from "../types";

const LOOKAHEAD = 8; // seconds of upcoming voice cues decoded ahead of the playhead
const MAX_DRIFT = 0.12; // re-sync only when the voice is this far from the video…
const DRIFT_FRAMES = 4; // …for this many frames in a row (video clock jitter / start-up)
const JUMP = 0.35; // a drift this large is a seek: re-sync at once
const CACHE_SIZE = 40;

type Playing = { key: string; source: AudioBufferSourceNode; ctxStart: number; offset: number };

/**
 * Plays voice cues in step with the video through Web Audio.
 *
 * Cues are fetched and decoded ahead of the playhead, so a cue starts instantly
 * at its exact offset; an <audio> element instead has to load each new file
 * while the video keeps running, and the resulting catch-up seeks are heard as
 * stutter. A GainNode also lets the preview go above 100 %.
 */
export class DubPlayer {
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private volume = 1;
  // Shuttle speed (J/K/L): takes play faster too, so they stay on the video's clock.
  private rate = 1;
  private buffers = new Map<string, AudioBuffer | null>();
  private loading = new Map<string, Promise<void>>();
  private playing: Playing | null = null;
  private driftFrames = 0;
  private lastPrefetch = -1;
  // Detects a context whose clock stopped (macOS output device change, sleep): it is then rebuilt.
  private lastClock = { ctx: 0, wall: 0 };

  /** Creates/resumes the context; call from a user gesture (WebKit starts it suspended). */
  unlock(): void {
    if (!this.ctx || this.ctx.state === "closed") {
      this.ctx = new AudioContext();
      this.gain = this.ctx.createGain();
      this.gain.gain.value = this.volume;
      this.gain.connect(this.ctx.destination);
    }
    if (this.ctx.state === "suspended") void this.ctx.resume();
  }

  setVolume(percent: number): void {
    this.volume = Math.max(0, percent / 100);
    if (this.gain) this.gain.gain.value = this.volume;
  }

  setRate(rate: number): void {
    if (rate === this.rate) return;
    this.rate = rate;
    this.stop(); // Restarted on the next frame at the new speed and offset.
  }

  /**
   * Timeline position heard right now when the video shows `t`: Web Audio is heard only
   * after the output latency (large on Bluetooth), so voice cues are picked this far ahead
   * and start on time instead of losing their first syllable.
   */
  heardTime(t: number): number {
    return t + this.latency() * this.rate;
  }

  /** Called every animation frame while the video plays; `seg` is the cue at heardTime(t). */
  sync(segments: EditorSegment[], seg: EditorSegment | null, t: number, isPlaying: boolean): void {
    if (!isPlaying || !this.ctx) {
      this.stop();
      return;
    }
    if (this.ctx.state !== "running") void this.ctx.resume();
    if (this.clockStalled()) {
      this.rebuild();
      return;
    }
    this.prefetch(segments, t);
    const url = seg ? outputUrl(seg.audio_url) : null;
    if (!seg || !url) {
      this.stop();
      return;
    }
    const key = `${seg.id}|${url}|${seg.audio_start}`;
    const offset = this.heardTime(t) - seg.audio_start;
    if (this.playing?.key === key) {
      const position = (this.ctx.currentTime - this.playing.ctxStart) * this.rate + this.playing.offset;
      const drift = Math.abs(position - offset);
      // A one-off mismatch is the video's coarse clock, not real drift; restarting the take
      // for it is heard as a stutter (it replays the start of the cue).
      if (drift <= MAX_DRIFT || (drift < JUMP && ++this.driftFrames < DRIFT_FRAMES)) {
        if (drift <= MAX_DRIFT) this.driftFrames = 0;
        return;
      }
    }
    this.driftFrames = 0;
    this.stop();
    const buffer = this.buffers.get(url);
    if (buffer === undefined) {
      // Not decoded yet (e.g. right after a seek): start it on a later frame at the right offset.
      void this.load(url);
      return;
    }
    if (!buffer || offset >= buffer.duration) return;
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = this.rate;
    source.connect(this.gain!);
    source.start(0, Math.max(0, offset));
    this.playing = { key, source, ctxStart: this.ctx.currentTime, offset: Math.max(0, offset) };
  }

  stop(): void {
    if (!this.playing) return;
    try {
      this.playing.source.stop();
    } catch {
      // Already ended.
    }
    this.playing.source.disconnect();
    this.playing = null;
  }

  private latency(): number {
    const ctx = this.ctx as (AudioContext & { outputLatency?: number }) | null;
    if (!ctx) return 0;
    return Math.min(0.5, (ctx.baseLatency || 0) + (ctx.outputLatency || 0));
  }

  private clockStalled(): boolean {
    const ctx = this.ctx!;
    const wall = performance.now() / 1000;
    // A suspended context (no gesture yet) is waiting, not broken.
    if (ctx.state !== "running" || ctx.currentTime !== this.lastClock.ctx || !this.lastClock.wall) {
      this.lastClock = { ctx: ctx.currentTime, wall };
      return false;
    }
    return wall - this.lastClock.wall > 0.6;
  }

  private rebuild(): void {
    // Decoded buffers are tied to no context and are kept.
    this.stop();
    const old = this.ctx;
    this.ctx = null;
    this.lastClock = { ctx: 0, wall: 0 };
    void old?.close().catch(() => undefined);
    this.unlock();
  }

  dispose(): void {
    this.stop();
    void this.ctx?.close();
    this.ctx = null;
    this.buffers.clear();
  }

  private prefetch(segments: EditorSegment[], t: number): void {
    // Twice a second is plenty; scanning every frame would waste the main thread.
    const bucket = Math.floor(t * 2);
    if (bucket === this.lastPrefetch) return;
    this.lastPrefetch = bucket;
    for (const seg of segments) {
      if (seg.muted || !seg.audio_url) continue;
      const end = seg.audio_start + seg.audio_duration;
      if (end >= t && seg.audio_start <= t + LOOKAHEAD) {
        const url = outputUrl(seg.audio_url);
        if (url) void this.load(url);
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
    // Map keeps insertion order: drop the oldest decoded cues.
    while (this.buffers.size > CACHE_SIZE) {
      const oldest = this.buffers.keys().next().value as string;
      this.buffers.delete(oldest);
    }
  }
}
