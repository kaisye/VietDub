import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyAutoReview,
  getAutoReview,
  rejectAutoReview,
  revertAutoReviewBatch,
  startAutoReview,
  type AutoReview,
  type ReviewMode,
  type ReviewSeverity,
} from "../../lib/auto-review";
import type { EditorWorkspace } from "../../types";
import type { EditorController } from "./useEditor";
import { errorText } from "./utils";

export type ReviewMarker = { cueId: string; start: number; end: number; severity: ReviewSeverity; type: string };

/**
 * Auto review state for the editor: the stored result, polling while it runs or
 * applies, and actions that keep the editor's undo history and workspace in sync.
 */
export function useAutoReview(jobId: string, editor: EditorController) {
  const [review, setReview] = useState<AutoReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const editorRef = useRef(editor);
  editorRef.current = editor;

  const load = useCallback(async () => {
    try {
      setReview(await getAutoReview(jobId));
    } catch (e) {
      setError(errorText(e));
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  const running = review?.status === "running";
  const applying = review?.applying?.status === "running";
  useEffect(() => {
    if (!running && !applying) return;
    const timer = window.setInterval(() => void load(), 1200);
    return () => window.clearInterval(timer);
  }, [running, applying, load]);

  // When fixes land server-side (apply finished, or "safe" mode applied some), reload the workspace.
  const lastBatches = useRef<number | null>(null);
  const wasApplying = useRef(false);
  useEffect(() => {
    if (!review) return;
    const batches = review.batches?.length ?? 0;
    const finishedApply = wasApplying.current && !applying;
    const newBatch = lastBatches.current !== null && batches > lastBatches.current;
    wasApplying.current = applying;
    lastBatches.current = batches;
    if (finishedApply || newBatch) {
      void editorRef.current.load().then(() => editorRef.current.resync());
    }
  }, [review, applying]);

  const act = useCallback(async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }, []);

  const start = useCallback(
    (mode: ReviewMode) =>
      act("start", async () => {
        await editorRef.current.flushAll();
        if (mode === "safe") editorRef.current.checkpoint("auto-review");
        setReview(await startAutoReview(jobId, mode));
      }),
    [act, jobId],
  );

  const apply = useCallback(
    (scope: "selected" | "all" | "safe", ids: string[] = []) =>
      act(scope === "selected" ? `apply:${ids.join(",")}` : `apply:${scope}`, async () => {
        if (!(await editorRef.current.flushAll())) return;
        editorRef.current.checkpoint("auto-review"); // Undo (⌘Z) puts the whole batch back.
        setReview(await applyAutoReview(jobId, scope, ids));
      }),
    [act, jobId],
  );

  const reject = useCallback(
    (ids: string[]) =>
      act(`reject:${ids.join(",")}`, async () => {
        setReview(await rejectAutoReview(jobId, ids));
      }),
    [act, jobId],
  );

  const revert = useCallback(
    (batchId: string) =>
      act(`revert:${batchId}`, async () => {
        const result = await revertAutoReviewBatch<EditorWorkspace>(jobId, batchId);
        lastBatches.current = result.review.batches.length;
        setReview(result.review);
        editorRef.current.onDraftRestored(result.workspace);
      }),
    [act, jobId],
  );

  const markers = useMemo<ReviewMarker[]>(
    () =>
      (review?.issues ?? []).map((issue) => ({
        cueId: issue.cue_id,
        start: issue.start,
        end: Math.max(issue.end, issue.start + 0.15),
        severity: issue.severity,
        type: issue.type,
      })),
    [review?.issues],
  );
  const pending = useMemo(() => (review?.proposals ?? []).filter((proposal) => proposal.status === "pending").length, [review?.proposals]);

  return { review, error, setError, busy, running, applying, load, start, apply, reject, revert, markers, pending };
}

export type AutoReviewController = ReturnType<typeof useAutoReview>;
