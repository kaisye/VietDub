import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { DubPlayer } from "./EditorDubPlayer";
import {
  deleteEditorSegment,
  exportEditorWorkspace,
  deleteEditorDraft,
  getEditorWaveform,
  insertEditorSegment,
  putEditorState,
  type EditorEditableState,
  listEditorDrafts,
  restoreEditorDraft,
  saveEditorDraft,
  type EditorDraft,
  getEditorWorkspace,
  mergeEditorSegmentWithNext,
  outputUrl,
  patchEditorWorkspace,
  regenerateEditorSegmentAudio,
  renderEditorWorkspace,
  retranslateEditorSegment,
  splitEditorSegment,
  swapEditorSegmentAudio,
} from "../api";
import type { EditorAudio, EditorBlur, EditorIssue, EditorRegion, EditorStyle, EditorSegment, EditorWorkspace } from "../types";
import { useT } from "../i18n";
import StylePanel, { hexAlpha } from "./EditorStyle";
import OriginalTrack, { originalVolumeAt, type Waveform } from "./EditorOriginalTrack";

type Filter = "all" | "issues" | EditorIssue;

const ISSUE_LABEL: Record<EditorIssue, [string, string]> = {
  overrun: ["Audio tràn", "Audio overrun"],
  stale_audio: ["Audio cũ", "Stale audio"],
  no_audio: ["Thiếu audio", "No audio"],
  untranslated: ["Chưa dịch", "Untranslated"],
  empty: ["Câu rỗng", "Empty"],
};

function formatTime(seconds: number, precise = true): string {
  const safe = Math.max(0, seconds);
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = safe % 60;
  const sec = precise ? s.toFixed(2).padStart(5, "0") : String(Math.floor(s)).padStart(2, "0");
  return `${h > 0 ? `${h}:` : ""}${String(m).padStart(2, "0")}:${sec}`;
}

function segmentAt(segments: EditorSegment[], time: number, key: "subtitle" | "audio"): EditorSegment | null {
  // Segments are sorted by start; binary-search the last one starting before `time`.
  let lo = 0;
  let hi = segments.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const start = key === "audio" ? segments[mid].audio_start : segments[mid].start;
    if (start <= time) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  for (let i = found; i >= Math.max(0, found - 3); i--) {
    const seg = segments[i];
    if (key === "audio") {
      if (seg.audio_url && !seg.muted && time < seg.audio_start + seg.audio_duration) return seg;
    } else if (time < seg.end) {
      return seg;
    }
  }
  return null;
}

export default function EditorScreen({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const { lang } = useT();
  const L = useCallback((vi: string, en: string) => (lang === "vi" ? vi : en), [lang]);

  const [workspace, setWorkspace] = useState<EditorWorkspace | null>(null);
  // The dub / source-audio switches are saved, so the render uses the same mix as the preview.
  const dubOn = workspace?.audio?.dubbed_enabled ?? true;
  const originalOn = workspace?.audio?.original_enabled ?? true;
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ text: string; start: string; end: string } | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(24);
  const [panels, setPanels] = useState<Panels>(loadPanels);
  const [waveform, setWaveform] = useState<Waveform | null>(null);
  const [selectedRegionId, setSelectedRegionId] = useState<string | null>(null);
  const togglePanel = useCallback((key: keyof Panels) => {
    setPanels((current) => {
      const next = { ...current, [key]: !current[key] };
      try {
        localStorage.setItem(PANELS_KEY, JSON.stringify(next));
      } catch {
        /* per-viewer convenience only */
      }
      return next;
    });
  }, []);
  const [batch, setBatch] = useState<{ done: number; total: number } | null>(null);
  // Every write goes through `track`, so the header can say whether edits reached disk.
  const [saveState, setSaveState] = useState<{ pending: number; savedAt: Date | null; failed: string | null }>({
    pending: 0,
    savedAt: null,
    failed: null,
  });
  const track = useCallback(<T,>(promise: Promise<T>): Promise<T> => {
    setSaveState((state) => ({ ...state, pending: state.pending + 1 }));
    return promise.then(
      (value) => {
        setSaveState((state) => ({ pending: state.pending - 1, savedAt: new Date(), failed: null }));
        return value;
      },
      (error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        setSaveState((state) => ({ ...state, pending: state.pending - 1, failed: message }));
        throw error;
      },
    );
  }, []);
  // Bumped after server-side text changes (translate, split, restore…) to reload the draft.
  const [draftSync, setDraftSync] = useState(0);

  // ---- undo / redo ----------------------------------------------------------
  // Each mutation first records the state it is about to change. Workspace updates are
  // immutable, so a snapshot only holds references.
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
  const batchCancel = useRef(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const dubPlayer = useRef<DubPlayer | null>(null);
  if (!dubPlayer.current) dubPlayer.current = new DubPlayer();
  const previewRef = useRef<HTMLAudioElement | null>(null);
  const textRef = useRef<HTMLTextAreaElement | null>(null);
  const stopAt = useRef<number | null>(null);

  const load = useCallback(
    async (rebuild = false) => {
      try {
        setError(null);
        const data = await getEditorWorkspace(jobId, rebuild);
        setWorkspace(data);
        setSelectedId((current) => current ?? data.segments[0]?.id ?? null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
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

  const segments = workspace?.segments ?? [];
  const selected = useMemo(() => segments.find((seg) => seg.id === selectedId) ?? null, [segments, selectedId]);
  const selectedIndex = selected ? segments.indexOf(selected) : -1;

  useEffect(() => {
    setDraft(
      selected
        ? { text: selected.text, start: selected.start.toFixed(2), end: selected.end.toFixed(2) }
        : null,
    );
    // Not on every server echo: an autosave answer must not overwrite what is typed meanwhile.
  }, [selected?.id, draftSync]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty =
    !!selected &&
    !!draft &&
    (draft.text.trim() !== selected.text ||
      Math.abs(Number(draft.start) - selected.start) > 0.004 ||
      Math.abs(Number(draft.end) - selected.end) > 0.004);

  const issueCounts = useMemo(() => {
    const counts: Partial<Record<EditorIssue, number>> = {};
    let any = 0;
    for (const seg of segments) {
      if (seg.issues.length) any++;
      for (const issue of seg.issues) counts[issue] = (counts[issue] ?? 0) + 1;
    }
    return { any, counts };
  }, [segments]);

  const visibleSegments = useMemo(() => {
    const q = query.trim().toLowerCase();
    return segments.filter((seg) => {
      if (filter === "issues" && !seg.issues.length) return false;
      if (filter !== "all" && filter !== "issues" && !seg.issues.includes(filter)) return false;
      if (q && !seg.text.toLowerCase().includes(q) && !seg.source_text.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [segments, filter, query]);

  // ---- playback -----------------------------------------------------------
  const syncDub = useCallback(
    (t: number, isPlaying: boolean) => {
      const seg = isPlaying && dubOn ? segmentAt(segments, t, "audio") : null;
      dubPlayer.current?.sync(segments, seg, t, isPlaying && dubOn);
    },
    [segments, dubOn],
  );

  useEffect(() => {
    if (!playing) {
      syncDub(videoRef.current?.currentTime ?? 0, false);
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
        syncDub(t, !video.paused);
        if (now - lastUi > 80) {
          lastUi = now;
          setTime(t);
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, syncDub]);

  // The browser caps element volume at 100%; boosts above that are heard only in the render.
  const originalVolume = workspace?.audio?.original_volume ?? 100;
  const dubbedVolume = workspace?.audio?.dubbed_volume ?? 100;
  const regions = workspace?.original_regions;
  // Rounded so the effect only re-runs when the audible level changes.
  const effectiveOriginal = Math.round(originalVolumeAt(regions ?? [], originalVolume, time));
  useEffect(() => {
    if (!videoRef.current) return;
    videoRef.current.muted = !originalOn;
    videoRef.current.volume = Math.min(1, effectiveOriginal / 100);
  }, [originalOn, effectiveOriginal, workspace?.video_url]);

  const saveRegions = useCallback(
    (next: EditorRegion[], selectId?: string) => {
      checkpoint("regions");
      setWorkspace((current) => (current ? { ...current, original_regions: next } : current));
      if (selectId !== undefined) setSelectedRegionId(selectId);
      void track(patchEditorWorkspace(jobId, { original_regions: next }))
        .then((data) => setWorkspace((current) => (current ? { ...current, original_regions: data.original_regions } : data)))
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    },
    [jobId, track, checkpoint],
  );
  const selectedRegion = regions?.find((region) => region.id === selectedRegionId) ?? null;
  useEffect(() => {
    dubPlayer.current?.setVolume(dubbedVolume);
    if (previewRef.current) previewRef.current.volume = Math.min(1, dubbedVolume / 100);
  }, [dubbedVolume]);

  // Web Audio may only start after a user gesture; any click or key in the editor unlocks it.
  useEffect(() => {
    const player = dubPlayer.current;
    const unlock = () => player?.unlock();
    window.addEventListener("pointerdown", unlock, true);
    window.addEventListener("keydown", unlock, true);
    return () => {
      window.removeEventListener("pointerdown", unlock, true);
      window.removeEventListener("keydown", unlock, true);
      player?.dispose();
    };
  }, []);

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
      setError(e instanceof Error ? e.message : String(e));
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

  const saveMaxLines = useCallback(
    (max_lines: number) => {
      checkpoint("max_lines");
      void track(patchEditorWorkspace(jobId, { max_lines }))
        .then(setWorkspace)
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    },
    [jobId],
  );

  const saveAudio = useCallback(
    (audio: Partial<EditorAudio>) => {
      checkpoint("audio");
      setWorkspace((current) => (current ? { ...current, audio: { ...current.audio, ...audio } } : current));
      void track(patchEditorWorkspace(jobId, { audio }))
        .then((data) => setWorkspace((current) => (current ? { ...current, audio: data.audio } : data)))
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    },
    [jobId],
  );

  const seek = useCallback((t: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, t);
    setTime(video.currentTime);
  }, []);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    stopAt.current = null;
    if (video.paused) void video.play();
    else video.pause();
  }, []);

  const playSegment = useCallback(
    (seg: EditorSegment) => {
      const video = videoRef.current;
      if (!video) return;
      seek(Math.min(seg.start, seg.audio_start));
      stopAt.current = Math.max(seg.end, seg.audio_start + seg.audio_duration) + 0.15;
      void video.play();
    },
    [seek],
  );

  const select = useCallback(
    (seg: EditorSegment, jump = true) => {
      // Save the line being left before the draft switches to the new one.
      void flushRef.current();
      setSelectedId(seg.id);
      if (jump) seek(seg.start);
    },
    [seek],
  );

  // ---- mutations ----------------------------------------------------------
  const run = useCallback(
    async (label: string, action: () => Promise<EditorWorkspace | void>) => {
      if (label !== "render" && label !== "export") checkpoint(label);
      setBusy(label);
      setError(null);
      try {
        const data = await track(action());
        if (data) {
          setWorkspace(data);
          setDraftSync((value) => value + 1);
        }
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return false;
      } finally {
        setBusy(null);
      }
    },
    [track, checkpoint],
  );

  // Saves the line being edited. Reads refs so it can run on segment switch or unmount.
  const draftState = useRef({ selected, draft, dirty });
  draftState.current = { selected, draft, dirty };
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
      const data = await track(
        patchEditorWorkspace(jobId, { segments: [{ id: segment.id, text: current.text, start, end }] }),
      );
      setWorkspace(data);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    }
  }, [track, checkpoint, jobId, L]);

  useEffect(() => {
    if (!dirty) return;
    const timer = window.setTimeout(() => void saveDraft(), 700);
    return () => window.clearTimeout(timer);
  }, [draft, dirty, saveDraft]);

  const flushAll = useCallback(async () => {
    const [ok] = await Promise.all([saveDraft(), flushStyle()]);
    return ok;
  }, [saveDraft, flushStyle]);
  const flushRef = useRef(flushAll);
  flushRef.current = flushAll;
  useEffect(() => () => void flushRef.current(), []);

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
        const data = await track(putEditorState(jobId, target));
        setWorkspace(data);
        setDraftSync((value) => value + 1);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [busy, batch, flushAll, syncHistorySize, track, jobId],
  );

  const goBack = useCallback(async () => {
    if (await flushAll()) onBack();
  }, [flushAll, onBack]);

  const previewTake = useCallback((url: string | null | undefined) => {
    const player = previewRef.current;
    if (!player || !url) return;
    videoRef.current?.pause();
    player.src = outputUrl(url) ?? "";
    void player.play().catch(() => undefined);
  }, []);

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

  const retimeSegment = useCallback(
    async (id: string, start: number, end: number) => {
      if (!(await saveDraft())) return; // Don't let a pending text edit race the new timing.
      checkpoint("retime");
      setWorkspace((current) =>
        current
          ? {
              ...current,
              segments: current.segments.map((seg) => (seg.id === id ? { ...seg, start, end } : seg)),
            }
          : current,
      );
      try {
        setWorkspace(await track(patchEditorWorkspace(jobId, { segments: [{ id, start, end }] })));
        setDraftSync((value) => value + 1);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [saveDraft, checkpoint, track, jobId],
  );

  const retimeVoice = useCallback(
    async (id: string, patch: { audio_start?: number; audio_length?: number }) => {
      checkpoint("voice");
      setWorkspace((current) =>
        current
          ? {
              ...current,
              segments: current.segments.map((seg) =>
                seg.id === id
                  ? {
                      ...seg,
                      audio_start: patch.audio_start ?? seg.audio_start,
                      audio_duration: patch.audio_length ?? seg.audio_duration,
                    }
                  : seg,
              ),
            }
          : current,
      );
      try {
        setWorkspace(await track(patchEditorWorkspace(jobId, { segments: [{ id, ...patch }] })));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        void load(); // Put the optimistic geometry back to what the server has.
      }
    },
    [checkpoint, track, jobId, load],
  );

  // New cue at t: after the cue playing there, up to 2.5 s, never over the next cue.
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
        setError(
          L(
            "Không còn chỗ trống ở đây — kéo ngắn cue bên cạnh trước rồi thêm.",
            "No room here — shorten the neighbouring cue first.",
          ),
        );
        return;
      }
      if (!(await saveDraft())) return;
      let created: string | null = null;
      const ok = await run("add", async () => {
        const data = await insertEditorSegment(jobId, Math.round(start * 1000) / 1000, Math.round(end * 1000) / 1000);
        created = data.created_id;
        return data;
      });
      if (ok && created) {
        focusText.current = true;
        setFilter("all");
        setSelectedId(created);
        seek(start);
      }
    },
    [L, saveDraft, run, jobId, seek],
  );
  useEffect(() => {
    if (focusText.current && textRef.current) {
      focusText.current = false;
      textRef.current.focus();
    }
  }, [selectedId, draft]);

  const toggleMute = useCallback(async () => {
    if (!selected) return;
    await run("mute", () => patchEditorWorkspace(jobId, { segments: [{ id: selected.id, muted: !selected.muted }] }));
  }, [selected, run, jobId]);

  const splitSelected = useCallback(async () => {
    if (!selected || !draft) return;
    const area = textRef.current;
    // Caret position in the saved (trimmed) text; null lets the server pick the clause break nearest the middle.
    const caret = area && area.selectionStart === area.selectionEnd ? area.selectionStart : null;
    const leading = draft.text.length - draft.text.trimStart().length;
    const atChar = caret === null ? null : caret - leading;
    if (!(await saveDraft())) return;
    await run("split", () => splitEditorSegment(jobId, selected.id, atChar && atChar > 0 ? atChar : null));
  }, [selected, draft, saveDraft, run, jobId]);

  const mergeSelected = useCallback(async () => {
    if (!selected || !(await saveDraft())) return;
    await run("merge", () => mergeEditorSegmentWithNext(jobId, selected.id));
  }, [selected, saveDraft, run, jobId]);

  const deleteSelected = useCallback(async () => {
    if (!selected || busy) return;
    // Keep the cursor in place: select the cue that slides into this position.
    const neighbour = segments[selectedIndex + 1] ?? segments[selectedIndex - 1] ?? null;
    const ok = await run("delete", () => deleteEditorSegment(jobId, selected.id));
    if (ok) setSelectedId(neighbour?.id ?? null);
  }, [selected, busy, segments, selectedIndex, run, jobId]);

  const regenerateAllStale = useCallback(async () => {
    const targets = segments.filter((seg) => seg.issues.includes("stale_audio") || seg.issues.includes("no_audio"));
    if (!targets.length) return;
    checkpoint("batch-tts");
    batchCancel.current = false;
    setBatch({ done: 0, total: targets.length });
    setError(null);
    for (let i = 0; i < targets.length; i++) {
      if (batchCancel.current) break;
      try {
        setWorkspace(await regenerateEditorSegmentAudio(jobId, targets[i].id));
      } catch (e) {
        setError(`${targets[i].id}: ${e instanceof Error ? e.message : String(e)}`);
        break;
      }
      setBatch({ done: i + 1, total: targets.length });
    }
    setBatch(null);
  }, [segments, jobId]);

  const saveBlur = useCallback(
    (blur: Partial<EditorBlur>) => {
      checkpoint("blur");
      setWorkspace((current) => (current ? { ...current, blur: { ...current.blur, ...blur } } : current));
      void track(patchEditorWorkspace(jobId, { blur }))
        .then((data) => setWorkspace((current) => (current ? { ...current, blur: data.blur } : data)))
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    },
    [jobId],
  );

  const exportFiles = useCallback(async () => {
    const ok = await run("export", async () => {
      const result = await exportEditorWorkspace(jobId);
      setNotice(L(`Đã xuất SRT, ASS và audio chunks vào: ${result.folder}`, `Exported SRT, ASS and audio chunks to: ${result.folder}`));
    });
    if (!ok) setNotice(null);
  }, [run, jobId, L]);

  const startRender = useCallback(async () => {
    if (!(await saveDraft())) return;
    await run("render", () => renderEditorWorkspace(jobId));
  }, [saveDraft, run, jobId]);

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

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = !!target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT");
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void saveDraft();
        return;
      }
      if (typing) return; // Text fields keep their own ⌘Z.
      const mod = event.metaKey || event.ctrlKey;
      if (mod && (event.key.toLowerCase() === "y" || (event.key.toLowerCase() === "z" && event.shiftKey))) {
        event.preventDefault();
        void travel("redo");
        return;
      }
      if (mod && event.key.toLowerCase() === "z") {
        event.preventDefault();
        void travel("undo");
        return;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && selectedRegion && regions) {
        event.preventDefault();
        saveRegions(regions.filter((region) => region.id !== selectedRegion.id));
        setSelectedRegionId(null);
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        void deleteSelected();
      } else if (event.key === " ") {
        event.preventDefault();
        togglePlay();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = segments[selectedIndex + (event.key === "ArrowDown" ? 1 : -1)];
        if (next) select(next);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saveDraft, togglePlay, segments, selectedIndex, select, deleteSelected, selectedRegion, regions, saveRegions, travel]);

  if (!workspace) {
    return (
      <div className="page">
        {error ? (
          <>
            <div className="banner err">{error}</div>
            <button className="btn" onClick={onBack}>← {L("Quay lại", "Back")}</button>
          </>
        ) : (
          <p className="muted">{L("Đang mở workspace…", "Opening workspace…")}</p>
        )}
      </div>
    );
  }

  const subtitleNow = segmentAt(segments, time, "subtitle");
  const staleCount = (issueCounts.counts.stale_audio ?? 0) + (issueCounts.counts.no_audio ?? 0);
  const render = workspace.render;

  return (
    <div className="editor">
      <div className="editor-bar">
        <button className="btn sm" onClick={() => void goBack()}>← {L("Quay lại", "Back")}</button>
        <div className="editor-title">
          <strong>{L("Editor", "Editor")}</strong>
          <span className="muted mono">{workspace.title || workspace.job_id}</span>
        </div>
        <span className="spacer" />
        <div className="editor-history">
          <button
            className="btn sm"
            disabled={!historySize.undo || !!busy || !!batch}
            title={L("Hoàn tác (⌘Z / Ctrl+Z)", "Undo (⌘Z / Ctrl+Z)")}
            onClick={() => void travel("undo")}
          >
            ↶
          </button>
          <button
            className="btn sm"
            disabled={!historySize.redo || !!busy || !!batch}
            title={L("Làm lại (⌘⇧Z / Ctrl+Y)", "Redo (⌘⇧Z / Ctrl+Y)")}
            onClick={() => void travel("redo")}
          >
            ↷
          </button>
        </div>
        <SaveStatus state={saveState} dirty={dirty} L={L} />
        <DraftsMenu
          jobId={jobId}
          beforeSave={flushAll}
          onRestored={(data) => {
            checkpoint("restore");
            setWorkspace(data);
            setDraftSync((value) => value + 1);
          }}
          L={L}
        />
        {render.status === "running" ? (
          <span className="pill run">{L("Đang render…", "Rendering…")}</span>
        ) : render.status === "done" ? (
          <span className="pill ready">{L("Render xong — xem ở màn hình kết quả", "Render done — see result screen")}</span>
        ) : render.status === "failed" ? (
          <span className="pill failed" title={render.error}>{L("Render lỗi", "Render failed")}</span>
        ) : null}
        <button className="btn sm" disabled={!!busy} onClick={() => void exportFiles()}>
          {busy === "export" ? L("Đang xuất…", "Exporting…") : L("Xuất SRT / ASS / audio", "Export SRT / ASS / audio")}
        </button>
        <button
          className="btn sm primary"
          disabled={!!busy || !!batch || render.status === "running"}
          onClick={() => void startRender()}
        >
          {L("Render video", "Render video")}
        </button>
      </div>

      {error ? <div className="banner err editor-banner">{error}</div> : null}
      {notice ? (
        <div className="banner ok editor-banner" onClick={() => setNotice(null)}>{notice}</div>
      ) : null}
      {render.status === "failed" && render.error ? (
        <div className="banner err editor-banner">{L("Render lỗi: ", "Render failed: ")}{render.error}</div>
      ) : null}

      <div className="editor-main">
        <div className="editor-stage">
          <div
            className="editor-video"
            style={{
              aspectRatio: `${workspace.width || 16} / ${workspace.height || 9}`,
              // Cap by height without breaking the aspect ratio, so the blur box maps 1:1 onto the frame.
              // Collapsed panels hand their height to the video.
              width: `min(100%, ${((panels.blur || panels.style ? 52 : 70) * (workspace.width || 16)) / (workspace.height || 9)}vh)`,
            }}
          >
            <video
              ref={videoRef}
              src={outputUrl(workspace.video_url) ?? undefined}
              preload="auto"
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onSeeked={(e) => setTime(e.currentTarget.currentTime)}
              onClick={togglePlay}
            />
            <BlurBox blur={workspace.blur} onChange={saveBlur} />
            <SubtitlePreview
              workspace={workspace}
              segment={subtitleNow}
              time={time}
              onMove={(position_y) => saveStyle({ position_y })}
            />
            <audio ref={previewRef} preload="none" />
          </div>
          <div className="editor-transport">
            <button className="btn sm" onClick={togglePlay}>{playing ? "❚❚" : "▶"}</button>
            <button
              className="btn sm"
              disabled={!!busy}
              title={L("Thêm cue phụ đề mới tại vị trí đang phát (hoặc nhấp đúp vào chỗ trống trên track Phụ đề)", "Add a cue at the playhead (or double-click an empty spot on the Subtitles track)")}
              onClick={() => void addCueAt(videoRef.current?.currentTime ?? time)}
            >
              + {L("Thêm cue", "Add cue")}
            </button>
            <span className="mono">{formatTime(time)} / {formatTime(workspace.duration, false)}</span>
            <span className="spacer" />
            <label className="editor-toggle">
              <input type="checkbox" checked={dubOn} onChange={(e) => saveAudio({ dubbed_enabled: e.target.checked })} />
              {L("Giọng lồng", "Dub voice")}
            </label>
            <VolumeSlider
              value={dubbedVolume}
              title={L("Âm lượng giọng lồng khi render", "Dub voice volume in the render")}
              onCommit={(dubbed_volume) => saveAudio({ dubbed_volume })}
            />
            <label className="editor-toggle">
              <input type="checkbox" checked={originalOn} onChange={(e) => saveAudio({ original_enabled: e.target.checked })} />
              {L("Âm gốc", "Original audio")}
            </label>
            <VolumeSlider
              value={originalVolume}
              title={L(
                "Âm lượng âm thanh gốc khi render (xem trước tối đa 100%)",
                "Original audio volume in the render (preview caps at 100%)",
              )}
              onCommit={(original_volume) => saveAudio({ original_volume })}
            />
          </div>
          <Collapsible
            title={L("Làm mờ sub gốc", "Blur hard subs")}
            summary={
              workspace.blur.enabled
                ? L(`Bật · độ mờ ${workspace.blur.strength}`, `On · strength ${workspace.blur.strength}`)
                : L("Tắt", "Off")
            }
            open={panels.blur}
            onToggle={() => togglePanel("blur")}
          >
            <BlurPanel blur={workspace.blur} onChange={saveBlur} L={L} />
          </Collapsible>
          <Collapsible
            title={L("Kiểu phụ đề", "Subtitle style")}
            summary={`${workspace.style.font_name} ${workspace.style.font_size}${workspace.style.bold ? L(" đậm", " bold") : ""} · ${
              !workspace.style.box_opacity
                ? L("không nền", "no box")
                : workspace.style.box_style === "panel"
                  ? L("khung bo góc", "rounded panel")
                  : L("nền liền", "classic box")
            }${workspace.style.outline && workspace.style.box_style !== "classic" ? L(" · có viền", " · stroke") : ""}`}
            open={panels.style}
            onToggle={() => togglePanel("style")}
          >
          <StylePanel
            style={workspace.style}
            blur={workspace.blur}
            maxLines={workspace.max_lines}
            onChange={saveStyle}
            onMaxLines={saveMaxLines}
            L={L}
          />
          </Collapsible>
        </div>

        <div className="editor-side">
          <div className="editor-filters">
            <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
              <option value="all">{L("Tất cả", "All")} ({segments.length})</option>
              <option value="issues">{L("Có vấn đề", "With issues")} ({issueCounts.any})</option>
              {(Object.keys(ISSUE_LABEL) as EditorIssue[]).map((issue) =>
                issueCounts.counts[issue] ? (
                  <option key={issue} value={issue}>
                    {ISSUE_LABEL[issue][lang === "vi" ? 0 : 1]} ({issueCounts.counts[issue]})
                  </option>
                ) : null,
              )}
            </select>
            <input
              placeholder={L("Tìm câu…", "Search…")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <SegmentList
            segments={visibleSegments}
            selectedId={selectedId}
            activeId={subtitleNow?.id ?? null}
            lang={lang}
            onSelect={select}
          />

          {selected && draft ? (
            <div className="editor-inspector">
              <div className="editor-inspector-head">
                <strong>#{selectedIndex + 1}</strong>
                {selected.issues.map((issue) => (
                  <span key={issue} className={`editor-issue ${issue}`}>{ISSUE_LABEL[issue][lang === "vi" ? 0 : 1]}</span>
                ))}
                <span className="spacer" />
                <button className="btn-link" onClick={() => nextIssue(-1)}>↑ {L("lỗi trước", "prev issue")}</button>
                <button className="btn-link" onClick={() => nextIssue(1)}>{L("lỗi sau", "next issue")} ↓</button>
              </div>
              {selected.source_text ? <p className="editor-source">{selected.source_text}</p> : null}
              <textarea
                ref={textRef}
                rows={3}
                value={draft.text}
                onChange={(e) => setDraft({ ...draft, text: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    void saveDraft();
                  }
                }}
              />
              <div className="editor-timing">
                <TimeField label={L("Bắt đầu", "Start")} value={draft.start} onChange={(start) => setDraft({ ...draft, start })} />
                <TimeField label={L("Kết thúc", "End")} value={draft.end} onChange={(end) => setDraft({ ...draft, end })} />
                <span className="muted">
                  {L("Audio", "Audio")} {selected.audio_duration.toFixed(2)}s / {L("khung", "slot")}{" "}
                  {(selected.end - selected.start).toFixed(2)}s
                </span>
              </div>
              <div className="editor-takes">
                {selected.audio_url ? (
                  <div className="editor-take">
                    <span>
                      {L("Giọng", "Voice")} {formatTime(selected.audio_start)} → {formatTime(selected.audio_start + selected.audio_duration)}
                      {selected.voice_slot && selected.audio_source_duration
                        ? L(
                            ` · tốc độ ×${(selected.audio_source_duration / selected.audio_duration).toFixed(2)}`,
                            ` · speed ×${(selected.audio_source_duration / selected.audio_duration).toFixed(2)}`,
                          )
                        : ""}
                    </span>
                    {Math.abs(selected.audio_start - selected.start) > 0.01 ? (
                      <button className="btn-link" onClick={() => void retimeVoice(selected.id, { audio_start: selected.start })}>
                        {L("Đưa giọng về đầu câu", "Align voice to cue")}
                      </button>
                    ) : null}
                    {selected.voice_slot && selected.audio_source_duration ? (
                      <button
                        className="btn-link"
                        onClick={() => void retimeVoice(selected.id, { audio_length: selected.audio_source_duration })}
                      >
                        {L("Về tốc độ gốc", "Natural speed")}
                      </button>
                    ) : null}
                  </div>
                ) : null}
                <div className="editor-take">
                  <span>
                    <strong>{L("Audio đang dùng", "Current audio")}</strong>{" "}
                    {selected.audio_url
                      ? selected.audio_generated_at && selected.audio_url.includes("/editor/")
                        ? L(
                            `· tạo lại lúc ${new Date(selected.audio_generated_at).toLocaleTimeString()}`,
                            `· regenerated at ${new Date(selected.audio_generated_at).toLocaleTimeString()}`,
                          )
                        : L("· bản gốc từ pipeline", "· original pipeline take")
                      : L("· chưa có", "· none")}
                    {selected.audio_url ? ` · ${selected.audio_duration.toFixed(2)}s` : ""}
                  </span>
                  {selected.audio_url ? (
                    <button className="btn sm" onClick={() => previewTake(selected.audio_url)}>▶</button>
                  ) : null}
                </div>
                {selected.previous_audio_url ? (
                  <div className="editor-take previous">
                    <span>
                      {L("Bản trước", "Previous take")} · {(selected.previous_audio_duration ?? 0).toFixed(2)}s
                    </span>
                    <button className="btn sm" onClick={() => previewTake(selected.previous_audio_url)}>▶</button>
                    <button className="btn sm" disabled={!!busy} onClick={() => void swapAudio()}>
                      {L("Dùng bản này", "Use this")}
                    </button>
                  </div>
                ) : null}
              </div>
              <div className="editor-actions">
                <button className="btn sm" onClick={() => playSegment(selected)}>▶ {L("Nghe", "Play")}</button>
                <button className="btn sm primary" disabled={!dirty || !!busy} onClick={() => void saveDraft()}>
                  {busy === "save" ? L("Đang lưu…", "Saving…") : L("Lưu", "Save")}
                </button>
                <button className="btn sm" disabled={!!busy || !selected.source_text} onClick={() => void retranslate()}>
                  {busy === "translate" ? L("Đang dịch…", "Translating…") : L("Dịch lại", "Retranslate")}
                </button>
                <button className="btn sm" disabled={!!busy || !!batch} onClick={() => void regenerateAudio()}>
                  {busy === "tts"
                    ? L("Đang tạo…", "Generating…")
                    : selected.audio_url
                      ? L("Tạo lại audio", "Regenerate audio")
                      : L("Tạo audio", "Generate audio")}
                </button>
                <button className="btn sm" disabled={!!busy} onClick={() => void toggleMute()}>
                  {selected.muted ? L("Bật tiếng", "Unmute") : L("Tắt tiếng", "Mute")}
                </button>
                <button
                  className="btn sm"
                  disabled={!!busy}
                  title={L(
                    "Tách thành 2 lần hiển thị tại vị trí con trỏ (không đặt con trỏ: tự tách ở dấu phẩy gần giữa câu). Giọng đọc giữ nguyên.",
                    "Split into two cues at the caret (no caret: the clause break nearest the middle). The voice is kept.",
                  )}
                  onClick={() => void splitSelected()}
                >
                  {busy === "split" ? L("Đang tách…", "Splitting…") : L("Tách câu", "Split")}
                </button>
                <button
                  className="btn sm"
                  disabled={!!busy || selectedIndex >= segments.length - 1}
                  onClick={() => void mergeSelected()}
                >
                  {busy === "merge" ? L("Đang gộp…", "Merging…") : L("Gộp với câu sau", "Merge with next")}
                </button>
                <button
                  className="btn sm danger"
                  disabled={!!busy}
                  title={L("Xoá cue (phím Delete)", "Delete cue (Delete key)")}
                  onClick={() => void deleteSelected()}
                >
                  {busy === "delete" ? L("Đang xoá…", "Deleting…") : L("Xoá cue", "Delete cue")}
                </button>
              </div>
              {staleCount > 0 || batch ? (
                <div className="editor-batch">
                  {batch ? (
                    <>
                      <span>{L("Đang tạo lại audio", "Regenerating audio")} {batch.done}/{batch.total}</span>
                      <button className="btn-link" onClick={() => (batchCancel.current = true)}>{L("Dừng", "Stop")}</button>
                    </>
                  ) : (
                    <button className="btn-link" disabled={!!busy} onClick={() => void regenerateAllStale()}>
                      {L(`Tạo lại audio cho ${staleCount} câu đã sửa`, `Regenerate audio for ${staleCount} edited lines`)}
                    </button>
                  )}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      {selectedRegion ? (
        <RegionBar
          region={selectedRegion}
          index={workspace.original_regions.indexOf(selectedRegion) + 1}
          onChange={(patch) =>
            saveRegions(
              workspace.original_regions.map((region) => (region.id === selectedRegion.id ? { ...region, ...patch } : region)),
            )
          }
          onPlay={() => {
            if (!originalOn) saveAudio({ original_enabled: true });
            seek(selectedRegion.start);
            stopAt.current = selectedRegion.end;
            void videoRef.current?.play();
          }}
          onDelete={() => {
            saveRegions(workspace.original_regions.filter((region) => region.id !== selectedRegion.id));
            setSelectedRegionId(null);
          }}
          onClose={() => setSelectedRegionId(null)}
          L={L}
        />
      ) : null}
      <Timeline
        onAddCue={addCueAt}
        onRetime={retimeSegment}
        onRetimeVoice={retimeVoice}
        waveform={waveform}
        selectedRegionId={selectedRegionId}
        onSelectRegion={setSelectedRegionId}
        onRegionsChange={saveRegions}
        workspace={workspace}
        time={time}
        playing={playing}
        zoom={zoom}
        onZoom={setZoom}
        selectedId={selectedId}
        onSeek={seek}
        onSelect={select}
        L={L}
      />
    </div>
  );
}

function SubtitlePreview({
  workspace,
  segment,
  time,
  onMove,
}: {
  workspace: EditorWorkspace;
  segment: EditorSegment | null;
  time: number;
  onMove: (positionY: number) => void;
}) {
  const [dragY, setDragY] = useState<number | null>(null);
  const part = segment?.display.find((item) => time >= item.start && time < item.end) ?? segment?.display[0];
  if (!workspace.subtitle || !part) return null;
  // Local style wins so edits show before the server answers; line wrapping comes from the server.
  const style = { ...workspace.subtitle, ...workspace.style };
  const fontPx = Math.max(8, Math.round(style.font_size * workspace.width / 512));
  const positionY = dragY ?? style.position_y;
  // Sizes are in video pixels (ASS PlayRes = video size); cqw scales them to the preview width.
  const px = (value: number) => `${(value / workspace.width) * 100}cqw`;
  const mode = !style.box_opacity ? "none" : style.box_style === "panel" ? "panel" : "classic";
  const padding = mode === "panel" ? (style.box_padding ?? 0) * fontPx : mode === "classic" ? style.outline : 0;
  const boxHeight = part.lines.length * fontPx + 2 * padding;
  const stroke = mode === "classic" ? 0 : style.outline;
  const anchorX = style.position_x <= 0.33 ? "0%" : style.position_x >= 0.67 ? "-100%" : "-50%";
  const align = style.position_x <= 0.33 ? "left" : style.position_x >= 0.67 ? "right" : "center";

  const beginDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const frame = event.currentTarget.closest(".editor-video")?.getBoundingClientRect();
    if (!frame) return;
    let latest = positionY;
    const onMovePointer = (move: PointerEvent) => {
      latest = Math.min(0.95, Math.max(0.05, (move.clientY - frame.top) / frame.height));
      setDragY(latest);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMovePointer);
      window.removeEventListener("pointerup", onUp);
      onMove(Math.round(latest * 1000) / 1000);
      setDragY(null);
    };
    window.addEventListener("pointermove", onMovePointer);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <div
      className={`editor-sub-preview${dragY !== null ? " dragging" : ""}`}
      onPointerDown={beginDrag}
      style={{
        left: `${style.position_x * 100}%`,
        top: `${positionY * 100}%`,
        // Same as ASS \an5\pos: the block's centre sits on position_y whatever the line count.
        transform: `translate(${anchorX}, -50%)`,
        textAlign: align,
        fontFamily: `"${style.font_name}", Arial, sans-serif`,
        fontWeight: style.bold ? 700 : 400,
        fontSize: px(fontPx),
        color: style.text_color,
        padding: px(padding),
        background: mode === "none" ? "transparent" : hexAlpha(style.box_color, style.box_opacity),
        borderRadius: mode === "panel" ? px(((style.box_radius ?? 0) * boxHeight) / 2) : 0,
        WebkitTextStroke: stroke ? `${px(stroke * 2)} ${style.outline_color}` : undefined,
        textShadow: style.shadow && mode !== "classic"
          ? `${px(style.shadow)} ${px(style.shadow)} 0 rgba(0, 0, 0, 0.55)`
          : undefined,
      }}
    >
      {part.lines.map((line, index) => (
        <div key={index}>{line}</div>
      ))}
    </div>
  );
}

function VolumeSlider({
  value,
  title,
  onCommit,
}: {
  value: number;
  title: string;
  onCommit: (value: number) => void;
}) {
  const [live, setLive] = useState(value);
  useEffect(() => setLive(value), [value]);
  const commit = () => {
    if (live !== value) onCommit(live);
  };
  return (
    <label className="editor-range editor-volume" title={title}>
      <input
        type="range"
        min={0}
        max={200}
        step={1}
        value={live}
        onChange={(e) => setLive(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
      />
      <span className="mono">{Math.round(live)}%</span>
    </label>
  );
}

function editableState(workspace: EditorWorkspace): EditorEditableState {
  return {
    segments: workspace.segments,
    blur: workspace.blur,
    audio: workspace.audio,
    style: workspace.style,
    max_lines: workspace.max_lines,
    original_regions: workspace.original_regions,
  };
}

function SaveStatus({
  state,
  dirty,
  L,
}: {
  state: { pending: number; savedAt: Date | null; failed: string | null };
  dirty: boolean;
  L: (vi: string, en: string) => string;
}) {
  if (state.failed) {
    return (
      <span className="editor-save failed" title={state.failed}>
        {L("Lưu lỗi — chỉnh sửa gần nhất chưa được lưu", "Save failed — latest edit not saved")}
      </span>
    );
  }
  if (state.pending > 0 || dirty) {
    return <span className="editor-save pending">{L("Đang lưu…", "Saving…")}</span>;
  }
  if (state.savedAt) {
    return (
      <span className="editor-save ok">
        ✓ {L("Đã lưu", "Saved")} {state.savedAt.toLocaleTimeString()}
      </span>
    );
  }
  return <span className="editor-save ok">✓ {L("Tự động lưu", "Autosave on")}</span>;
}

function DraftsMenu({
  jobId,
  beforeSave,
  onRestored,
  L,
}: {
  jobId: string;
  beforeSave: () => Promise<boolean>;
  onRestored: (workspace: EditorWorkspace) => void;
  L: (vi: string, en: string) => string;
}) {
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<EditorDraft[]>([]);
  const [name, setName] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    listEditorDrafts(jobId).then(setDrafts).catch(() => undefined);
    const onDown = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open, jobId]);

  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  };

  const save = () =>
    act(async () => {
      if (!(await beforeSave())) throw new Error(L("Còn chỉnh sửa chưa lưu được.", "Some edits could not be saved."));
      const draft = await saveEditorDraft(jobId, name || new Date().toLocaleString());
      setDrafts((items) => [draft, ...items]);
      setName("");
      setMessage(L(`Đã lưu bản nháp "${draft.name}".`, `Saved draft "${draft.name}".`));
    });

  const restore = (draft: EditorDraft) =>
    act(async () => {
      await beforeSave();
      onRestored(await restoreEditorDraft(jobId, draft.id));
      setDrafts(await listEditorDrafts(jobId));
      setMessage(L(`Đã khôi phục "${draft.name}". Bản trước đó được tự lưu.`, `Restored "${draft.name}". The previous state was saved as a draft.`));
    });

  const remove = (draft: EditorDraft) =>
    act(async () => {
      await deleteEditorDraft(jobId, draft.id);
      setDrafts((items) => items.filter((item) => item.id !== draft.id));
    });

  return (
    <div className="editor-drafts" ref={ref}>
      <button className="btn sm" onClick={() => setOpen((value) => !value)}>
        {L("Bản nháp", "Drafts")} ▾
      </button>
      {open ? (
        <div className="editor-drafts-pop">
          <div className="editor-drafts-save">
            <input
              placeholder={L("Tên bản nháp (tuỳ chọn)", "Draft name (optional)")}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void save();
              }}
            />
            <button className="btn sm primary" disabled={busy} onClick={() => void save()}>
              {L("Lưu bản nháp", "Save draft")}
            </button>
          </div>
          <p className="muted editor-hint">
            {L(
              "Mọi chỉnh sửa đã tự lưu. Bản nháp là bản chụp để quay lại sau.",
              "Edits autosave. A draft is a snapshot you can return to.",
            )}
          </p>
          {message ? <p className="editor-hint">{message}</p> : null}
          <div className="editor-drafts-list">
            {drafts.length ? (
              drafts.map((draft) => (
                <div key={draft.id} className="editor-draft-row">
                  <div>
                    <strong>{draft.name}</strong>
                    <span className="muted">{new Date(draft.created_at).toLocaleString()}</span>
                  </div>
                  {confirming === draft.id ? (
                    <button className="btn sm primary" disabled={busy} onClick={() => void restore(draft)}>
                      {L("Chắc chắn?", "Sure?")}
                    </button>
                  ) : (
                    <button className="btn sm" disabled={busy} onClick={() => setConfirming(draft.id)}>
                      {L("Khôi phục", "Restore")}
                    </button>
                  )}
                  <button className="btn-link" disabled={busy} onClick={() => void remove(draft)}>
                    {L("Xoá", "Delete")}
                  </button>
                </div>
              ))
            ) : (
              <p className="muted editor-hint">{L("Chưa có bản nháp nào.", "No drafts yet.")}</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function RegionBar({
  region,
  index,
  onChange,
  onPlay,
  onDelete,
  onClose,
  L,
}: {
  region: EditorRegion;
  index: number;
  onChange: (patch: Partial<EditorRegion>) => void;
  onPlay: () => void;
  onDelete: () => void;
  onClose: () => void;
  L: (vi: string, en: string) => string;
}) {
  const [volume, setVolume] = useState(region.volume);
  useEffect(() => setVolume(region.volume), [region.volume]);
  const commit = () => {
    if (volume !== region.volume) onChange({ volume });
  };
  return (
    <div className="editor-region-bar">
      <strong>{L(`Vùng âm gốc #${index}`, `Original region #${index}`)}</strong>
      <span className="mono">
        {formatTime(region.start)} → {formatTime(region.end)} ({(region.end - region.start).toFixed(2)}s)
      </span>
      <FadeSlider
        label={L("Tăng dần", "Fade in")}
        value={region.fade_in}
        max={Math.max(0, region.end - region.start - region.fade_out)}
        onCommit={(fade_in) => onChange({ fade_in })}
      />
      <FadeSlider
        label={L("Giảm dần", "Fade out")}
        value={region.fade_out}
        max={Math.max(0, region.end - region.start - region.fade_in)}
        onCommit={(fade_out) => onChange({ fade_out })}
      />
      <label className="editor-range">
        {L("Âm lượng", "Volume")}
        <input
          type="range"
          min={0}
          max={400}
          step={5}
          value={volume}
          onChange={(e) => setVolume(Number(e.target.value))}
          onPointerUp={commit}
          onKeyUp={commit}
        />
        <span className="mono">{Math.round(volume)}%</span>
      </label>
      <button className="btn sm" onClick={onPlay}>▶ {L("Nghe vùng", "Play")}</button>
      <button className="btn sm danger" onClick={onDelete}>{L("Xoá vùng", "Delete")}</button>
      <span className="spacer" />
      <span className="muted editor-hint">{L("Kéo 2 mép vùng trên timeline để trim.", "Drag the edges on the timeline to trim.")}</span>
      <button className="btn-link" onClick={onClose}>{L("Đóng", "Close")}</button>
    </div>
  );
}

function FadeSlider({
  label,
  value,
  max,
  onCommit,
}: {
  label: string;
  value: number;
  max: number;
  onCommit: (value: number) => void;
}) {
  const [live, setLive] = useState(value);
  useEffect(() => setLive(value), [value]);
  const commit = () => {
    if (live !== value) onCommit(Math.round(live * 100) / 100);
  };
  return (
    <label className="editor-range">
      {label}
      <input
        type="range"
        min={0}
        max={Math.max(0.1, Math.min(10, max))}
        step={0.1}
        value={Math.min(live, max)}
        onChange={(e) => setLive(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
      />
      <span className="mono">{live.toFixed(1)}s</span>
    </label>
  );
}

type Panels = { blur: boolean; style: boolean };
const PANELS_KEY = "vd.editor.panels";

function loadPanels(): Panels {
  try {
    const raw = localStorage.getItem(PANELS_KEY);
    if (raw) return { blur: true, style: false, ...JSON.parse(raw) };
  } catch {
    /* fall back to defaults */
  }
  return { blur: true, style: false };
}

function Collapsible({
  title,
  summary,
  open,
  onToggle,
  children,
}: {
  title: string;
  summary: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className={`editor-section${open ? " open" : ""}`}>
      <button className="editor-section-head" aria-expanded={open} onClick={onToggle}>
        <span className="editor-section-caret">{open ? "▾" : "▸"}</span>
        <strong>{title}</strong>
        <span className="muted editor-section-summary">{summary}</span>
      </button>
      {open ? <div className="editor-section-body">{children}</div> : null}
    </section>
  );
}

function TimeField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const nudge = (delta: number) => onChange(Math.max(0, Number(value) + delta).toFixed(2));
  return (
    <label className="editor-timefield">
      <span>{label}</span>
      <button type="button" onClick={() => nudge(-0.1)}>−</button>
      <input value={value} inputMode="decimal" onChange={(e) => onChange(e.target.value)} />
      <button type="button" onClick={() => nudge(0.1)}>+</button>
    </label>
  );
}

const SegmentList = memo(function SegmentList({
  segments,
  selectedId,
  activeId,
  lang,
  onSelect,
}: {
  segments: EditorSegment[];
  selectedId: string | null;
  activeId: string | null;
  lang: string;
  onSelect: (seg: EditorSegment) => void;
}) {
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-id="${selectedId}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  return (
    <div className="editor-list" ref={listRef}>
      {segments.map((seg) => (
        <button
          key={seg.id}
          data-id={seg.id}
          className={`editor-row${seg.id === selectedId ? " selected" : ""}${seg.id === activeId ? " active" : ""}${seg.muted ? " muted-row" : ""}`}
          onClick={() => onSelect(seg)}
        >
          <span className="mono editor-row-time">{formatTime(seg.start)}</span>
          <span className="editor-row-text">{seg.text || "—"}</span>
          {seg.audio_url?.includes("/editor/") ? <span className="editor-row-regen" title="Audio đã tạo lại">↻</span> : null}
          {seg.issues.length ? (
            <span className="editor-row-issues">
              {seg.issues.map((issue) => (
                <span key={issue} className={`editor-dot ${issue}`} title={ISSUE_LABEL[issue][lang === "vi" ? 0 : 1]} />
              ))}
            </span>
          ) : null}
        </button>
      ))}
      {!segments.length ? <p className="muted" style={{ padding: 12 }}>—</p> : null}
    </div>
  );
});

function BlurBox({ blur, onChange }: { blur: EditorBlur; onChange: (blur: Partial<EditorBlur>) => void }) {
  const [live, setLive] = useState(blur);
  // The frame and handle only show once the box is clicked, so they don't cover the video.
  const [active, setActive] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => setLive(blur), [blur]);
  useEffect(() => {
    if (!active) return;
    const onDown = (event: PointerEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setActive(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActive(false);
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [active]);
  if (!blur.enabled) return null;

  const begin = (mode: "move" | "resize") => (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const frame = event.currentTarget.closest(".editor-video")?.getBoundingClientRect();
    if (!frame) return;
    setActive(true);
    const origin = { x: event.clientX, y: event.clientY, box: live };
    let latest = live;
    const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
    const onMove = (move: PointerEvent) => {
      const dx = (move.clientX - origin.x) / frame.width;
      const dy = (move.clientY - origin.y) / frame.height;
      const box = origin.box;
      latest =
        mode === "move"
          ? { ...box, x: clamp(box.x + dx, 0.02, 0.98), y: clamp(box.y + dy, 0.02, 0.98) }
          : {
              ...box,
              // Resize from the bottom-right corner while keeping the top-left fixed.
              width: clamp(box.width + dx, 0.04, 1),
              height: clamp(box.height + dy, 0.03, 0.8),
              x: clamp(box.x + dx / 2, 0.02, 0.98),
              y: clamp(box.y + dy / 2, 0.02, 0.98),
            };
      setLive(latest);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (latest === origin.box) return; // A plain click only selects the box.
      const round = (value: number) => Math.round(value * 1000) / 1000;
      onChange({ x: round(latest.x), y: round(latest.y), width: round(latest.width), height: round(latest.height) });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const strength = Math.max(1, (live.strength / 50) * 10);
  const filter =
    live.style === "box" ? `blur(${strength}px)` : `blur(${strength * 0.6}px)`;
  return (
    <div
      ref={boxRef}
      className={`editor-blur${active ? " active" : ""}`}
      title={active ? undefined : "Bấm để chỉnh vùng làm mờ"}
      style={{
        left: `${(live.x - live.width / 2) * 100}%`,
        top: `${(live.y - live.height / 2) * 100}%`,
        width: `${live.width * 100}%`,
        height: `${live.height * 100}%`,
        backdropFilter: filter,
        WebkitBackdropFilter: filter,
      }}
      onPointerDown={begin("move")}
    >
      {active ? <div className="editor-blur-handle" onPointerDown={begin("resize")} /> : null}
    </div>
  );
}

function BlurPanel({
  blur,
  onChange,
  L,
}: {
  blur: EditorBlur;
  onChange: (blur: Partial<EditorBlur>) => void;
  L: (vi: string, en: string) => string;
}) {
  const [strength, setStrength] = useState(blur.strength);
  useEffect(() => setStrength(blur.strength), [blur.strength]);
  return (
    <div className="editor-blur-panel">
      <label className="editor-toggle">
        <input type="checkbox" checked={blur.enabled} onChange={(e) => onChange({ enabled: e.target.checked })} />
        {L("Bật", "On")}
      </label>
      <select value={blur.style} disabled={!blur.enabled} onChange={(e) => onChange({ style: e.target.value as EditorBlur["style"] })}>
        <option value="box">{L("Đều", "Box")}</option>
        <option value="horizontal">{L("Ngang", "Horizontal")}</option>
        <option value="vertical">{L("Dọc", "Vertical")}</option>
      </select>
      <label className="editor-range">
        {L("Độ mờ", "Strength")}
        <input
          type="range"
          min={0}
          max={250}
          value={strength}
          disabled={!blur.enabled}
          onChange={(e) => setStrength(Number(e.target.value))}
          onPointerUp={() => onChange({ strength })}
          onKeyUp={() => onChange({ strength })}
        />
        <span className="mono">{strength}</span>
      </label>
      <span className="muted editor-hint">{L("Kéo khung trên video để di chuyển, góc phải dưới để đổi cỡ.", "Drag the box on the video; bottom-right corner resizes.")}</span>
    </div>
  );
}

function Timeline({
  onAddCue,
  onRetime,
  onRetimeVoice,
  waveform,
  selectedRegionId,
  onSelectRegion,
  onRegionsChange,
  workspace,
  time,
  playing,
  zoom,
  onZoom,
  selectedId,
  onSeek,
  onSelect,
  L,
}: {
  onAddCue: (t: number) => void;
  onRetime: (id: string, start: number, end: number) => void;
  onRetimeVoice: (id: string, patch: { audio_start?: number; audio_length?: number }) => void;
  waveform: Waveform | null;
  selectedRegionId: string | null;
  onSelectRegion: (id: string | null) => void;
  onRegionsChange: (regions: EditorRegion[], selectId?: string) => void;
  workspace: EditorWorkspace;
  time: number;
  playing: boolean;
  zoom: number;
  onZoom: (zoom: number) => void;
  selectedId: string | null;
  onSeek: (t: number) => void;
  onSelect: (seg: EditorSegment, jump?: boolean) => void;
  L: (vi: string, en: string) => string;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const width = Math.max(1, workspace.duration * zoom);
  const [viewport, setViewport] = useState({ left: 0, width: 0 });
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setViewport({ left: el.scrollLeft, width: el.clientWidth }));
    };
    update();
    el.addEventListener("scroll", update);
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      el.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, []);
  // Trackpad pinch zooms the timeline around the pointer. WebKit (Tauri on macOS) sends
  // gesture* events with a scale; Chromium sends wheel events with ctrlKey set.
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const zoomAnchor = useRef<{ time: number; offset: number } | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const zoomAt = (clientX: number, next: number) => {
      const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next));
      if (Math.abs(clamped - zoomRef.current) < 0.01) return;
      const offset = clientX - el.getBoundingClientRect().left;
      // Several events can land before React re-renders; keep the first anchor's time.
      zoomAnchor.current = {
        time: zoomAnchor.current?.time ?? (el.scrollLeft + offset) / zoomRef.current,
        offset,
      };
      zoomRef.current = clamped;
      onZoom(clamped);
    };
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return; // Plain two-finger swipes keep scrolling the timeline.
      event.preventDefault();
      zoomAt(event.clientX, zoomRef.current * Math.exp(-event.deltaY * 0.01));
    };
    let gestureBase = zoomRef.current;
    type GestureEvent = Event & { scale: number; clientX: number };
    const onGestureStart = (event: Event) => {
      event.preventDefault();
      gestureBase = zoomRef.current;
    };
    const onGestureChange = (event: Event) => {
      event.preventDefault();
      const gesture = event as GestureEvent;
      zoomAt(gesture.clientX, gestureBase * gesture.scale);
    };
    const block = (event: Event) => event.preventDefault();
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("gesturestart", onGestureStart);
    el.addEventListener("gesturechange", onGestureChange);
    el.addEventListener("gestureend", block);
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("gesturestart", onGestureStart);
      el.removeEventListener("gesturechange", onGestureChange);
      el.removeEventListener("gestureend", block);
    };
  }, [onZoom]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const anchor = zoomAnchor.current;
    if (anchor) {
      el.scrollLeft = Math.max(0, anchor.time * zoom - anchor.offset);
      zoomAnchor.current = null;
    }
    setViewport({ left: el.scrollLeft, width: el.clientWidth });
  }, [zoom]);
  const playheadX = time * zoom;

  // Follow the playhead while playing.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !playing) return;
    if (playheadX < el.scrollLeft || playheadX > el.scrollLeft + el.clientWidth - 40) {
      el.scrollLeft = Math.max(0, playheadX - 80);
    }
  }, [playheadX, playing]);

  // Bring the selected segment into view.
  useEffect(() => {
    const el = scrollRef.current;
    const seg = workspace.segments.find((item) => item.id === selectedId);
    if (!el || !seg) return;
    const x = seg.start * zoom;
    if (x < el.scrollLeft || x > el.scrollLeft + el.clientWidth - 60) el.scrollLeft = Math.max(0, x - el.clientWidth / 3);
  }, [selectedId, zoom]); // eslint-disable-line react-hooks/exhaustive-deps

  const seekFromEvent = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      const rect = event.currentTarget.getBoundingClientRect();
      onSeek((event.clientX - rect.left) / zoom);
    },
    [onSeek, zoom],
  );

  const step = zoom >= 120 ? 1 : zoom >= 40 ? 5 : zoom >= 12 ? 15 : zoom >= 4 ? 60 : 300;
  const ticks = useMemo(() => {
    const items: number[] = [];
    for (let t = 0; t <= workspace.duration; t += step) items.push(t);
    return items;
  }, [workspace.duration, step]);

  return (
    <div className="editor-timeline">
      <div className="editor-timeline-head">
        <div className="editor-track-labels">
          <span />
          <span>{L("Phụ đề", "Subtitles")}</span>
          <span>{L("Giọng", "Voice")}</span>
          <span title={L("Kéo để tạo vùng chỉnh âm lượng âm gốc", "Drag to add an original-audio volume region")}>
            {L("Âm gốc", "Original")}
          </span>
          <span>{L("Làm mờ", "Blur")}</span>
        </div>
        <div className="editor-tracks" ref={scrollRef}>
          <div className="editor-tracks-inner" style={{ width }}>
            <div className="editor-ruler" onClick={seekFromEvent}>
              {ticks.map((t) => (
                <span key={t} style={{ left: t * zoom }}>{formatTime(t, false)}</span>
              ))}
            </div>
            <TrackBlocks
              workspace={workspace}
              zoom={zoom}
              selectedId={selectedId}
              onSelect={onSelect}
              onSeek={seekFromEvent}
              onRetime={onRetime}
              onRetimeVoice={onRetimeVoice}
              onAddCue={onAddCue}
            />
            <OriginalTrack
              duration={workspace.duration}
              zoom={zoom}
              viewport={viewport}
              waveform={waveform}
              regions={workspace.original_regions}
              baseVolume={workspace.audio.original_volume}
              selectedRegionId={selectedRegionId}
              onSelectRegion={onSelectRegion}
              onRegionsChange={onRegionsChange}
              onSeek={onSeek}
            />
            <div className="editor-lane" onClick={seekFromEvent}>
              {workspace.blur.enabled ? <div className="editor-block blur" style={{ left: 0, width }} /> : null}
            </div>
            <div className="editor-playhead" style={{ left: playheadX }} />
          </div>
        </div>
      </div>
      <label className="editor-zoom">
        {L("Thu phóng", "Zoom")}
        <input type="range" min={ZOOM_MIN} max={ZOOM_MAX} step={0.5} value={zoom} onChange={(e) => onZoom(Number(e.target.value))} />
      </label>
    </div>
  );
}

const ZOOM_MIN = 1;
const ZOOM_MAX = 200;

type CueDrag = { id: string; mode: "move" | "start" | "end"; start: number; end: number; shift: number };

const MIN_CUE = 0.2;

const TrackBlocks = memo(function TrackBlocks({
  workspace,
  zoom,
  selectedId,
  onSelect,
  onSeek,
  onRetime,
  onRetimeVoice,
  onAddCue,
}: {
  onAddCue: (t: number) => void;
  workspace: EditorWorkspace;
  zoom: number;
  selectedId: string | null;
  onSelect: (seg: EditorSegment, jump?: boolean) => void;
  onSeek: (event: React.MouseEvent<HTMLDivElement>) => void;
  onRetime: (id: string, start: number, end: number) => void;
  onRetimeVoice: (id: string, patch: { audio_start?: number; audio_length?: number }) => void;
}) {
  const [drag, setDrag] = useState<CueDrag | null>(null);
  const [voiceDrag, setVoiceDrag] = useState<{ id: string; start: number; length: number } | null>(null);

  // Voice track, independent of subtitles: body drag moves the take, edges speed it up / slow it down.
  const beginVoice = (seg: EditorSegment, mode: CueDrag["mode"]) => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const end = seg.audio_start + seg.audio_duration;
    let latest = { id: seg.id, start: seg.audio_start, length: seg.audio_duration };
    let moved = false;
    const onMove = (pointer: PointerEvent) => {
      const dx = pointer.clientX - startX;
      if (!moved && Math.abs(dx) < 3) return;
      moved = true;
      const dt = dx / zoom;
      if (mode === "move") {
        latest = { ...latest, start: Math.min(workspace.duration - seg.audio_duration, Math.max(0, seg.audio_start + dt)) };
      } else if (mode === "start") {
        const start = Math.min(end - 0.3, Math.max(0, seg.audio_start + dt));
        latest = { ...latest, start, length: end - start };
      } else {
        latest = { ...latest, length: Math.max(0.3, seg.audio_duration + dt) };
      }
      setVoiceDrag(latest);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setVoiceDrag(null);
      if (!moved) {
        onSelect(seg);
        return;
      }
      const round = (value: number) => Math.round(value * 1000) / 1000;
      onSelect(seg, false);
      onRetimeVoice(
        seg.id,
        mode === "move"
          ? { audio_start: round(latest.start) }
          : mode === "start"
            ? { audio_start: round(latest.start), audio_length: round(latest.length) }
            : { audio_length: round(latest.length) },
      );
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };
  const segments = workspace.segments;

  // Body drag moves the cue (and its voice); edge drags trim/extend only the subtitle.
  const begin = (seg: EditorSegment, index: number, mode: CueDrag["mode"]) => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    // Neighbours bound the cue; an existing overlap is tolerated rather than snapped.
    const floor = Math.min(seg.start, index > 0 ? segments[index - 1].end : 0);
    const ceiling = Math.max(seg.end, index + 1 < segments.length ? segments[index + 1].start : workspace.duration);
    let latest: CueDrag = { id: seg.id, mode, start: seg.start, end: seg.end, shift: 0 };
    let moved = false;
    const onMove = (pointer: PointerEvent) => {
      const dx = pointer.clientX - startX;
      if (!moved && Math.abs(dx) < 3) return;
      moved = true;
      const dt = dx / zoom;
      if (mode === "move") {
        const shift = Math.min(ceiling - seg.end, Math.max(floor - seg.start, dt));
        latest = { ...latest, start: seg.start + shift, end: seg.end + shift, shift };
      } else if (mode === "start") {
        latest = { ...latest, start: Math.min(seg.end - MIN_CUE, Math.max(floor, seg.start + dt)) };
      } else {
        latest = { ...latest, end: Math.max(seg.start + MIN_CUE, Math.min(ceiling, seg.end + dt)) };
      }
      setDrag(latest);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setDrag(null);
      if (!moved) {
        onSelect(seg);
        return;
      }
      const round = (value: number) => Math.round(value * 1000) / 1000;
      onSelect(seg, false);
      onRetime(seg.id, round(latest.start), round(latest.end));
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <>
      <div
        className="editor-lane"
        onClick={onSeek}
        onDoubleClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          onAddCue((event.clientX - rect.left) / zoom);
        }}
      >
        {segments.map((seg, index) => {
          const live = drag?.id === seg.id ? drag : null;
          const start = live ? live.start : seg.start;
          const end = live ? live.end : seg.end;
          const width = Math.max(2, (end - start) * zoom);
          return (
            <div
              key={seg.id}
              className={`editor-block sub${seg.id === selectedId || live ? " selected" : ""}${live ? " dragging" : ""}${seg.issues.includes("untranslated") || seg.issues.includes("empty") ? " bad" : ""}`}
              style={{ left: start * zoom, width }}
              title={seg.text}
              onPointerDown={begin(seg, index, "move")}
              onClick={(event) => event.stopPropagation()}
              onDoubleClick={(event) => event.stopPropagation()}
            >
              {width > 16 ? <div className="editor-cue-edge start" onPointerDown={begin(seg, index, "start")} /> : null}
              {zoom >= 30 ? seg.text : null}
              {width > 16 ? <div className="editor-cue-edge end" onPointerDown={begin(seg, index, "end")} /> : null}
            </div>
          );
        })}
      </div>
      <div className="editor-lane" onClick={onSeek}>
        {segments.map((seg) => {
          if (!seg.audio_url) return null;
          const live = voiceDrag?.id === seg.id ? voiceDrag : null;
          const start = live ? live.start : seg.audio_start;
          const length = live ? live.length : seg.audio_duration;
          const width = Math.max(2, length * zoom);
          return (
            <div
              key={seg.id}
              className={`editor-block voice${seg.id === selectedId || live ? " selected" : ""}${live ? " dragging" : ""}${seg.issues.includes("overrun") ? " bad" : ""}${seg.issues.includes("stale_audio") ? " stale" : ""}${seg.muted ? " off" : ""}${seg.audio_url.includes("/editor/") ? " regen" : ""}`}
              style={{ left: start * zoom, width }}
              title={`${length.toFixed(2)}s`}
              onPointerDown={beginVoice(seg, "move")}
              onClick={(event) => event.stopPropagation()}
            >
              {width > 16 ? <div className="editor-cue-edge start" onPointerDown={beginVoice(seg, "start")} /> : null}
              {live && live.length !== seg.audio_duration ? (
                <span className="editor-voice-tempo">
                  {live.length.toFixed(2)}s · ×{(seg.audio_duration / live.length).toFixed(2)}
                </span>
              ) : null}
              {width > 16 ? <div className="editor-cue-edge end" onPointerDown={beginVoice(seg, "end")} /> : null}
            </div>
          );
        })}
      </div>
    </>
  );
});
