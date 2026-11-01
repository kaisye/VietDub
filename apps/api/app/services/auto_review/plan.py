"""Auto review — planning: turn detected issues into concrete, least-invasive fixes.

Each problem cue is looked at together with its neighbourhood (±2 voiced cues and
the silence around them). Fixes are tried from least to most invasive:

1. ``borrow_silence``  — move/extend only this voice into free time around it;
2. ``shift_neighbors`` — also slide the neighbours (same speed) within their sync tolerance;
3. ``redistribute``    — let the whole window share the time at a comfortable speed (≤1.15×);
4. ``speed``           — accept up to 1.25× for this cue and propose a shorter translation;
5. subtitle fixes      — extend into silence, split long cues at natural boundaries,
   merge orphans, move a dangling word to the cue it belongs to;
6. ``rewrite``         — condense / expand the Vietnamese line (LLM, filled in by the runner).

Plans never create overlaps: every voice keeps ``GAP`` seconds to the next one and
every subtitle ``SUB_GAP`` to the next. Pure module — no I/O.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from ..editor import split_time
from . import vi_text
from .detect import (
    DEFAULT_THRESHOLDS,
    CueView,
    Issue,
    Layout,
    Thresholds,
    cue_penalty,
    detect,
    issues_by_cue,
    natural_rate,
)

GAP = 0.05  # Silence kept between two voices.
SUB_GAP = 0.04  # Between two subtitles.
LEAD = 0.3  # A voice may start this much before its subtitle…
LAG = 0.4  # …or this much after it.
COMFORT_MAX = 1.15  # Comfortable speed-up; users tolerate up to ACCEPT_MAX.
ACCEPT_MAX = 1.25
HARD_MAX = 2.0  # editor.MAX_TEMPO
PIPELINE_MAX = 1.6  # tts._fit_audio_to_slot default cap (AETHER_EDGE_MAX_TEMPO).
CPS_TARGET = 17.0
MAX_SUB_EXTEND = 2.0
MAX_SUB_LEAD = 0.4
MAX_CONDENSE = 0.7  # A rewrite keeps at least 70 % of the syllables.

VOICE_FIX_TYPES = {"voice_fast", "voice_overlap", "voice_offset", "voice_slow"}


@dataclass
class Proposal:
    kind: str
    cue_ids: list[str]
    issue_types: list[str]
    risk: str  # "low": timing / speed / subtitle cut only. "medium": text or new voice.
    ops: list[dict[str, Any]]
    before: list[dict[str, Any]]
    after: list[dict[str, Any]]
    confidence: float
    needs_tts: bool = False
    needs_llm: bool = False
    predicted: dict[str, Any] = field(default_factory=dict)
    rewrite: dict[str, Any] | None = None
    note: str = ""
    id: str = field(default_factory=lambda: f"p-{uuid.uuid4().hex[:8]}")
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def snapshot(cue: CueView) -> dict[str, Any]:
    speed = cue.speed
    return {
        "id": cue.id,
        "start": round(cue.start, 3),
        "end": round(cue.end, 3),
        "text": cue.text,
        "voiced": cue.voiced,
        "voice_start": round(cue.voice_start, 3) if cue.voiced else None,
        "voice_len": round(cue.voice_len, 3) if cue.voiced else None,
        "natural": round(cue.natural, 3) if cue.natural else None,
        "speed": round(speed, 3) if speed else None,
        "cps": round(len(cue.text.strip()) / cue.duration, 2) if cue.duration > 0 else None,
    }


# --------------------------------------------------------------------------- voice layout


def _voiced_positions(cues: list[CueView]) -> list[int]:
    return [index for index, cue in enumerate(cues) if cue.voiced and cue.natural]


def layout_voices(
    units: list[CueView],
    speeds: list[float],
    left: float,
    right: float,
) -> list[tuple[float, float]] | None:
    """Place consecutive voices between ``left`` and ``right`` at the given speeds.

    Each voice starts as close to its subtitle as possible, within [−LEAD, +LAG]
    (widened to include where it is now). Returns (start, length) per unit or
    None when they cannot fit without overlapping.
    """
    lengths = [unit.natural / speed for unit, speed in zip(units, speeds)]
    bounds = []
    for unit in units:
        low = min(unit.start - LEAD, unit.voice_start)
        high = max(unit.start + LAG, unit.voice_start)
        bounds.append((max(0.0, low), high))
    latest: list[float] = [0.0] * len(units)
    latest_end = right - GAP
    for k in range(len(units) - 1, -1, -1):
        latest[k] = min(bounds[k][1], latest_end - lengths[k])
        latest_end = latest[k] - GAP
    placed: list[tuple[float, float]] = []
    previous_end = left
    for k, unit in enumerate(units):
        earliest = max(bounds[k][0], previous_end + GAP if (k or left > 0) else previous_end)
        if earliest > latest[k] + 1e-6:
            return None
        start = min(max(unit.start, earliest), latest[k])
        placed.append((round(start, 3), round(lengths[k], 3)))
        previous_end = start + lengths[k]
    return placed


def _min_speed(fn, low: float, high: float, steps: int = 18) -> float | None:
    """Smallest speed in [low, high] for which ``fn(speed)`` is feasible (monotone)."""
    if fn(high) is None:
        return None
    if fn(low) is not None:
        return low
    for _ in range(steps):
        middle = (low + high) / 2
        if fn(middle) is None:
            low = middle
        else:
            high = middle
    return high


@dataclass
class _Window:
    units: list[CueView]
    target: int  # Index of the problem cue inside ``units``.
    left: float
    right: float


def _window(cues: list[CueView], voiced: list[int], position: int, radius: int, locked: set[str]) -> _Window:
    order = voiced.index(position)
    first = order
    while first > 0 and order - first < radius and cues[voiced[first - 1]].id not in locked:
        first -= 1
    last = order
    while last + 1 < len(voiced) and last - order < radius and cues[voiced[last + 1]].id not in locked:
        last += 1
    units = [cues[voiced[k]] for k in range(first, last + 1)]
    left = cues[voiced[first - 1]].voice_end if first > 0 else 0.0
    right = cues[voiced[last + 1]].voice_start if last + 1 < len(voiced) else float("inf")
    return _Window(units, order - first, left, right)


def _solve_voice(
    cues: list[CueView],
    position: int,
    locked: set[str],
    duration: float,
    *,
    floor: float = 1.0,
    best_effort_max: float | None = None,
) -> tuple[str, float, list[CueView], list[tuple[float, float]]] | None:
    """Least invasive voice layout around ``cues[position]``: (level, speed, units, placement).

    Levels are tried in order; inside a level the target gets the lowest feasible
    speed first, then the neighbours the lowest speed-up that still fits.
    """
    voiced = _voiced_positions(cues)
    if position not in voiced:
        return None
    end = duration or float("inf")
    target = cues[position]
    current = target.speed or 1.0
    levels = [
        # (name, window radius, neighbours may speed up to, target ceiling)
        ("borrow_silence", 0, None, COMFORT_MAX),
        ("shift_neighbors", 2, None, COMFORT_MAX),
        ("redistribute", 2, COMFORT_MAX, COMFORT_MAX),
        ("speed", 2, COMFORT_MAX, ACCEPT_MAX),
        # Never faster than it already is: fixes overlaps / sync only.
        ("best_effort", 2, COMFORT_MAX, max(floor, min(HARD_MAX, best_effort_max or current))),
    ]
    for level, radius, neighbor_cap, ceiling in levels:
        window = _window(cues, voiced, position, radius, locked - {target.id})
        right = min(window.right, end)

        def run(target_speed: float, cap: float | None, window=window, right=right):
            speeds = [
                target_speed if k == window.target else max(unit.speed or 1.0, cap or 0.0)
                for k, unit in enumerate(window.units)
            ]
            return layout_voices(window.units, speeds, window.left, right)

        if ceiling < floor:
            continue
        speed = _min_speed(lambda value: run(value, neighbor_cap), floor, ceiling)
        if speed is None:
            continue
        cap = neighbor_cap
        if neighbor_cap is not None:
            cap = _min_speed(lambda value: run(speed, value), 1.0, neighbor_cap)
        return level, speed, window.units, run(speed, cap)
    return None


# --------------------------------------------------------------------------- helpers


def _apply_voice_layout(cues: list[CueView], units: list[CueView], placement: list[tuple[float, float]]) -> dict[str, CueView]:
    changed: dict[str, CueView] = {}
    for unit, (start, length) in zip(units, placement):
        if abs(unit.voice_start - start) > 0.01 or abs(unit.voice_len - length) > 0.01:
            changed[unit.id] = unit.copy(voice_start=start, voice_len=length)
    return changed


def _follow_subtitles(cues: list[CueView], changed: dict[str, CueView]) -> dict[str, CueView]:
    """Let subtitles of re-placed voices cover them, without touching the neighbours' subtitles."""
    result = dict(changed)
    index = {cue.id: position for position, cue in enumerate(cues)}
    for cue_id, cue in changed.items():
        position = index[cue_id]
        previous = cues[position - 1] if position else None
        following = cues[position + 1] if position + 1 < len(cues) else None
        start, end = cue.start, cue.end
        if cue.voice_start < start:
            start = min(start, max(cue.voice_start, (previous.end + SUB_GAP) if previous else 0.0, start - MAX_SUB_LEAD))
        if cue.voice_end > end and following is not None and following.shared:
            pass  # The split-off part shows the rest of this voice.
        elif cue.voice_end > end:
            limit = (following.start - SUB_GAP) if following else cue.voice_end
            end = max(end, min(cue.voice_end, limit))
        if abs(start - cue.start) > 0.005 or abs(end - cue.end) > 0.005:
            result[cue_id] = cue.copy(start=round(start, 3), end=round(end, 3))
    return result


def _replace(cues: list[CueView], changed: dict[str, CueView]) -> list[CueView]:
    return [changed.get(cue.id, cue) for cue in cues]


def _ops_for(original: dict[str, CueView], changed: dict[str, CueView]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for cue_id, cue in changed.items():
        before = original[cue_id]
        if abs(before.start - cue.start) > 0.005 or abs(before.end - cue.end) > 0.005:
            ops.append({"op": "sub_timing", "id": cue_id, "start": round(cue.start, 3), "end": round(cue.end, 3)})
        if cue.voiced and (abs(before.voice_start - cue.voice_start) > 0.005 or abs(before.voice_len - cue.voice_len) > 0.005):
            ops.append({"op": "voice", "id": cue_id, "audio_start": round(cue.voice_start, 3), "audio_length": round(cue.voice_len, 3)})
    return ops


def _window_ids(cues: list[CueView], ids: set[str], pad: int = 1) -> list[str]:
    positions = [position for position, cue in enumerate(cues) if cue.id in ids]
    if not positions:
        return []
    low, high = max(0, min(positions) - pad), min(len(cues), max(positions) + pad + 1)
    return [cue.id for cue in cues[low:high]]


def _penalty(issues: list[Issue], ids: list[str]) -> float:
    wanted = set(ids)
    return round(cue_penalty([issue for issue in issues if issue.cue_id in wanted]), 3)


def _types(issues: list[Issue], ids: list[str]) -> list[str]:
    wanted = set(ids)
    return sorted({issue.type for issue in issues if issue.cue_id in wanted})


# --------------------------------------------------------------------------- planner


@dataclass
class Plan:
    proposals: list[Proposal]
    predicted_cues: list[CueView]


SUBTITLE_FIX_TYPES = {"sub_empty", "sub_fast", "sub_short", "sub_long", "sub_lines", "sub_bad_break", "sub_orphan", "sub_fragment", "dead_air", "voice_missing"}


def plan(
    cues: list[CueView],
    layout: Layout,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    issues: list[Issue] | None = None,
    allow_text: bool = True,
    rounds: int = 2,
) -> Plan:
    """Proposals for every issue that can be improved, applied virtually in order.

    Later proposals are planned on top of earlier ones; when one touches a cue an
    earlier proposal changed, it records that proposal in ``depends_on`` (applying
    it applies the dependency first, rejecting the dependency rejects it too).
    After a round, the predicted result is checked again: fixes change the
    neighbourhood (deleting a stray "..." exposes the orphan word before it).
    """
    planner = _Planner(cues, layout, thresholds, allow_text)
    current = detect(cues, layout, thresholds) if issues is None else issues
    for _ in range(max(1, rounds)):
        if not planner.run_round(current):
            break
        current = detect(planner.state, layout, thresholds)
    return Plan(planner.proposals, planner.state)


class _Planner:
    def __init__(self, cues: list[CueView], layout: Layout, thresholds: Thresholds, allow_text: bool):
        self.state = list(cues)
        self.layout = layout
        self.thresholds = thresholds
        self.allow_text = allow_text
        self.rate = natural_rate(cues)
        self.proposals: list[Proposal] = []
        self.changed_by: dict[str, str] = {}
        self.attempted: set[tuple[str, frozenset[str], tuple[str, ...]]] = set()
        self.rewritten: set[str] = set()  # One text rewrite per cue.

    def commit(self, proposal: Proposal, new_state: list[CueView]) -> bool:
        if proposal.needs_llm:
            if proposal.cue_ids[0] in self.rewritten:
                return False
            self.rewritten.add(proposal.cue_ids[0])
        touched = set(proposal.cue_ids) | {op["id"] for op in proposal.ops} | {
            op["next_id"] for op in proposal.ops if op.get("next_id")
        }
        proposal.depends_on = sorted({self.changed_by[cue_id] for cue_id in touched if cue_id in self.changed_by})
        if proposal.ops:
            for cue_id in touched:
                self.changed_by[cue_id] = proposal.id
        self.state = new_state
        self.proposals.append(proposal)
        return True

    def run_round(self, issues: list[Issue]) -> int:
        severity_rank = {"error": 0, "warning": 1, "info": 2}
        grouped = issues_by_cue(issues)
        order = sorted(
            grouped.items(),
            key=lambda item: (min(severity_rank[issue.severity] for issue in item[1]), _start_of(self.state, item[0])),
        )
        voice_locked: set[str] = set()  # Cues inside a voice window planned this round.
        sub_locked: set[str] = set()  # Cues whose subtitle a fix changed this round.
        made = 0
        for cue_id, cue_issues in order:
            types = {issue.type for issue in cue_issues}
            position = _position(self.state, cue_id)
            if position is None:
                continue
            # Retry a cue only when its issues or its neighbours changed (e.g. a deleted "...").
            neighbours = tuple(cue.id for cue in self.state[max(0, position - 1) : position + 2])
            key = (cue_id, frozenset(types), neighbours)
            if key in self.attempted:
                continue
            self.attempted.add(key)
            if types & VOICE_FIX_TYPES and cue_id not in voice_locked:
                for proposal, new_state, lock in _plan_voice(
                    self.state, position, types, voice_locked, self.layout, self.thresholds, self.rate, self.allow_text
                ):
                    if self.commit(proposal, new_state):
                        voice_locked.update(lock)
                        made += 1
                position = _position(self.state, cue_id)
                if position is None:
                    continue
            if not types & SUBTITLE_FIX_TYPES or cue_id in sub_locked:
                continue
            for proposal, new_state, lock in _plan_subtitle(
                self.state, position, types, sub_locked, self.layout, self.thresholds, self.rate, self.allow_text
            ):
                if self.commit(proposal, new_state):
                    sub_locked.update(lock)
                    made += 1
        return made


def _position(cues: list[CueView], cue_id: str) -> int | None:
    return next((k for k, cue in enumerate(cues) if cue.id == cue_id), None)


def _start_of(cues: list[CueView], cue_id: str) -> float:
    return next((cue.start for cue in cues if cue.id == cue_id), 0.0)


def _evaluate(
    state: list[CueView], new_state: list[CueView], ids: list[str], layout: Layout, thresholds: Thresholds
) -> tuple[float, float, list[str], list[str]]:
    window = _window_ids(state, set(ids), pad=1)
    wanted = set(window) | set(ids)
    window_new = [cue.id for cue in new_state if cue.id in wanted]
    before = detect(_slice(state, window), layout, thresholds)
    after = detect(_slice(new_state, window_new), layout, thresholds)
    return _penalty(before, window), _penalty(after, window_new), _types(before, ids), _types(after, ids)


def _slice(cues: list[CueView], ids: list[str]) -> list[CueView]:
    """The cues in ``ids`` plus one more on each side so edge checks still see their neighbours."""
    wanted = set(ids)
    positions = [k for k, cue in enumerate(cues) if cue.id in wanted]
    if not positions:
        return []
    return cues[max(0, min(positions) - 1) : max(positions) + 2]


def _plan_voice(
    state: list[CueView],
    position: int,
    types: set[str],
    locked: set[str],
    layout: Layout,
    thresholds: Thresholds,
    rate: float,
    allow_text: bool,
) -> list[tuple[Proposal, list[CueView], set[str]]]:
    target = state[position]
    if not target.voiced or not target.natural:
        return []
    # Natural speed (1.0) is the floor: a stretched (slow) take is brought back to it.
    solved = _solve_voice(state, position, locked, layout.duration)
    results: list[tuple[Proposal, list[CueView], set[str]]] = []
    by_id = {cue.id: cue for cue in state}
    if solved:
        level, speed, units, placement = solved
        changed = _follow_subtitles(state, _apply_voice_layout(state, units, placement))
        if changed:
            new_state = _replace(state, changed)
            ids = list(changed)
            before_pen, after_pen, before_types, after_types = _evaluate(state, new_state, ids, layout, thresholds)
            if after_pen < before_pen - 0.01:
                confidence = {"borrow_silence": 0.95, "shift_neighbors": 0.9, "redistribute": 0.85, "speed": 0.7, "best_effort": 0.55}[level]
                window_ids = [unit.id for unit in units]
                proposal = Proposal(
                    kind=level,
                    cue_ids=sorted(set(ids) | {target.id}, key=lambda cue_id: _start_of(state, cue_id)),
                    issue_types=sorted(types & (VOICE_FIX_TYPES | {"dead_air"})),
                    risk="low",
                    ops=_ops_for(by_id, changed),
                    before=[snapshot(by_id[cue_id]) for cue_id in window_ids if cue_id in changed or cue_id == target.id],
                    after=[snapshot(changed.get(cue_id, by_id[cue_id])) for cue_id in window_ids if cue_id in changed or cue_id == target.id],
                    confidence=confidence,
                    predicted={
                        "speed_before": _round(target.speed),
                        "speed_after": _round(changed.get(target.id, target).speed),
                        "penalty_before": before_pen,
                        "penalty_after": after_pen,
                        "issues_before": before_types,
                        "issues_after": after_types,
                    },
                )
                results.append((proposal, new_state, set(window_ids)))
                state = new_state
                target = changed.get(target.id, target)
        needs_text = level in {"speed", "best_effort"} or (target.speed or 1.0) > COMFORT_MAX
    else:
        needs_text = True
    if needs_text and allow_text and ("voice_fast" in types or (target.speed or 1.0) > COMFORT_MAX):
        rewrite = _rewrite_proposal(state, state.index(target), "condense", rate, layout, thresholds, locked)
        if rewrite:
            results.append((rewrite, state, set()))
    return results


def _rewrite_proposal(
    state: list[CueView],
    position: int,
    mode: str,
    rate: float,
    layout: Layout,
    thresholds: Thresholds,
    locked: set[str],
) -> Proposal | None:
    """A text rewrite placeholder; the runner asks the LLM and fills ``ops`` / ``after``."""
    cue = state[position]
    syllables = cue.syllables
    if syllables < 4:
        return None
    if mode == "condense":
        # How long may the take be? What the comfortable layout leaves for it.
        solved = _solve_voice(state, position, locked, layout.duration)
        speed = solved[1] if solved else (cue.speed or 1.4)
        ratio = max(MAX_CONDENSE, COMFORT_MAX / max(COMFORT_MAX, speed))
        target = max(3, int(syllables * ratio))
        if target >= syllables:
            return None
        confidence = 0.6
    else:
        covered = max(0.3, min(cue.end, cue.voice_end) - max(cue.start, cue.voice_start))
        room = cue.duration * 0.85
        target = min(int(syllables * 1.6), int(syllables * room / covered))
        if target <= syllables + 1:
            return None
        confidence = 0.45
    previous = state[position - 1].text if position else ""
    following = state[position + 1].text if position + 1 < len(state) else ""
    return Proposal(
        kind="rewrite",
        cue_ids=[cue.id],
        issue_types=["voice_fast"] if mode == "condense" else ["dead_air"],
        risk="medium",
        ops=[],
        before=[snapshot(cue)],
        after=[],
        confidence=confidence,
        needs_tts=True,
        needs_llm=True,
        rewrite={
            "mode": mode,
            "text": cue.text,
            "syllables": syllables,
            "target_syllables": target,
            "previous": previous,
            "next": following,
            "natural_rate": rate,
        },
        predicted={"speed_before": _round(cue.speed)},
    )


def _plan_subtitle(
    state: list[CueView],
    position: int,
    types: set[str],
    locked: set[str],
    layout: Layout,
    thresholds: Thresholds,
    rate: float,
    allow_text: bool,
) -> list[tuple[Proposal, list[CueView], set[str]]]:
    cue = state[position]
    by_id = {item.id: item for item in state}
    candidates: list[tuple[str, dict[str, CueView], list[dict[str, Any]], str, float, bool, list[str] | None]] = []

    # 0. Punctuation-only cue: drop it (its "voice" is a breath of silence).
    if "sub_empty" in types and cue.id not in locked:
        proposal = Proposal(
            kind="delete",
            cue_ids=[cue.id],
            issue_types=["sub_empty"],
            risk="low",
            ops=[{"op": "delete", "id": cue.id}],
            before=[snapshot(cue)],
            after=[],
            confidence=0.9,
            predicted={"issues_before": ["sub_empty"], "issues_after": []},
        )
        return [(proposal, [item for item in state if item.id != cue.id], {cue.id})]

    # 1. Extend into the silence around a too-short / too-fast cue.
    if types & {"sub_fast", "sub_short"} and cue.id not in locked:
        extended = _extend_subtitle(state, position, thresholds)
        if extended:
            candidates.append(("extend_subtitle", {cue.id: extended}, [], "low", 0.95, False, None))

    # 2. Split a long cue at its most natural boundary (the voice stays on the first part).
    if types & {"sub_long", "sub_lines", "sub_bad_break"} and cue.id not in locked:
        split = _split(cue, layout, thresholds)
        if split:
            at_char, first, second = split
            changed = {cue.id: first, second.id: second}
            ops = [{"op": "split", "id": cue.id, "at_char": at_char, "at_time": round(first.end, 3)}]
            candidates.append(("split", changed, ops, "low", 0.85, False, [cue.id]))

    # 3. Orphans / fragments: merge with, or move words to, the cue they belong to.
    if allow_text and types & {"sub_orphan", "sub_fragment", "sub_short", "sub_fast"} and cue.id not in locked:
        joined = _join(state, position, types, layout, thresholds)
        if joined:
            candidates.append(joined)

    # Every candidate is scored on the window; the biggest improvement wins, with a small
    # handicap for fixes that need a new take (text changes / merges).
    results = []
    best: tuple[float, Proposal, list[CueView], set[str]] | None = None
    for kind, changed, extra_ops, risk, confidence, needs_tts, split_ids in candidates:
        new_state = _apply_changes(state, changed, kind)
        ids = list(changed)
        before_pen, after_pen, before_types, after_types = _evaluate(state, new_state, ids, layout, thresholds)
        if after_pen >= before_pen - 0.01:
            continue
        ops = extra_ops or _ops_for(by_id, {key: value for key, value in changed.items() if key in by_id and value is not None})
        proposal = Proposal(
            kind=kind,
            cue_ids=[key for key in ids if key in by_id] or [cue.id],
            issue_types=sorted(types & {"sub_fast", "sub_short", "sub_long", "sub_lines", "sub_bad_break", "sub_orphan", "sub_fragment"}),
            risk=risk,
            ops=ops,
            before=[snapshot(by_id[key]) for key in ids if key in by_id],
            after=[snapshot(value) for value in changed.values() if value is not None],
            confidence=confidence,
            needs_tts=needs_tts,
            predicted={
                "penalty_before": before_pen,
                "penalty_after": after_pen,
                "issues_before": before_types,
                "issues_after": after_types,
            },
        )
        score = after_pen + (0.75 if risk != "low" else 0.0)
        if best is None or score < best[0]:
            best = (score, proposal, new_state, set(ids))
    if best is not None:
        results.append(best[1:])

    # 4. Dead air: the dub says too little for the time the speaker talks.
    if not results and allow_text and "dead_air" in types and cue.voiced and cue.id not in locked:
        covered = max(0.0, min(cue.end, cue.voice_end) - max(cue.start, cue.voice_start))
        severe = cue.duration - covered >= max(3.0, thresholds.dead_air_error_gap)
        if severe and (cue.speed or 1.0) <= 1.05:
            rewrite = _rewrite_proposal(state, position, "expand", rate, layout, thresholds, locked)
            if rewrite:
                results.append((rewrite, state, {cue.id}))

    # 5. Missing voice: generate it.
    if not results and "voice_missing" in types and cue.id not in locked:
        proposal = Proposal(
            kind="generate_voice",
            cue_ids=[cue.id],
            issue_types=["voice_missing"],
            risk="medium",
            ops=[{"op": "tts", "id": cue.id}],
            before=[snapshot(cue)],
            after=[snapshot(cue)],
            confidence=0.8,
            needs_tts=True,
        )
        results.append((proposal, state, {cue.id}))

    return results


def _apply_changes(state: list[CueView], changed: dict[str, CueView | None], kind: str) -> list[CueView]:
    known = {cue.id for cue in state}
    result: list[CueView] = []
    for cue in state:
        if cue.id not in changed:
            result.append(cue)
            continue
        if changed[cue.id] is not None:
            result.append(changed[cue.id])  # type: ignore[arg-type]
        if kind == "split":
            result.extend(value for key, value in changed.items() if key not in known and value is not None)
    return result


def _extend_subtitle(state: list[CueView], position: int, thresholds: Thresholds) -> CueView | None:
    cue = state[position]
    previous = state[position - 1] if position else None
    following = state[position + 1] if position + 1 < len(state) else None
    chars = len(cue.text.strip())
    wanted = max(thresholds.min_duration, chars / CPS_TARGET)
    if cue.duration >= wanted:
        return None
    end_limit = min(cue.end + MAX_SUB_EXTEND, (following.start - SUB_GAP) if following else cue.end + MAX_SUB_EXTEND)
    new_end = min(end_limit, cue.start + wanted)
    new_end = max(new_end, cue.end)
    new_start = cue.start
    if new_end - new_start < wanted:
        start_limit = max(cue.start - MAX_SUB_LEAD, (previous.end + SUB_GAP) if previous else 0.0)
        new_start = min(cue.start, max(start_limit, new_end - wanted))
    if new_end - new_start <= cue.duration + 0.05:
        return None
    return cue.copy(start=round(new_start, 3), end=round(new_end, 3))


def _split(cue: CueView, layout: Layout, thresholds: Thresholds) -> tuple[int, CueView, CueView] | None:
    text = cue.text.strip()
    at = vi_text.best_split_index(text)
    if at is None:
        return None
    left, right = text[:at].strip(), text[at:].strip()
    ratio = len(left) / max(1, len(left) + len(right))
    # Same cut as editor.split_segment: follow the voice, not the display slot.
    voiced = cue.voiced and cue.voice_len > 0
    middle = split_time(cue.start, cue.end, ratio, cue.voice_start if voiced else None, cue.voice_len if voiced else 0.0)
    if middle - cue.start < thresholds.min_duration or cue.end - middle < thresholds.min_duration:
        return None
    first = cue.copy(end=middle, text=left)
    second = CueView(
        id=f"{cue.id}~split",
        start=middle,
        end=cue.end,
        text=right,
        voiced=False,
        shared=cue.voiced or cue.shared,
        voice_start=middle,
    )
    return at, first, second


def _join(
    state: list[CueView], position: int, types: set[str], layout: Layout, thresholds: Thresholds
) -> tuple[str, dict[str, CueView], list[dict[str, Any]], str, float, bool, list[str] | None] | None:
    cue = state[position]
    previous = state[position - 1] if position else None
    following = state[position + 1] if position + 1 < len(state) else None
    capacity = layout.chars_per_line * layout.max_lines
    # Merge an orphan / tiny fragment into the cue it continues.
    pairs = []
    if following is not None and following.id and following.start - cue.end <= thresholds.fragment_join_gap and not vi_text.ends_sentence(cue.text):
        pairs.append((cue, following))
    if previous is not None and cue.start - previous.end <= thresholds.fragment_join_gap and not vi_text.ends_sentence(previous.text):
        pairs.append((previous, cue))
    small = len(cue.text.split()) <= thresholds.orphan_words or cue.duration < thresholds.min_duration
    for first, second in pairs:
        if not any(char.isalnum() for char in first.text) or not any(char.isalnum() for char in second.text):
            continue  # "..." is deleted, not merged.
        text = f"{first.text.strip()} {second.text.strip()}".strip()
        duration = second.end - first.start
        if not small or len(text) > capacity or duration > thresholds.max_duration:
            continue
        if len(text) / max(0.1, duration) > thresholds.cps_warn:
            continue
        own_voices = [item for item in (first, second) if item.voiced]
        merged = first.copy(
            end=second.end,
            text=text,
            voiced=bool(own_voices),
            voice_start=own_voices[0].voice_start if own_voices else first.start,
            voice_len=(own_voices[-1].voice_end - own_voices[0].voice_start) if own_voices else 0.0,
            natural=sum(item.natural or 0 for item in own_voices) or None,
        )
        needs_tts = len(own_voices) > 1 or (bool(own_voices) and second.voiced)
        ops = [{"op": "merge", "id": first.id, "next_id": second.id}]
        if needs_tts:
            ops.append({"op": "tts", "id": first.id})
        return ("merge", {first.id: merged, second.id: None}, ops, "medium" if needs_tts else "low", 0.75, needs_tts, None)  # type: ignore[dict-item]
    # The cue boundary cuts a phrase: move the word(s) to the side they belong to.
    if "sub_fragment" in types and following is not None:
        moved = _move_boundary_words(cue.text, following.text)
        if moved:
            left, right = moved
            if len(left) <= capacity and len(right) <= capacity:
                first = cue.copy(text=left)
                second = following.copy(text=right)
                ops = [{"op": "text", "id": cue.id, "text": left}, {"op": "text", "id": following.id, "text": right}]
                needs_tts = cue.voiced or following.voiced
                ops += [{"op": "tts", "id": item.id} for item in (cue, following) if item.voiced]
                return ("move_words", {cue.id: first, following.id: second}, ops, "medium", 0.7, needs_tts, None)
    return None


def _move_boundary_words(left_text: str, right_text: str) -> tuple[str, str] | None:
    """Re-cut two consecutive lines at a better word boundary near the current one.

    "…thiết kế nó rất | đơn giản…" → "…thiết kế nó | rất đơn giản…";
    "…mười đến hai | mươi năm tới" → "…mười đến hai mươi | năm tới".
    """
    left, right = left_text.split(), right_text.split()
    if len(left) < 2 or len(right) < 2:
        return None
    current, _ = vi_text.break_penalty(left[-1], right[0])
    best: tuple[int, int] | None = None
    for shift in (-2, -1, 1, 2):  # Negative: move words from the end of left to right.
        new_left = left[:shift] if shift < 0 else left + right[:shift]
        new_right = left[shift:] + right if shift < 0 else right[shift:]
        if len(new_left) < 2 or len(new_right) < 2:
            continue
        penalty, _ = vi_text.break_penalty(new_left[-1], new_right[0])
        score = penalty + abs(shift) * 6
        if penalty < current and (best is None or score < best[0]):
            best = (score, shift)
    if best is None:
        return None
    shift = best[1]
    new_left = left[:shift] if shift < 0 else left + right[:shift]
    new_right = left[shift:] + right if shift < 0 else right[shift:]
    return " ".join(new_left), " ".join(new_right)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


# --------------------------------------------------------------------------- after a new take


def refit(cues: list[CueView], cue_id: str, duration: float, locked: set[str] | None = None) -> tuple[list[CueView], list[dict[str, Any]], float | None]:
    """Re-place a cue whose take changed (new text / new voice) with the same strategy ladder.

    Returns (new state, ops, target speed) — ops are empty when nothing must move.
    """
    position = _position(cues, cue_id)
    if position is None:
        return cues, [], None
    # A new take has no "current" speed to protect: allow what the pipeline allows.
    solved = _solve_voice(cues, position, locked or set(), duration, best_effort_max=PIPELINE_MAX)
    if not solved:
        return cues, [], cues[position].speed
    _, speed, units, placement = solved
    changed = _follow_subtitles(cues, _apply_voice_layout(cues, units, placement))
    by_id = {cue.id: cue for cue in cues}
    return _replace(cues, changed), _ops_for(by_id, changed), speed


def predict_rewrite(cues: list[CueView], cue_id: str, text: str, duration: float) -> tuple[list[CueView], float | None]:
    """Predicted state after replacing a line and re-voicing it (take length scales with syllables)."""
    position = _position(cues, cue_id)
    if position is None:
        return cues, None
    cue = cues[position]
    old, new = max(1, cue.syllables), max(1, vi_text.syllable_count(text))
    natural = (cue.natural or cue.voice_len or 0.0) * new / old
    updated = cue.copy(text=text, natural=natural or None, voice_len=natural or cue.voice_len)
    state = _replace(cues, {cue_id: updated})
    if not updated.voiced or not updated.natural:
        return state, None
    state, _, speed = refit(state, cue_id, duration)
    return state, speed
