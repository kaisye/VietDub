import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { outputUrl } from "../../api";
import type { EditorAudioTrack, EditorSegment, EditorWorkspace } from "../../types";
import { DubPlayer } from "../EditorDubPlayer";
import { originalVolumeAt } from "../EditorOriginalTrack";
import { DuckingCache, voiceIntervals } from "./music/model";
import { MusicPlayer } from "./music/MusicPlayer";
import { clamp, FRAME, segmentAt } from "./utils";

const SHUTTLE_RATES = [1, 1.5, 2];

/**
 * Preview transport: the <video> element is the master clock, voice takes follow it
 * through Web Audio (DubPlayer). `dubAudible` / `originalAudible` already include the
 * saved mute switches and the preview-only solo buttons.
 */
export function usePlayback(
  workspace: EditorWorkspace | null,
  {
    dubAudible,
    originalAudible,
    musicAudible,
    assetUrl,
  }: {
    dubAudible: boolean;
    originalAudible: boolean;
    /** Music / sound-effect track audible in the preview (saved mute + preview solo). */
    musicAudible?: (track: EditorAudioTrack) => boolean;
    /** Library asset id → file URL (null when the asset is missing). */
    assetUrl?: (id: string) => string | null;
  },
) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const previewRef = useRef<HTMLAudioElement | null>(null);
  const dubPlayer = useRef<DubPlayer | null>(null);
  if (!dubPlayer.current) dubPlayer.current = new DubPlayer();
  const musicPlayer = useRef<MusicPlayer | null>(null);
  if (!musicPlayer.current) musicPlayer.current = new MusicPlayer();
  const stopAt = useRef<number | null>(null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRateState] = useState(1);
  const segments = workspace?.segments;
  const duration = workspace?.duration ?? 0;

  const syncDub = useCallback(
    (t: number, isPlaying: boolean) => {
      const list = segments ?? [];
      const player = dubPlayer.current;
      const seg = isPlaying && dubAudible && player ? segmentAt(list, player.heardTime(t), "audio") : null;
      player?.sync(list, seg, t, isPlaying && dubAudible);
    },
    [segments, dubAudible],
  );

  // Ducking follows the saved voice timing (like the render), not the preview mute/solo.
  const audio = workspace?.audio;
  const tracks = workspace?.audio_tracks;
  const ducking = useMemo(
    () => new DuckingCache(segments ? voiceIntervals({ segments, audio } as EditorWorkspace) : []),
    [segments, audio],
  );
  const syncMusic = useCallback(
    (t: number, isPlaying: boolean) => {
      const player = musicPlayer.current;
      if (!player) return;
      if (!tracks?.length || !assetUrl || !musicAudible) {
        player.stopAll();
        return;
      }
      player.sync({ tracks, audible: musicAudible, ducking, assetUrl }, t, isPlaying);
    },
    [tracks, musicAudible, ducking, assetUrl],
  );

  useEffect(() => {
    if (!playing) {
      syncDub(videoRef.current?.currentTime ?? 0, false);
      syncMusic(videoRef.current?.currentTime ?? 0, false);
      return;
    }
    let frame = 0;
    let lastUi = 0;
    const tick = (now: number) => {
      const video = videoRef.current;
      if (video) {
        const t = video.currentTime;
        if (stopAt.current !== null && t >= stopAt.current) {
          stopAt.current = null;
          video.pause();
        }
        // Right after play()/seek the element reports "playing" before its clock moves;
        // hold the voice until the video can actually run, or the take starts early.
        const running = !video.paused && !video.seeking && video.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA;
        syncDub(t, running);
        syncMusic(t, running);
        if (now - lastUi > 50) {
          lastUi = now;
          setTime(t);
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, syncDub, syncMusic]);

  // The browser caps element volume at 100%; boosts above that are heard only in the render.
  const originalVolume = workspace?.audio?.original_volume ?? 100;
  const dubbedVolume = workspace?.audio?.dubbed_volume ?? 100;
  const regions = workspace?.original_regions;
  // Rounded so the effect only re-runs when the audible level changes.
  const effectiveOriginal = Math.round(originalVolumeAt(regions ?? [], originalVolume, time));
  useEffect(() => {
    if (!videoRef.current) return;
    videoRef.current.muted = !originalAudible;
    videoRef.current.volume = Math.min(1, effectiveOriginal / 100);
  }, [originalAudible, effectiveOriginal, workspace?.video_url]);

  useEffect(() => {
    dubPlayer.current?.setVolume(dubbedVolume);
    if (previewRef.current) previewRef.current.volume = Math.min(1, dubbedVolume / 100);
  }, [dubbedVolume]);

  // Web Audio may only start after a user gesture; any click or key in the editor unlocks it.
  useEffect(() => {
    const player = dubPlayer.current;
    const music = musicPlayer.current;
    const unlock = () => {
      player?.unlock();
      music?.unlock();
    };
    window.addEventListener("pointerdown", unlock, true);
    window.addEventListener("keydown", unlock, true);
    return () => {
      window.removeEventListener("pointerdown", unlock, true);
      window.removeEventListener("keydown", unlock, true);
      player?.dispose();
      music?.dispose();
    };
  }, []);

  const setRate = useCallback((next: number) => {
    setRateState(next);
    if (videoRef.current) videoRef.current.playbackRate = next;
    dubPlayer.current?.setRate(next);
    musicPlayer.current?.setRate(next);
  }, []);

  const seek = useCallback(
    (t: number) => {
      const video = videoRef.current;
      if (!video) return;
      video.currentTime = clamp(t, 0, duration || video.duration || 0);
      setTime(video.currentTime);
    },
    [duration],
  );

  const play = useCallback(() => {
    const video = videoRef.current;
    if (video?.paused) void video.play();
  }, []);

  const pause = useCallback(() => {
    videoRef.current?.pause();
  }, []);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    stopAt.current = null;
    if (video.paused) {
      void video.play();
    } else {
      video.pause();
      setRate(1);
    }
  }, [setRate]);

  /** J / K / L: K stops, L plays and steps up the speed, J jumps back 5 s. */
  const shuttle = useCallback(
    (key: "j" | "k" | "l") => {
      const video = videoRef.current;
      if (!video) return;
      stopAt.current = null;
      if (key === "k") {
        video.pause();
        setRate(1);
      } else if (key === "l") {
        if (video.paused) {
          setRate(1);
          void video.play();
        } else {
          setRate(SHUTTLE_RATES[Math.min(SHUTTLE_RATES.length - 1, SHUTTLE_RATES.indexOf(rate) + 1)]);
        }
      } else {
        seek(video.currentTime - 5);
      }
    },
    [rate, seek, setRate],
  );

  const step = useCallback(
    (seconds: number) => {
      const video = videoRef.current;
      if (!video) return;
      if (!video.paused) video.pause();
      seek(video.currentTime + seconds);
    },
    [seek],
  );
  const stepFrames = useCallback((frames: number) => step(frames * FRAME), [step]);

  const playRange = useCallback(
    (start: number, end: number) => {
      const video = videoRef.current;
      if (!video) return;
      seek(start);
      stopAt.current = end;
      void video.play();
    },
    [seek],
  );

  const playSegment = useCallback(
    (seg: EditorSegment) =>
      playRange(Math.min(seg.start, seg.audio_start), Math.max(seg.end, seg.audio_start + seg.audio_duration) + 0.15),
    [playRange],
  );

  /** Plays one take on its own (inspector ▶ buttons). */
  const previewTake = useCallback((url: string | null | undefined) => {
    const player = previewRef.current;
    if (!player || !url) return;
    videoRef.current?.pause();
    player.src = outputUrl(url) ?? "";
    void player.play().catch(() => undefined);
  }, []);

  const currentTime = useCallback(() => videoRef.current?.currentTime ?? time, [time]);

  const videoHandlers = {
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
    onSeeked: (event: React.SyntheticEvent<HTMLVideoElement>) => setTime(event.currentTarget.currentTime),
  };

  return {
    videoRef,
    previewRef,
    time,
    playing,
    rate,
    seek,
    play,
    pause,
    togglePlay,
    shuttle,
    step,
    stepFrames,
    playRange,
    playSegment,
    previewTake,
    currentTime,
    videoHandlers,
  };
}

export type Playback = ReturnType<typeof usePlayback>;
