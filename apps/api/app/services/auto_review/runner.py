"""Auto review — background runs, the stored change set, apply / reject / revert.

State lives next to the editor workspace in ``editor/<job>/auto-review.json`` so it
survives restarts. Every change goes through the editor workspace (same functions
the editor endpoints use), and each apply batch first saves an editor draft, so a
whole batch can be reverted (and the editor's own undo works on the result).
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import editor
from . import detect as det
from . import llm as llm_module
from . import plan as planner
from . import vi_text
from .views import _source_take, build_views, layout_for, natural_duration

log = logging.getLogger(__name__)

REVIEW_VERSION = 1
MODES = ("report", "propose", "safe")
MAX_REWRITES = 120
MAX_ROUNDS = 2
DRAFT_NAME = "Trước khi áp dụng rà soát tự động"

_RUNNING: dict[str, str] = {}  # job id → "review" | "apply"
_LOCKS: dict[str, threading.Lock] = {}
_REGISTRY = threading.Lock()

# Injection points for tests (LLM, TTS).
Completers = list[tuple[str, llm_module.Completer]]
completers_factory: Callable[[], Completers] = llm_module.default_completers


def _lock(job_id: str) -> threading.Lock:
    with _REGISTRY:
        return _LOCKS.setdefault(job_id, threading.Lock())


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def review_path(job_id: str) -> Path:
    return editor.workspace_dir(job_id) / "auto-review.json"


def is_running(job_id: str) -> bool:
    return job_id in _RUNNING


def load_review(job_id: str) -> dict[str, Any] | None:
    path = editor.ensure_storage() / "editor" / job_id / "auto-review.json"
    if not path.exists():
        return None
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if review.get("status") == "running" and job_id not in _RUNNING:
        review["status"] = "failed"
        review["error"] = "Rà soát bị ngắt do backend khởi động lại."
    if (review.get("applying") or {}).get("status") == "running" and job_id not in _RUNNING:
        review["applying"]["status"] = "failed"
        review["applying"]["error"] = "Áp dụng bị ngắt do backend khởi động lại."
    return review


def save_review(review: dict[str, Any]) -> dict[str, Any]:
    review["updated_at"] = _now()
    path = review_path(review["job_id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return review


def _update(job_id: str, **changes: Any) -> dict[str, Any]:
    with _lock(job_id):
        review = load_review(job_id) or {"job_id": job_id}
        review.update(changes)
        return save_review(review)


def summary(review: dict[str, Any] | None) -> dict[str, Any]:
    """Small status for badges (project cards, progress screen)."""
    if not review:
        return {"status": "none"}
    proposals = review.get("proposals") or []
    current = review.get("summary_current") or review.get("summary_before") or {}
    return {
        "status": review.get("status"),
        "mode": review.get("mode"),
        "trigger": review.get("trigger"),
        "finished_at": review.get("finished_at"),
        "score_before": (review.get("summary_before") or {}).get("score"),
        "score": current.get("score"),
        "score_predicted": (review.get("summary_predicted") or {}).get("score"),
        "issues": current.get("issues", 0),
        "cues_with_issues": current.get("cues_with_issues", 0),
        "errors": (current.get("by_severity") or {}).get("error", 0),
        "pending": sum(1 for proposal in proposals if proposal.get("status") == "pending"),
        "applied": sum(1 for proposal in proposals if proposal.get("status") == "applied"),
        "applying": (review.get("applying") or {}).get("status") == "running",
    }


# --------------------------------------------------------------------------- workspace


def ensure_workspace(job_id: str, configuration: dict[str, Any] | None, title: str = "") -> dict[str, Any]:
    """The editor workspace, built from the pipeline output on first use (like opening the editor)."""
    workspace = editor.load_workspace(job_id)
    if workspace is None:
        workspace = editor.build_workspace(job_id, configuration, title)
    if not {"audio", "style", "max_lines", "original_regions"} <= workspace.keys():
        workspace.setdefault("audio", editor.default_audio(configuration))
        workspace.setdefault("style", editor.default_style(configuration))
        workspace.setdefault("max_lines", editor.default_max_lines(configuration))
        workspace.setdefault("original_regions", [])
        editor.save_workspace(workspace)
    return workspace


def _measure(workspace: dict[str, Any], cache: dict[str, float | None], progress: Callable[[int, int], None] | None = None) -> dict[str, float | None]:
    """Natural take length per segment id, cached by take URL (probing is the slow part)."""
    naturals: dict[str, float | None] = {}
    segments = workspace["segments"]
    for index, segment in enumerate(segments):
        key = f'{segment.get("audio_source_url") or ""}|{segment.get("audio_url") or ""}'
        if not segment.get("audio_url"):
            naturals[segment["id"]] = None
            continue
        if key not in cache:
            try:
                cache[key] = natural_duration(segment)
            except Exception:  # noqa: BLE001 — a missing file must not stop the review.
                cache[key] = None
        naturals[segment["id"]] = cache[key]
        if progress and index % 25 == 0:
            progress(index, len(segments))
    return naturals


def analyze(workspace: dict[str, Any], cache: dict[str, float | None] | None = None) -> tuple[list[det.CueView], det.Layout, list[det.Issue]]:
    views = build_views(workspace, _measure(workspace, {} if cache is None else cache))
    layout = layout_for(workspace)
    return views, layout, det.detect(views, layout)


def _take_urls(workspace: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    urls: dict[str, dict[str, str | None]] = {}
    for segment in workspace["segments"]:
        if not segment.get("audio_url"):
            continue
        source, _ = _source_take(segment)
        urls[segment["id"]] = {"audio_url": segment.get("audio_url"), "source_url": editor._storage_url(source) if source else None}
    return urls


# --------------------------------------------------------------------------- review run


def start_review(
    job_id: str,
    *,
    mode: str = "propose",
    configuration: dict[str, Any] | None = None,
    title: str = "",
    source_language: str = "auto",
    voice_settings: dict[str, Any] | None = None,
    trigger: str = "manual",
    background: bool = True,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"Chế độ rà soát không hợp lệ: {mode}")
    with _REGISTRY:
        if job_id in _RUNNING:
            raise RuntimeError("Đang rà soát hoặc áp dụng cho video này.")
        _RUNNING[job_id] = "review"
    try:
        with editor.workspace_lock(job_id):
            ensure_workspace(job_id, configuration, title)
        with _lock(job_id):
            previous = load_review(job_id) or {}
            review = {
                "version": REVIEW_VERSION,
                "job_id": job_id,
                "status": "running",
                "phase": "measure",
                "progress": {"done": 0, "total": 0},
                "mode": mode,
                "trigger": trigger,
                "started_at": _now(),
                "finished_at": None,
                "error": None,
                "llm": None,
                "thresholds": asdict(det.DEFAULT_THRESHOLDS),
                "summary_before": None,
                "summary_current": None,
                "summary_predicted": None,
                "issues": [],
                "proposals": [],
                "batches": previous.get("batches", []),
                "applying": None,
            }
            save_review(review)
    except Exception:
        _RUNNING.pop(job_id, None)
        raise
    args = (job_id, mode, source_language, voice_settings or {})
    if background:
        threading.Thread(target=_run_review, args=args, daemon=True, name=f"auto-review-{job_id[:8]}").start()
    else:
        _run_review(*args)
    return load_review(job_id) or review


def _run_review(job_id: str, mode: str, source_language: str, voice_settings: dict[str, Any]) -> None:
    try:
        with editor.workspace_lock(job_id):
            workspace = copy.deepcopy(editor.load_workspace(job_id))
        cache: dict[str, float | None] = {}
        naturals = _measure(
            workspace, cache, lambda done, total: _update(job_id, progress={"done": done, "total": total})
        )
        views = build_views(workspace, naturals)
        layout = layout_for(workspace)
        issues = det.detect(views, layout)
        before = det.summarize(views, issues)
        _update(
            job_id,
            phase="plan",
            summary_before=before,
            summary_current=before,
            issues=[issue.to_dict() for issue in issues],
        )
        if mode == "report":
            _update(job_id, status="done", phase="done", finished_at=_now())
            return

        result = planner.plan(views, layout, issues=issues)
        proposals = result.proposals
        predicted = result.predicted_cues
        rewrites = [proposal for proposal in proposals if proposal.needs_llm]
        llm_info: dict[str, Any] = {"available": False, "requested": 0, "accepted": 0}
        if rewrites:
            _update(job_id, phase="rewrite", progress={"done": 0, "total": min(len(rewrites), MAX_REWRITES)})
            sources = {segment["id"]: segment.get("source_text", "") for segment in workspace["segments"]}
            items = [
                {"id": proposal.cue_ids[0], **proposal.rewrite, "source": sources.get(proposal.cue_ids[0], "")}
                for proposal in rewrites[:MAX_REWRITES]
            ]
            texts, llm_info = llm_module.rewrite(
                items,
                workspace.get("source_language") or source_language,
                completers_factory(),
                lambda done: _update(job_id, progress={"done": done, "total": len(items)}),
            )
            kept: list[planner.Proposal] = []
            for proposal in proposals:
                if not proposal.needs_llm:
                    kept.append(proposal)
                    continue
                cue_id = proposal.cue_ids[0]
                text = texts.get(cue_id)
                if not text:
                    continue
                candidate, speed = planner.predict_rewrite(predicted, cue_id, text, layout.duration)
                before_pen, after_pen, _, after_types = planner._evaluate(predicted, candidate, [cue_id], layout, det.DEFAULT_THRESHOLDS)
                if after_pen >= before_pen - 0.01:
                    llm_info["not_better"] = llm_info.get("not_better", 0) + 1
                    continue  # Shorter, but not enough to fix anything.
                predicted = candidate
                proposal.predicted.update(penalty_before=before_pen, penalty_after=after_pen, issues_after=after_types)
                after_cue = next((cue for cue in predicted if cue.id == cue_id), None)
                proposal.ops = [{"op": "text", "id": cue_id, "text": text}, {"op": "tts", "id": cue_id}]
                proposal.after = [planner.snapshot(after_cue)] if after_cue else []
                proposal.predicted.update(
                    speed_after=round(speed, 3) if speed else None,
                    syllables_before=proposal.rewrite["syllables"],
                    syllables_after=vi_text.syllable_count(text),
                )
                kept.append(proposal)
            proposals = kept
        else:
            llm_info["available"] = bool(completers_factory())
        predicted_summary = det.summarize(predicted, det.detect(predicted, layout))
        urls = _take_urls(workspace)
        payload = []
        for proposal in proposals:
            data = proposal.to_dict()
            for snap in data["before"]:
                snap.update(urls.get(snap["id"], {}))
            payload.append(data)
        _update(
            job_id,
            phase="ready",
            proposals=payload,
            summary_predicted=predicted_summary,
            llm={key: value for key, value in llm_info.items() if key != "errors"} | {"errors": llm_info.get("errors", [])[:3]},
        )
        if mode == "safe":
            safe_ids = [proposal["id"] for proposal in payload if _is_safe(proposal, payload)]
            if safe_ids:
                _RUNNING[job_id] = "apply"
                _apply_run(job_id, safe_ids, voice_settings, label="Tự sửa an toàn", auto=True)
        _update(job_id, status="done", phase="done", finished_at=_now(), progress={"done": 1, "total": 1})
    except Exception as exc:  # noqa: BLE001 — reported in the review, never raised into the pipeline.
        log.exception("Auto review failed for %s", job_id)
        _update(job_id, status="failed", phase="failed", error=str(exc)[-800:], finished_at=_now())
    finally:
        _RUNNING.pop(job_id, None)


def _is_safe(proposal: dict[str, Any], proposals: list[dict[str, Any]]) -> bool:
    if proposal.get("risk") != "low" or proposal.get("needs_tts") or proposal.get("needs_llm"):
        return False
    by_id = {item["id"]: item for item in proposals}
    return all(dep in by_id and _is_safe(by_id[dep], proposals) for dep in proposal.get("depends_on") or [])


# --------------------------------------------------------------------------- apply


def start_apply(
    job_id: str,
    proposal_ids: list[str] | None,
    voice_settings: dict[str, Any],
    *,
    scope: str = "selected",
    background: bool = True,
) -> dict[str, Any]:
    review = load_review(job_id)
    if not review or not review.get("proposals"):
        raise LookupError("Chưa có đề xuất nào để áp dụng.")
    pending = [proposal for proposal in review["proposals"] if proposal.get("status") == "pending"]
    if scope == "all":
        ids = [proposal["id"] for proposal in pending]
    elif scope == "safe":
        ids = [proposal["id"] for proposal in pending if _is_safe(proposal, review["proposals"])]
    else:
        wanted = set(proposal_ids or [])
        ids = [proposal["id"] for proposal in pending if proposal["id"] in wanted]
    if not ids:
        raise ValueError("Không có đề xuất nào đang chờ trong lựa chọn này.")
    with _REGISTRY:
        if job_id in _RUNNING:
            raise RuntimeError("Đang rà soát hoặc áp dụng cho video này.")
        _RUNNING[job_id] = "apply"
    label = {"all": "Chấp nhận tất cả", "safe": "Chấp nhận các sửa an toàn"}.get(scope, "Chấp nhận đề xuất")
    _update(job_id, applying={"status": "running", "done": 0, "total": len(ids), "started_at": _now()})
    if background:
        threading.Thread(target=_apply_thread, args=(job_id, ids, voice_settings, label), daemon=True).start()
    else:
        _apply_thread(job_id, ids, voice_settings, label)
    return load_review(job_id) or {}


def _apply_thread(job_id: str, ids: list[str], voice_settings: dict[str, Any], label: str) -> None:
    try:
        _apply_run(job_id, ids, voice_settings, label=label)
    except Exception as exc:  # noqa: BLE001
        log.exception("Auto review apply failed for %s", job_id)
        _update(job_id, applying={"status": "failed", "error": str(exc)[-800:], "finished_at": _now()})
    finally:
        _RUNNING.pop(job_id, None)


def _expand(ids: list[str], proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Selected proposals plus the pending ones they depend on, in planning order."""
    by_id = {proposal["id"]: proposal for proposal in proposals}
    wanted: set[str] = set()

    def add(proposal_id: str) -> None:
        proposal = by_id.get(proposal_id)
        if not proposal or proposal_id in wanted or proposal.get("status") != "pending":
            return
        wanted.add(proposal_id)
        for dep in proposal.get("depends_on") or []:
            add(dep)

    for proposal_id in ids:
        add(proposal_id)
    return [proposal for proposal in proposals if proposal["id"] in wanted]


def _apply_run(job_id: str, ids: list[str], voice_settings: dict[str, Any], *, label: str, auto: bool = False) -> None:
    review = load_review(job_id) or {}
    chosen = _expand(ids, review.get("proposals") or [])
    cache: dict[str, float | None] = {}
    with editor.workspace_lock(job_id):
        workspace = editor.load_workspace(job_id)
        draft = editor.save_draft(workspace, DRAFT_NAME)
        views, layout, issues = analyze(workspace, cache)
        score_before = det.summarize(views, issues)["score"]
        penalties_before = _per_cue(issues, views)
    outcomes: dict[str, dict[str, Any]] = {}
    for done, proposal in enumerate(chosen, start=1):
        try:
            outcomes[proposal["id"]] = _apply_one(job_id, proposal, voice_settings, cache)
        except Exception as exc:  # noqa: BLE001 — one failing fix must not stop the batch.
            log.warning("Auto review proposal %s failed: %s", proposal["id"], exc)
            outcomes[proposal["id"]] = {"status": "failed", "error": str(exc)[-400:]}
        _update(job_id, applying={"status": "running", "done": done, "total": len(chosen)})

    with editor.workspace_lock(job_id):
        workspace = editor.load_workspace(job_id)
        views, layout, issues = analyze(workspace, cache)
    current = det.summarize(views, issues)
    penalties_after = _per_cue(issues, views)
    common = penalties_before.keys() & penalties_after.keys()
    batch = {
        "id": f"b-{uuid.uuid4().hex[:8]}",
        "label": label,
        "auto": auto,
        "draft_id": draft["id"],
        "created_at": _now(),
        "proposal_ids": [proposal_id for proposal_id, outcome in outcomes.items() if outcome["status"] == "applied"],
        "attempted_ids": list(outcomes),
        "counts": {
            status: sum(1 for outcome in outcomes.values() if outcome["status"] == status)
            for status in ("applied", "stale", "failed", "reverted_worse")
        },
        "score_before": score_before,
        "score_after": current["score"],
        "cues_improved": sum(1 for key in common if penalties_after[key] < penalties_before[key] - 0.01),
        "cues_worse": sum(1 for key in common if penalties_after[key] > penalties_before[key] + 0.01),
        "reverted": False,
    }
    with _lock(job_id):
        review = load_review(job_id) or {"job_id": job_id}
        for proposal in review.get("proposals") or []:
            outcome = outcomes.get(proposal["id"])
            if outcome:
                proposal.update(outcome)
                if outcome["status"] == "applied":
                    proposal["batch_id"] = batch["id"]
        review.setdefault("batches", []).append(batch)
        review["summary_current"] = current
        review["issues"] = [issue.to_dict() for issue in issues]
        review["applying"] = {"status": "done", "done": len(chosen), "total": len(chosen), "finished_at": _now(), "batch_id": batch["id"]}
        save_review(review)


def _fingerprint_ok(segment: dict[str, Any] | None, snap: dict[str, Any], timing: bool) -> bool:
    if segment is None or segment.get("text", "") != snap.get("text", ""):
        return False
    if not timing:
        return True
    if abs(float(segment["start"]) - snap["start"]) > 0.02 or abs(float(segment["end"]) - snap["end"]) > 0.02:
        return False
    if snap.get("voiced") and segment.get("audio_url"):
        if abs(float(segment.get("audio_start", segment["start"])) - (snap.get("voice_start") or 0)) > 0.02:
            return False
        if abs(float(segment.get("audio_duration") or 0) - (snap.get("voice_len") or 0)) > 0.03:
            return False
    return True


def _apply_one(job_id: str, proposal: dict[str, Any], voice_settings: dict[str, Any], cache: dict[str, float | None]) -> dict[str, Any]:
    # Absolute timings only make sense on the exact state they were planned on; text
    # based ops (split at a word, move words, rewrite…) just need the same text.
    timing = any(op["op"] in ("sub_timing", "voice") for op in proposal["ops"])
    with editor.workspace_lock(job_id):
        workspace = editor.load_workspace(job_id)
        by_id = {segment["id"]: segment for segment in workspace["segments"]}
        for snap in proposal.get("before") or []:
            if not _fingerprint_ok(by_id.get(snap["id"]), snap, timing):
                return {"status": "stale", "error": "Câu đã thay đổi sau khi rà soát."}
        backup = copy.deepcopy(workspace["segments"])
        views, layout, issues = analyze(workspace, cache)
        ids = _neighbourhood(views, set(proposal["cue_ids"]) | {op["id"] for op in proposal["ops"]})
        penalty_before = _penalty(issues, ids)
        speed_before = _speed(views, proposal["cue_ids"][0])
        tts_ids = _execute(workspace, proposal["ops"], voice_settings)
        for round_number in range(MAX_ROUNDS):
            for cue_id in tts_ids:
                _refit(workspace, cue_id, cache)
            if proposal["kind"] != "rewrite" or round_number + 1 >= MAX_ROUNDS:
                break
            # Round 2: the new take is still rushed — ask for a tighter line once more.
            views, layout, issues = analyze(workspace, cache)
            cue_id = proposal["cue_ids"][0]
            still = [issue for issue in issues if issue.cue_id == cue_id and issue.type == "voice_fast"]
            if not still:
                break
            tighter = _tighter_text(workspace, views, layout, cue_id, proposal)
            if not tighter:
                break
            tts_ids = _execute(workspace, [{"op": "text", "id": cue_id, "text": tighter}, {"op": "tts", "id": cue_id}], voice_settings)
            proposal.setdefault("rounds", []).append({"text": tighter})
        views, layout, issues = analyze(workspace, cache)
        wanted = set(ids) | _new_ids(backup, workspace)
        after_ids = [cue.id for cue in views if cue.id in wanted]
        penalty_after = _penalty(issues, after_ids)
        speed_after = _speed(views, proposal["cue_ids"][0])
        # Never worse: the window must score better, or at least equal without the line
        # getting faster (an overlap traded for a slightly faster take is a real gain).
        faster = speed_before is not None and speed_after is not None and speed_after > max(speed_before, planner.ACCEPT_MAX) + 0.02
        worse = penalty_after > penalty_before + 0.01 or (penalty_after >= penalty_before - 0.01 and faster)
        if worse:
            workspace["segments"] = backup
            editor.save_workspace(workspace)
            return {
                "status": "reverted_worse",
                "error": f"Kết quả thực tế không tốt hơn (điểm phạt {penalty_before} → {penalty_after}); đã giữ nguyên bản cũ.",
                "result": {"penalty_before": penalty_before, "penalty_after": penalty_after, "speed_before": speed_before, "speed_after": speed_after},
            }
        editor.save_workspace(workspace)
        return {
            "status": "applied",
            "applied_at": _now(),
            "result": {"penalty_before": penalty_before, "penalty_after": penalty_after, "speed_before": speed_before, "speed_after": speed_after},
        }


def _per_cue(issues: list[det.Issue], views: list[det.CueView]) -> dict[str, float]:
    penalties = {cue.id: 0.0 for cue in views}
    for issue in issues:
        penalties[issue.cue_id] = penalties.get(issue.cue_id, 0.0) + det.SEVERITY_WEIGHT[issue.severity]
    return penalties


def _new_ids(before: list[dict[str, Any]], workspace: dict[str, Any]) -> set[str]:
    known = {segment["id"] for segment in before}
    return {segment["id"] for segment in workspace["segments"] if segment["id"] not in known}


def _neighbourhood(views: list[det.CueView], ids: set[str]) -> list[str]:
    positions = [k for k, cue in enumerate(views) if cue.id in ids]
    if not positions:
        return []
    return [cue.id for cue in views[max(0, min(positions) - 1) : max(positions) + 2]]


def _penalty(issues: list[det.Issue], ids: list[str]) -> float:
    wanted = set(ids)
    return round(det.cue_penalty([issue for issue in issues if issue.cue_id in wanted]), 3)


def _speed(views: list[det.CueView], cue_id: str) -> float | None:
    cue = next((item for item in views if item.id == cue_id), None)
    return round(cue.speed, 3) if cue and cue.speed else None


def _execute(workspace: dict[str, Any], ops: list[dict[str, Any]], voice_settings: dict[str, Any]) -> list[str]:
    """Run ops through the editor functions; returns the ids that got a new take."""
    tts_ids: list[str] = []
    for op in ops:
        kind, cue_id = op["op"], op["id"]
        if kind == "sub_timing":
            editor.patch_workspace(workspace, [{"id": cue_id, "start": op["start"], "end": op["end"]}], None)
        elif kind == "voice":
            editor.patch_workspace(workspace, [{"id": cue_id, "audio_start": op["audio_start"], "audio_length": op["audio_length"]}], None)
        elif kind == "text":
            editor.patch_workspace(workspace, [{"id": cue_id, "text": op["text"]}], None)
        elif kind == "split":
            editor.split_segment(workspace, cue_id, at_char=int(op["at_char"]))
        elif kind == "merge":
            position = editor._position(workspace["segments"], cue_id)
            following = workspace["segments"][position + 1] if position + 1 < len(workspace["segments"]) else None
            if not following or (op.get("next_id") and following["id"] != op["next_id"]):
                raise ValueError("Câu kế tiếp đã thay đổi, không gộp được.")
            editor.merge_with_next(workspace, cue_id)
        elif kind == "delete":
            editor.delete_segment(workspace, cue_id)
        elif kind == "tts":
            if not voice_settings:
                raise RuntimeError("Thiếu cấu hình giọng để tạo lại audio.")
            editor.regenerate_segment_audio(workspace, cue_id, **voice_settings)
            tts_ids.append(cue_id)
        else:
            raise ValueError(f"Thao tác không hỗ trợ: {kind}")
    return tts_ids


def _refit(workspace: dict[str, Any], cue_id: str, cache: dict[str, float | None]) -> None:
    """Place a freshly voiced cue with the planner's ladder (borrow silence → … → speed)."""
    views, layout, _ = analyze(workspace, cache)
    _, ops, _ = planner.refit(views, cue_id, layout.duration)
    updates: dict[str, dict[str, Any]] = {}
    for op in ops:
        update = updates.setdefault(op["id"], {"id": op["id"]})
        if op["op"] == "sub_timing":
            update.update(start=op["start"], end=op["end"])
        elif op["op"] == "voice":
            update.update(audio_start=op["audio_start"], audio_length=op["audio_length"])
    if updates:
        editor.patch_workspace(workspace, list(updates.values()), None)


def _tighter_text(workspace: dict[str, Any], views: list[det.CueView], layout: det.Layout, cue_id: str, proposal: dict[str, Any]) -> str | None:
    position = next((k for k, cue in enumerate(views) if cue.id == cue_id), None)
    if position is None:
        return None
    cue = views[position]
    solved = planner._solve_voice(views, position, set(), layout.duration)
    speed = solved[1] if solved else (cue.speed or 1.3)
    syllables = cue.syllables
    original = (proposal.get("rewrite") or {}).get("syllables") or syllables
    target = max(int(original * planner.MAX_CONDENSE), int(syllables * planner.COMFORT_MAX / max(planner.COMFORT_MAX, speed)))
    if target >= syllables:
        return None
    segment = next(item for item in workspace["segments"] if item["id"] == cue_id)
    item = {
        "id": cue_id,
        "mode": "condense",
        "text": cue.text,
        "syllables": syllables,
        "target_syllables": target,
        "source": segment.get("source_text", ""),
        "previous": views[position - 1].text if position else "",
        "next": views[position + 1].text if position + 1 < len(views) else "",
    }
    texts, _ = llm_module.rewrite([item], workspace.get("source_language") or "auto", completers_factory())
    return texts.get(cue_id)


# --------------------------------------------------------------------------- reject / revert / preview


def reject(job_id: str, ids: list[str]) -> dict[str, Any]:
    with _lock(job_id):
        review = load_review(job_id)
        if not review:
            raise LookupError("Chưa có kết quả rà soát.")
        rejected = set(ids)
        changed = True
        while changed:  # Proposals built on top of a rejected one go too.
            changed = False
            for proposal in review.get("proposals") or []:
                if proposal["id"] not in rejected and proposal.get("status") == "pending" and rejected & set(proposal.get("depends_on") or []):
                    rejected.add(proposal["id"])
                    changed = True
        for proposal in review.get("proposals") or []:
            if proposal["id"] in rejected and proposal.get("status") == "pending":
                proposal["status"] = "rejected"
        return save_review(review)


def revert_batch(job_id: str, batch_id: str) -> dict[str, Any]:
    if job_id in _RUNNING:
        raise RuntimeError("Đang rà soát hoặc áp dụng cho video này.")
    with _lock(job_id):
        review = load_review(job_id)
        batch = next((item for item in (review or {}).get("batches", []) if item["id"] == batch_id), None)
        if not review or not batch:
            raise LookupError("Không tìm thấy lần áp dụng này.")
        if batch.get("reverted"):
            raise ValueError("Lần áp dụng này đã được hoàn tác.")
    with editor.workspace_lock(job_id):
        workspace = editor.restore_draft(editor.load_workspace(job_id), batch["draft_id"])
        views, layout, issues = analyze(workspace)
    with _lock(job_id):
        review = load_review(job_id)
        attempted = set(batch.get("attempted_ids") or batch.get("proposal_ids") or [])
        for proposal in review.get("proposals") or []:
            # Back on the state they were planned for: applied, stale or not-better ones are valid again.
            if proposal["id"] in attempted and proposal.get("status") in {"applied", "stale", "failed", "reverted_worse"}:
                proposal["status"] = "pending"
                for key in ("batch_id", "error", "result", "applied_at"):
                    proposal.pop(key, None)
                proposal["reverted_at"] = _now()
        for item in review.get("batches", []):
            if item["id"] == batch_id:
                item["reverted"] = True
                item["reverted_at"] = _now()
        review["summary_current"] = det.summarize(views, issues)
        review["issues"] = [issue.to_dict() for issue in issues]
        return save_review(review)


def preview_rewrite(job_id: str, proposal_id: str, voice_settings: dict[str, Any]) -> dict[str, Any]:
    """Synthesize a rewrite's new line (not applied) so it can be heard before accepting."""
    from .. import tts
    from ..zerotts_tts import is_zerotts_voice

    review = load_review(job_id)
    proposal = next((item for item in (review or {}).get("proposals", []) if item["id"] == proposal_id), None)
    if not proposal:
        raise LookupError("Không tìm thấy đề xuất.")
    text = next((op["text"] for op in proposal["ops"] if op["op"] == "text"), None)
    if not text:
        raise ValueError("Đề xuất này không đổi lời thoại.")
    workdir = editor.workspace_dir(job_id) / "review-previews"
    workdir.mkdir(exist_ok=True)
    label = f"{proposal_id}-{uuid.uuid4().hex[:4]}"
    output = workdir / f"{label}.mp3"
    provider = "zerotts" if is_zerotts_voice(voice_settings["voice"]) else voice_settings["provider"]
    with tts.tts_provider_override(provider):
        tts._save_tts_segment(
            text,
            tts._resolve_voice(voice_settings["voice"], voice_settings["target_language"]),
            output,
            tts._normalize_rate(voice_settings["voice_rate"]),
            workdir,
            label,
            None,
            voice_settings.get("voice_overrides"),
        )
    natural = tts._probe_duration(output)
    after = (proposal.get("after") or [{}])[0]
    slot = after.get("voice_len") or natural
    return {"url": editor._storage_url(output), "natural": round(natural, 3), "slot": slot, "speed": round(natural / slot, 3) if slot else None}
