import { useCallback, useMemo, useState } from "react";
import { useT } from "../../../i18n";
import type { EditorAudioTrack, EditorWorkspace } from "../../../types";
import type { EditorController } from "../useEditor";
import type { DroppedAsset } from "./AudioTracks";
import { useMusicLibrary } from "./library";
import { DuckingCache, fits, makeClip, makeTrack, voiceIntervals } from "./model";

/**
 * Screen-level glue for the music lanes: track CRUD on top of editor.saveAudioTracks (one undo
 * step each), preview-only solo, and "add this library asset at time t" (timeline drops and the
 * Music tab's "add at playhead").
 */
export function useAudioTracks(editor: EditorController, builtInSolo: "dub" | "original" | null) {
  const { t } = useT();
  const workspace = editor.workspace as EditorWorkspace | null;
  const tracks = useMemo(() => workspace?.audio_tracks ?? [], [workspace?.audio_tracks]);
  const library = useMusicLibrary();
  const [solo, setSolo] = useState<Set<string>>(() => new Set());
  const names = useMemo(() => ({ music: t.mu_track_music, sfx: t.mu_track_sfx }), [t]);

  const anySolo = solo.size > 0 || builtInSolo !== null;
  const audible = useCallback(
    (track: EditorAudioTrack) => !track.muted && (!anySolo || solo.has(track.id)),
    [anySolo, solo],
  );
  const ducking = useMemo(
    () => new DuckingCache(workspace ? voiceIntervals(workspace) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [workspace?.segments, workspace?.audio],
  );

  const save = editor.saveAudioTracks;
  const addTrack = useCallback(
    (kind: "music" | "sfx" = "music") => {
      const count = tracks.filter((track) => track.kind === kind).length + 1;
      save([...tracks, makeTrack(kind, count, names)], "add-track");
    },
    [tracks, save, names],
  );
  const patchTrack = useCallback(
    (id: string, patch: Partial<EditorAudioTrack>) =>
      save(tracks.map((track) => (track.id === id ? { ...track, ...patch } : track)), `track:${id}`),
    [tracks, save],
  );
  const deleteTrack = useCallback(
    (id: string) => {
      save(tracks.filter((track) => track.id !== id), "delete-track");
      setSolo((current) => {
        if (!current.has(id)) return current;
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    },
    [tracks, save],
  );
  const toggleSolo = useCallback((id: string) => {
    setSolo((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  /** Adds the asset at `at`: on the given lane if it fits, else the first free lane of its kind, else a new lane. */
  const addAsset = useCallback(
    (asset: DroppedAsset, at: number, trackId: string | null = null) => {
      if (!workspace) return;
      const clip = makeClip(asset, at, workspace.duration);
      const kind = asset.kind === "sfx" ? "sfx" : "music";
      const wanted = trackId ? tracks.find((track) => track.id === trackId && !track.locked) : undefined;
      const target =
        (wanted && fits(wanted, clip.start, clip.duration) ? wanted : undefined) ??
        (trackId ? undefined : tracks.find((track) => !track.locked && track.kind === kind && fits(track, clip.start, clip.duration)));
      let next: EditorAudioTrack[];
      if (target) {
        next = tracks.map((track) =>
          track.id === target.id ? { ...track, clips: [...track.clips, clip].sort((a, b) => a.start - b.start) } : track,
        );
      } else {
        const count = tracks.filter((track) => track.kind === kind).length + 1;
        next = [...tracks, { ...makeTrack(kind, count, names), clips: [clip] }];
      }
      save(next, "add-clip");
      editor.selectClips([clip.id]);
    },
    [workspace, tracks, save, names, editor],
  );

  const assetUrl = useCallback(
    (id: string) => {
      const asset = library.byId.get(id);
      return asset && !asset.missing ? asset.file_url : null;
    },
    [library.byId],
  );

  return {
    tracks,
    assets: library.byId,
    library,
    solo,
    anySolo,
    audible,
    ducking,
    addTrack,
    patchTrack,
    deleteTrack,
    toggleSolo,
    addAsset,
    assetUrl,
  };
}

export type AudioTracksController = ReturnType<typeof useAudioTracks>;
