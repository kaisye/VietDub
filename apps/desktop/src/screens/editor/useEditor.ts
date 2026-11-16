import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import {
  deleteEditorSegment,
  deleteEditorSegments,
  exportEditorWorkspace,
  getEditorWaveform,
  getEditorWorkspace,
  insertEditorSegment,
  mergeEditorSegmentWithNext,
  patchEditorWorkspace,
  putEditorState,
  regenerateEditorSegmentAudio,
  renderEditorWorkspace,
  retranslateEditorSegment,
  splitEditorSegment,
  swapEditorSegmentAudio,
  type EditorEditableState,
} from "../../api";
import type {
  EditorAudio,
  EditorAudioTrack,
  EditorBlur,
  EditorRegion,
  EditorSegment,
  EditorSegmentPatch,
  EditorStyle,
  EditorTextOverlay,
  EditorWorkspace,
} from "../../types";
import type { Waveform } from "../EditorOriginalTrack";
import { clipEnd, splitClip } from "./music/model";
import { editableState, errorText, round3, segmentAt, type Translate } from "./utils";

export type SaveState = { pending: number; savedAt: Date | null; failed: string | null };
export type CueDraft = { text: string; start: string; end: string };
export type SelectMode = "replace" | "toggle" | "range";

/**
 * Editor controller: the workspace, its autosave, client-side undo/redo and every
 * mutation. UI components only call these actions; playback lives in usePlayback.
 */
export function useEditor(jobId: string, L: Translate, seekRef: MutableRefObject<(t: number) => void>) {
  const [workspace, setWorkspace] = useState<EditorWorkspace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [selection, setSelection] = useState<{ ids: string[]; primary: string | null }>({ ids: [], primary: null });
  const [draft, setDraft] = useState<CueDraft | null>(null);
  const [waveform, setWaveform] = useState<Waveform | null>(null);
  const [selectedRegionId, setSelectedRegionId] = useState<string | null>(null);
  // Music / sound-effect clips selected on the timeline (exclusive with cues and regions).
  const [clipSelection, setClipSelection] = useState<string[]>([]);
  // Text box selected on the timeline or the preview (exclusive with cues, regions and clips).
  const [textSelection, setTextSelection] = useState<string | null>(null);
  const [batch, setBatch] = useState<{ done: number; total: number } | null>(null);
  const batchCancel = useRef(false);
  const textRef = useRef<HTMLTextAreaElement | null>(null);

  // Every write goes through `track`, so the toolbar can say whether edits reached disk.
  const [saveState, setSaveState] = useState<SaveState>({ pending: 0, savedAt: null, failed: null });
  const track = useCallback(<T,>(promise: Promise<T>): Promise<T> => {
    setSaveState((state) => ({ ...state, pending: state.pending + 1 }));
    return promise.then(
      (value) => {
        setSaveState((state) => ({ pending: state.pending - 1, savedAt: new Date(), failed: null }));
        return value;
      },
      (e: unknown) => {
        setSaveState((state) => ({ ...state, pending: state.pending - 1, failed: errorText(e) }));
        throw e;
      },
    );
  }, []);
  // Bumped after server-side text changes (translate, split, restore…) to reload the draft.
  const [draftSync, setDraftSync] = useState(0);
  const resync = useCallback(() => setDraftSync((value) => value + 1), []);

  // ---- undo / redo ----------------------------------------------------------
  // Each mutation first records the state it is about to change. Workspace updates are
  // immutable, so a snapshot only holds references; undo puts a snapshot back server-side.
  const workspaceRef = useRef(workspace);
  workspaceRef.current = workspace;
  const history = useRef<{
    undo: { state: EditorEditableState; kind: string; at: number }[];
    redo: EditorEditableState[];
  }>({ undo: [], redo: [] });
  const [historySize, setHistorySize] = useState({ undo: 0, redo: 0 });
  const syncHistorySize = useCallback(
    () => setHistorySize({ undo: history.current.undo.length, redo: history.current.redo.length }),
    [],
  );
  const checkpoint = useCallback(
    (kind: string, coalesce = false) => {
      const current = workspaceRef.current;
      if (!current) return;
      const last = history.current.undo[history.current.undo.length - 1];
      const now = Date.now();
      // Rapid edits of one kind (typing, dragging a colour) collapse into one step.
      if (coalesce && last && last.kind === kind && now - last.at < 1500) {
        last.at = now;
        return;
      }
      history.current.undo.push({ state: editableState(current), kind, at: now });
      if (history.current.undo.length > 100) history.current.undo.shift();
      history.current.redo = [];
      syncHistorySize();
    },
    [syncHistorySize],
  );

  const load = useCallback(
    async (rebuild = false) => {
      try {
        setError(null);
        const data = await getEditorWorkspace(jobId, rebuild);
        setWorkspace(data);
        setSelection((current) =>
          current.primary ? current : { ids: data.segments[0] ? [data.segments[0].id] : [], primary: data.segments[0]?.id ?? null },
        );
      } catch (e) {
        setError(errorText(e));
      }
    },
    [jobId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    getEditorWaveform(jobId).then(setWaveform).catch(() => undefined);
  }, [jobId]);

  // Poll while an edited render runs.
  const renderStatus = workspace?.render.status;
  useEffect(() => {
    if (renderStatus !== "running") return;
    const timer = window.setInterval(() => {
      getEditorWorkspace(jobId)
        .then((data) => setWorkspace((current) => (current ? { ...current, render: data.render } : data)))
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [jobId, renderStatus]);

  const segments = useMemo(() => workspace?.segments ?? [], [workspace?.segments]);
  const selectedId = selection.primary;
  const selectedIds = useMemo(() => new Set(selection.ids), [selection.ids]);
  const selected = useMemo(() => segments.find((seg) => seg.id === selectedId) ?? null, [segments, selectedId]);
  const selectedIndex = selected ? segments.indexOf(selected) : -1;

  // Drop ids that no longer exist (deleted, merged, undone).
  useEffect(() => {
    if (!workspace) return;
    const known = new Set(workspace.segments.map((seg) => seg.id));
    setSelection((current) => {
      const ids = current.ids.filter((id) => known.has(id));
      const primary = current.primary && known.has(current.primary) ? current.primary : (ids[ids.length - 1] ?? null);
      return ids.length === current.ids.length && primary === current.primary ? current : { ids, primary };
    });
  }, [workspace]);

  useEffect(() => {
    setDraft(selected ? { text: selected.text, start: selected.start.toFixed(2), end: selected.end.toFixed(2) } : null);
    // Not on every server echo: an autosave answer must not overwrite what is typed meanwhile.
  }, [selected?.id, draftSync]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty =
    !!selected &&
    !!draft &&
    (draft.text.trim() !== selected.text ||
      Math.abs(Number(draft.start) - selected.start) > 0.004 ||
      Math.abs(Number(draft.end) - selected.end) > 0.004);

  // ---- saving ---------------------------------------------------------------
  const draftState = useRef({ selected, draft, dirty });
  draftState.current = { selected, draft, dirty };
  /** Saves the line being edited. Reads refs so it can run on selection switch or unmount. */
  const saveDraft = useCallback(async () => {
    const { selected: segment, draft: current, dirty: changed } = draftState.current;
    if (!segment || !current || !changed) return true;
    const start = Number(current.start);
    const end = Number(current.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      setError(L("Thời gian không hợp lệ: kết thúc phải sau bắt đầu.", "Invalid timing: end must be after start."));
      return false;
    }
    checkpoint(`line:${segment.id}`, true);
    try {
      setWorkspace(await track(patchEditorWorkspace(jobId, { segments: [{ id: segment.id, text: current.text, start, end }] })));
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    }
  }, [track, checkpoint, jobId, L]);

  useEffect(() => {
    if (!dirty) return;
    const timer = window.setTimeout(() => void saveDraft(), 700);
    return () => window.clearTimeout(timer);
  }, [draft, dirty, saveDraft]);

  // Style edits preview instantly and are saved in one debounced request.
  const pendingStyle = useRef<Partial<EditorStyle>>({});
  const styleTimer = useRef<number | null>(null);
  const flushStyle = useCallback(async () => {
    if (styleTimer.current !== null) window.clearTimeout(styleTimer.current);
    styleTimer.current = null;
    const patch = pendingStyle.current;
    pendingStyle.current = {};
    if (!Object.keys(patch).length) return;
    try {
      const data = await track(patchEditorWorkspace(jobId, { style: patch }));
      // Keep newer local edits that are still waiting to be sent.
      setWorkspace({ ...data, style: { ...data.style, ...pendingStyle.current } });
    } catch (e) {
      setError(errorText(e));
    }
  }, [jobId, track]);
  const saveStyle = useCallback(
    (style: Partial<EditorStyle>) => {
      checkpoint("style", true);
      setWorkspace((current) => (current ? { ...current, style: { ...current.style, ...style } } : current));
      pendingStyle.current = { ...pendingStyle.current, ...style };
      if (styleTimer.current !== null) window.clearTimeout(styleTimer.current);
      styleTimer.current = window.setTimeout(() => void flushStyle(), 350);
    },
    [flushStyle, checkpoint],
  );

  const flushAll = useCallback(async () => {
    const [ok] = await Promise.all([saveDraft(), flushStyle()]);
    return ok;
  }, [saveDraft, flushStyle]);
  const flushRef = useRef(flushAll);
  flushRef.current = flushAll;
  useEffect(() => () => void flushRef.current(), []);

  const saveMaxLines = useCallback(
    (max_lines: number) => {
      checkpoint("max_lines");
      void track(patchEditorWorkspace(jobId, { max_lines }))
        .then(setWorkspace)
        .catch((e) => setError(errorText(e)));
    },
    [jobId, track, checkpoint],
  );

  const saveAudio = useCallback(
    (audio: Partial<EditorAudio>) => {
      checkpoint("audio");
      setWorkspace((current) => (current ? { ...current, audio: { ...current.audio, ...audio } } : current));
      void track(patchEditorWorkspace(jobId, { audio }))
        .then((data) => setWorkspace((current) => (current ? { ...current, audio: data.audio } : data)))
        .catch((e) => setError(errorText(e)));
    },
    [jobId, track, checkpoint],
  );

  const saveBlur = useCallback(
    (blur: Partial<EditorBlur>) => {
      checkpoint("blur");
      setWorkspace((current) => (current ? { ...current, blur: { ...current.blur, ...blur } } : current));
      void track(patchEditorWorkspace(jobId, { blur }))
        .then((data) => setWorkspace((current) => (current ? { ...current, blur: data.blur } : data)))
        .catch((e) => setError(errorText(e)));
    },
    [jobId, track, checkpoint],
  );

  const saveRegions = useCallback(
    (next: EditorRegion[], selectId?: string) => {
      checkpoint("regions");
      setWorkspace((current) => (current ? { ...current, original_regions: next } : current));
      if (selectId !== undefined) setSelectedRegionId(selectId);
      void track(patchEditorWorkspace(jobId, { original_regions: next }))
        .then((data) => setWorkspace((current) => (current ? { ...current, original_regions: data.original_regions } : data)))
        .catch((e) => setError(errorText(e)));
    },
    [jobId, track, checkpoint],
  );

  // ---- audio tracks (music / sound effects) ------------------------------------
  // Full-list saves like the regions; a sequence number drops answers to older saves so a
  // slow response never overwrites a newer local edit.
  const audioSeq = useRef(0);
  const saveAudioTracks = useCallback(
    (next: EditorAudioTrack[], kind = "audio-tracks", coalesce = false) => {
      checkpoint(kind, coalesce);
      setWorkspace((current) => (current ? { ...current, audio_tracks: next } : current));
      const seq = ++audioSeq.current;
      void track(patchEditorWorkspace(jobId, { audio_tracks: next }))
        .then((data) => {
          if (seq === audioSeq.current) setWorkspace((current) => (current ? { ...current, audio_tracks: data.audio_tracks } : data));
        })
        .catch((e) => {
          setError(errorText(e));
          if (seq === audioSeq.current) void load();
        });
    },
    [jobId, track, checkpoint, load],
  );

  const selectClips = useCallback((ids: string[], mode: SelectMode = "replace") => {
    void flushRef.current();
    setSelectedRegionId(null);
    setTextSelection(null);
    setSelection((current) => (current.ids.length ? { ids: [], primary: current.primary } : current));
    setClipSelection((current) => {
      if (mode === "toggle") {
        const set = new Set(current);
        for (const id of ids) {
          if (set.has(id)) set.delete(id);
          else set.add(id);
        }
        return [...set];
      }
      return ids;
    });
  }, []);

  // Drop clip ids that no longer exist (deleted, undone).
  useEffect(() => {
    if (!workspace) return;
    const known = new Set((workspace.audio_tracks ?? []).flatMap((item) => item.clips.map((clip) => clip.id)));
    setClipSelection((current) => (current.every((id) => known.has(id)) ? current : current.filter((id) => known.has(id))));
  }, [workspace]);

  const deleteClips = useCallback(() => {
    const current = workspaceRef.current;
    if (!current || !clipSelection.length) return;
    const doomed = new Set(clipSelection);
    saveAudioTracks(
      (current.audio_tracks ?? []).map((item) =>
        item.locked ? item : { ...item, clips: item.clips.filter((clip) => !doomed.has(clip.id)) },
      ),
      "delete-clips",
    );
    setClipSelection([]);
  }, [clipSelection, saveAudioTracks]);

  /** Splits every selected (unlocked) clip under the playhead; false when none is there. */
  const splitClipsAt = useCallback(
    (t: number) => {
      const current = workspaceRef.current;
      if (!current) return false;
      const wanted = new Set(clipSelection);
      let changed = false;
      const created: string[] = [];
      const next = (current.audio_tracks ?? []).map((item) => {
        if (item.locked) return item;
        const clips = item.clips.flatMap((clip) => {
          if (!wanted.has(clip.id) || t <= clip.start || t >= clipEnd(clip)) return [clip];
          const halves = splitClip(clip, t);
          if (!halves) return [clip];
          changed = true;
          created.push(halves[1].id);
          return halves;
        });
        return changed ? { ...item, clips } : item;
      });
      if (!changed) return false;
      saveAudioTracks(next, "split-clip");
      setClipSelection(created);
      return true;
    },
    [clipSelection, saveAudioTracks],
  );

  // ---- text boxes -------------------------------------------------------------
  // Full-list saves like the audio tracks (a box is a few hundred bytes).
  const textSeq = useRef(0);
  const saveTextOverlays = useCallback(
    (next: EditorTextOverlay[], kind = "text", coalesce = false) => {
      checkpoint(kind, coalesce);
      setWorkspace((current) => (current ? { ...current, text_overlays: next } : current));
      const seq = ++textSeq.current;
      void track(patchEditorWorkspace(jobId, { text_overlays: next }))
        .then((data) => {
          if (seq === textSeq.current) setWorkspace((current) => (current ? { ...current, text_overlays: data.text_overlays } : data));
        })
        .catch((e) => {
          setError(errorText(e));
          if (seq === textSeq.current) void load();
        });
    },
    [jobId, track, checkpoint, load],
  );

  const selectText = useCallback((id: string | null) => {
    void flushRef.current();
    setTextSelection(id);
    if (!id) return;
    setSelectedRegionId(null);
    setClipSelection([]);
    setSelection((current) => (current.ids.length ? { ids: [], primary: current.primary } : current));
  }, []);

  // Drop a text selection that no longer exists (deleted, undone).
  useEffect(() => {
    if (!workspace || !textSelection) return;
    if (!(workspace.text_overlays ?? []).some((item) => item.id === textSelection)) setTextSelection(null);
  }, [workspace, textSelection]);

  const deleteText = useCallback(() => {
    const current = workspaceRef.current;
    if (!current || !textSelection) return;
    saveTextOverlays((current.text_overlays ?? []).filter((item) => item.id !== textSelection), "delete-text");
    setTextSelection(null);
  }, [textSelection, saveTextOverlays]);

  // ---- selection ------------------------------------------------------------
  const select = useCallback(
    (seg: EditorSegment, options: { jump?: boolean; mode?: SelectMode } = {}) => {
      const { jump = true, mode = "replace" } = options;
      // Save the line being left before the inspector switches to the new one.
      void flushRef.current();
      setSelectedRegionId(null);
      setClipSelection([]);
      setTextSelection(null);
      setSelection((current) => {
        if (mode === "toggle") {
          const has = current.ids.includes(seg.id);
          const ids = has ? current.ids.filter((id) => id !== seg.id) : [...current.ids, seg.id];
          return { ids, primary: has ? (ids[ids.length - 1] ?? null) : seg.id };
        }
        if (mode === "range" && current.primary) {
          const all = workspaceRef.current?.segments ?? [];
          const a = all.findIndex((item) => item.id === current.primary);
          const b = all.findIndex((item) => item.id === seg.id);
          if (a >= 0 && b >= 0) {
            const [from, to] = a < b ? [a, b] : [b, a];
            return { ids: all.slice(from, to + 1).map((item) => item.id), primary: seg.id };
          }
        }
        return { ids: [seg.id], primary: seg.id };
      });
      if (jump && mode === "replace") seekRef.current(seg.start);
    },
    [seekRef],
  );
  const selectIds = useCallback((ids: string[]) => {
    void flushRef.current();
    setClipSelection([]);
    setTextSelection(null);
    setSelection({ ids, primary: ids[ids.length - 1] ?? null });
  }, []);
  const clearSelection = useCallback(() => {
    void flushRef.current();
    setSelection({ ids: [], primary: null });
    setSelectedRegionId(null);
    setClipSelection([]);
    setTextSelection(null);
  }, []);
  const selectRegion = useCallback((id: string | null) => {
    setSelectedRegionId(id);
    if (id) setClipSelection([]);
    if (id) setTextSelection(null);
    if (id) setSelection((current) => (current.ids.length ? { ids: [], primary: current.primary } : current));
  }, []);

  // ---- mutations ------------------------------------------------------------
  const run = useCallback(
    async (label: string, action: () => Promise<EditorWorkspace | void>) => {
      if (label !== "render" && label !== "export") checkpoint(label);
      setBusy(label);
      setError(null);
      try {
        const data = await track(action());
        if (data) {
          setWorkspace(data);
          resync();
        }
        return true;
      } catch (e) {
        setError(errorText(e));
        return false;
      } finally {
        setBusy(null);
      }
    },
    [track, checkpoint, resync],
  );

  const travel = useCallback(
    async (direction: "undo" | "redo") => {
      if (busy || batch || !workspaceRef.current) return;
      await flushAll(); // Pending typing becomes its own step first.
      const stacks = history.current;
      const current = workspaceRef.current;
      if (!current) return;
      const target = direction === "undo" ? stacks.undo.pop()?.state : stacks.redo.pop();
      if (!target) return;
      if (direction === "undo") stacks.redo.push(editableState(current));
      else stacks.undo.push({ state: editableState(current), kind: "redo", at: 0 });
      syncHistorySize();
      setBusy(direction);
      try {
        setWorkspace(await track(putEditorState(jobId, target)));
        resync();
      } catch (e) {
        setError(errorText(e));
      } finally {
        setBusy(null);
      }
    },
    [busy, batch, flushAll, syncHistorySize, track, jobId, resync],
  );

  /** One request for any number of timing changes (multi-clip drag, nudges). */
  const moveSegments = useCallback(
    async (updates: EditorSegmentPatch[]) => {
      if (!updates.length) return;
      if (!(await saveDraft())) return; // Don't let a pending text edit race the new timing.
      checkpoint("retime");
      const byId = new Map(updates.map((update) => [update.id, update]));
      setWorkspace((current) =>
        current
          ? {
              ...current,
              segments: current.segments
                .map((seg) => {
                  const update = byId.get(seg.id);
                  if (!update) return seg;
                  return {
                    ...seg,
                    start: update.start ?? seg.start,
                    end: update.end ?? seg.end,
                    audio_start: update.audio_start ?? seg.audio_start,
                    audio_duration: update.audio_length ?? seg.audio_duration,
                  };
                })
                .sort((a, b) => a.start - b.start),
            }
          : current,
      );
      try {
        setWorkspace(await track(patchEditorWorkspace(jobId, { segments: updates })));
        resync();
      } catch (e) {
        setError(errorText(e));
        void load(); // Put the optimistic geometry back to what the server has.
      }
    },
    [saveDraft, checkpoint, track, jobId, resync, load],
  );

  const retimeVoice = useCallback(
    (id: string, patch: { audio_start?: number; audio_length?: number }) => moveSegments([{ id, ...patch }]),
    [moveSegments],
  );

  /** New cue at t: after the cue playing there, up to 2.5 s, never over the next cue. */
  const focusText = useRef(false);
  const addCueAt = useCallback(
    async (t: number) => {
      const current = workspaceRef.current;
      if (!current) return;
      const covering = current.segments.find((seg) => t >= seg.start && t < seg.end);
      const start = covering ? covering.end : t;
      const next = current.segments.find((seg) => seg.start >= start + 0.001);
      const end = Math.min(start + 2.5, next ? next.start : current.duration);
      if (end - start < 0.3) {
        setError(L("Không còn chỗ trống ở đây — kéo ngắn cue bên cạnh trước rồi thêm.", "No room here — shorten the neighbouring cue first."));
        return;
      }
      if (!(await saveDraft())) return;
      let created: string | null = null;
      const ok = await run("add", async () => {
        const data = await insertEditorSegment(jobId, round3(start), round3(end));
        created = data.created_id;
        return data;
      });
      if (ok && created) {
        focusText.current = true;
        setSelection({ ids: [created], primary: created });
        seekRef.current(start);
      }
    },
    [L, saveDraft, run, jobId, seekRef],
  );
  useEffect(() => {
    if (focusText.current && textRef.current) {
      focusText.current = false;
      textRef.current.focus();
    }
  }, [selectedId, draft]);

  const toggleMute = useCallback(async () => {
    const targets = segments.filter((seg) => selectedIds.has(seg.id));
    if (!targets.length) return;
    // Mixed selection → mute all; otherwise flip.
    const muted = !targets.every((seg) => seg.muted);
    await run("mute", () => patchEditorWorkspace(jobId, { segments: targets.map((seg) => ({ id: seg.id, muted })) }));
  }, [segments, selectedIds, run, jobId]);

  /** Inspector split: at the text caret, or the clause break nearest the middle. */
  const splitSelected = useCallback(async () => {
    if (!selected || !draft) return;
    const area = textRef.current;
    const caret = area && area.selectionStart === area.selectionEnd ? area.selectionStart : null;
    const leading = draft.text.length - draft.text.trimStart().length;
    const atChar = caret === null ? null : caret - leading;
    if (!(await saveDraft())) return;
    await run("split", () => splitEditorSegment(jobId, selected.id, atChar && atChar > 0 ? atChar : null));
  }, [selected, draft, saveDraft, run, jobId]);

  /** Timeline split (S / ⌘B): the selected cue under the playhead, else whichever cue is there. */
  const splitAt = useCallback(
    async (t: number) => {
      if (clipSelection.length) {
        // A selected music clip takes the cut (S / ⌘B), like the selected cue would.
        if (!splitClipsAt(t)) {
          setError(L("Đặt đầu phát lên clip nhạc đang chọn để tách.", "Put the playhead over the selected music clip to split it."));
        }
        return;
      }
      const covering =
        selected && t > selected.start && t < selected.end ? selected : segmentAt(segments, t, "subtitle");
      if (!covering) {
        setError(L("Đặt đầu phát lên một cue phụ đề để tách.", "Put the playhead over a subtitle cue to split it."));
        return;
      }
      // Same 0.1 s edge margin the API enforces: reject locally instead of surfacing a 409
      // (which the toolbar would report as a failed save).
      if (t - covering.start < 0.1 || covering.end - t < 0.1) {
        setError(
          L(
            "Đầu phát quá sát mép cue — cần cách hai mép ít nhất 0,1 giây để tách.",
            "The playhead is too close to the cue edge — keep it at least 0.1 s from both ends to split.",
          ),
        );
        return;
      }
      if (!(await saveDraft())) return;
      await run("split", () => splitEditorSegment(jobId, covering.id, null, round3(t)));
    },
    [selected, segments, saveDraft, run, jobId, L, clipSelection.length, splitClipsAt],
  );

  const mergeSelected = useCallback(async () => {
    if (!selected || !(await saveDraft())) return;
    await run("merge", () => mergeEditorSegmentWithNext(jobId, selected.id));
  }, [selected, saveDraft, run, jobId]);

  const deleteSelection = useCallback(async () => {
    if (busy) return;
    const ids = segments.filter((seg) => selectedIds.has(seg.id)).map((seg) => seg.id);
    if (!ids.length) return;
    // Keep the cursor in place: select the cue that slides into this position.
    const lastIndex = segments.findIndex((seg) => seg.id === ids[ids.length - 1]);
    const firstIndex = segments.findIndex((seg) => seg.id === ids[0]);
    const neighbour = segments[lastIndex + 1] ?? segments[firstIndex - 1] ?? null;
    const ok = await run("delete", () =>
      ids.length === 1 ? deleteEditorSegment(jobId, ids[0]) : deleteEditorSegments(jobId, ids),
    );
    if (ok) setSelection({ ids: neighbour ? [neighbour.id] : [], primary: neighbour?.id ?? null });
  }, [busy, segments, selectedIds, run, jobId]);

  const regenerateAudio = useCallback(async () => {
    if (!selected || !(await saveDraft())) return;
    const number = selectedIndex + 1;
    const before = selected.audio_duration;
    setNotice(null);
    let fresh: EditorSegment | undefined;
    const ok = await run("tts", async () => {
      const data = await regenerateEditorSegmentAudio(jobId, selected.id);
      fresh = data.segments.find((seg) => seg.id === selected.id);
      return data;
    });
    if (!ok || !fresh) return;
    const slot = fresh.end - fresh.start;
    const tail = fresh.issues.includes("overrun")
      ? L(
          ` Vẫn tràn sang câu sau (audio ${fresh.audio_duration.toFixed(2)}s, khung ${slot.toFixed(2)}s) — rút gọn câu hoặc kéo dài khung rồi tạo lại.`,
          ` Still overruns the next cue (audio ${fresh.audio_duration.toFixed(2)}s, slot ${slot.toFixed(2)}s) — shorten the line or widen the slot.`,
        )
      : "";
    setNotice(
      L(
        `Đã tạo lại audio câu #${number}: ${before.toFixed(2)}s → ${fresh.audio_duration.toFixed(2)}s.${tail}`,
        `Regenerated audio for line #${number}: ${before.toFixed(2)}s → ${fresh.audio_duration.toFixed(2)}s.${tail}`,
      ),
    );
  }, [selected, selectedIndex, saveDraft, run, jobId, L]);

  const swapAudio = useCallback(async () => {
    if (!selected) return;
    await run("swap", () => swapEditorSegmentAudio(jobId, selected.id));
  }, [selected, run, jobId]);

  const retranslate = useCallback(async () => {
    if (!selected) return;
    await run("translate", () => retranslateEditorSegment(jobId, selected.id));
  }, [selected, run, jobId]);

  /** Re-voices every edited / unvoiced line (or only the selected ones), one request each. */
  const regenerateStale = useCallback(
    async (onlySelected = false) => {
      const targets = segments.filter(
        (seg) =>
          (!onlySelected || selectedIds.has(seg.id)) &&
          (onlySelected || seg.issues.includes("stale_audio") || seg.issues.includes("no_audio")),
      );
      if (!targets.length) return;
      if (!(await saveDraft())) return;
      checkpoint("batch-tts");
      batchCancel.current = false;
      setBatch({ done: 0, total: targets.length });
      setError(null);
      for (let i = 0; i < targets.length; i++) {
        if (batchCancel.current) break;
        try {
          setWorkspace(await track(regenerateEditorSegmentAudio(jobId, targets[i].id)));
        } catch (e) {
          setError(`${targets[i].id}: ${errorText(e)}`);
          break;
        }
        setBatch({ done: i + 1, total: targets.length });
      }
      setBatch(null);
    },
    [segments, selectedIds, saveDraft, checkpoint, track, jobId],
  );
  const cancelBatch = useCallback(() => {
    batchCancel.current = true;
  }, []);

  const exportFiles = useCallback(async () => {
    const ok = await run("export", async () => {
      const result = await exportEditorWorkspace(jobId);
      setNotice(L(`Đã xuất SRT, ASS và audio chunks vào: ${result.folder}`, `Exported SRT, ASS and audio chunks to: ${result.folder}`));
    });
    if (!ok) setNotice(null);
  }, [run, jobId, L]);

  const startRender = useCallback(async () => {
    if (!(await flushAll())) return;
    await run("render", () => renderEditorWorkspace(jobId));
  }, [flushAll, run, jobId]);

  const onDraftRestored = useCallback(
    (data: EditorWorkspace) => {
      checkpoint("restore");
      setWorkspace(data);
      resync();
    },
    [checkpoint, resync],
  );

  const nextIssue = useCallback(
    (direction: 1 | -1) => {
      if (!segments.length) return;
      const from = selectedIndex < 0 ? (direction === 1 ? -1 : segments.length) : selectedIndex;
      for (let i = from + direction; i >= 0 && i < segments.length; i += direction) {
        if (segments[i].issues.length) {
          select(segments[i]);
          return;
        }
      }
    },
    [segments, selectedIndex, select],
  );

  return {
    workspace,
    segments,
    error,
    setError,
    notice,
    setNotice,
    busy,
    batch,
    saveState,
    historySize,
    waveform,
    // selection
    selectedId,
    selectedIds,
    selected,
    selectedIndex,
    selectedRegionId,
    select,
    selectIds,
    clearSelection,
    selectRegion,
    // inspector draft
    draft,
    setDraft,
    dirty,
    textRef,
    // actions
    load,
    travel,
    saveDraft,
    flushAll,
    saveStyle,
    saveMaxLines,
    saveAudio,
    saveBlur,
    saveRegions,
    saveAudioTracks,
    clipSelection,
    selectClips,
    deleteClips,
    splitClipsAt,
    saveTextOverlays,
    textSelection,
    selectText,
    deleteText,
    moveSegments,
    retimeVoice,
    addCueAt,
    toggleMute,
    splitSelected,
    splitAt,
    mergeSelected,
    deleteSelection,
    regenerateAudio,
    regenerateStale,
    cancelBatch,
    swapAudio,
    retranslate,
    exportFiles,
    startRender,
    onDraftRestored,
    nextIssue,
    // external changes (auto review): record an undo step first, reload after
    checkpoint,
    resync,
  };
}

export type EditorController = ReturnType<typeof useEditor>;
