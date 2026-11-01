"""Auto review — detection: what is wrong with a dubbed video, cue by cue.

Pure functions over :class:`CueView` (a flattened editor segment plus the voice
take's natural length). No I/O: the runner builds the views, so this module is
cheap to test with synthetic cues.

Thresholds were calibrated on real ZeroTTS (int8) jobs, see ``Thresholds``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from statistics import median
from typing import Any, Callable

from . import vi_text

SEVERITY_WEIGHT = {"error": 3.0, "warning": 1.0, "info": 0.3}

VOICE_TYPES = ("voice_fast", "voice_slow", "voice_overlap", "voice_offset", "dead_air", "voice_missing")
SUBTITLE_TYPES = (
    "sub_empty",
    "sub_fast",
    "sub_short",
    "sub_long",
    "sub_lines",
    "sub_bad_break",
    "sub_orphan",
    "sub_fragment",
)


@dataclass(frozen=True)
class Thresholds:
    """Detection limits.

    Calibration (5 ready jobs, 1 097 cues, ZeroTTS int8): natural Vietnamese
    TTS speaks 4.0–4.7 syllables/s (median); the pipeline speeds takes up to
    1.6× so placed rates reach 6–8 syl/s. Speeds the user picked by hand in
    the editor (94 takes) have a median of 1.25× and p25 1.11×, so 1.25× is
    tolerated when the speech rate stays sane; 1.4× / 6.6 syl/s is clearly
    rushed. Subtitle CPS medians are 17–20 for these videos.
    """

    speed_warn: float = 1.25
    speed_error: float = 1.4
    rate_warn: float = 5.4  # Placed syllables per second.
    rate_error: float = 6.6
    rate_alone_warn: float = 6.2  # Rushed even when the measured speed factor looks fine.
    speed_slow_warn: float = 0.9
    speed_slow_error: float = 0.84
    overlap_tolerance: float = 0.08
    overlap_error: float = 0.3
    offset_late: float = 0.6  # Voice starts this long after its subtitle.
    offset_early: float = 0.45  # Voice starts this long before its subtitle.
    tail_over: float = 1.0  # Voice still speaking this long into the next subtitle.
    dead_air_min_cue: float = 2.5
    dead_air_coverage: float = 0.45
    dead_air_error_gap: float = 2.5
    cps_warn: float = 22.0
    cps_error: float = 30.0
    min_duration: float = 1.0
    min_duration_warn: float = 0.7
    max_duration: float = 7.0
    orphan_words: int = 2
    orphan_max_duration: float = 1.2
    fragment_join_gap: float = 0.5


DEFAULT_THRESHOLDS = Thresholds()


@dataclass
class CueView:
    id: str
    start: float
    end: float
    text: str
    voiced: bool = False  # Has its own (unmuted) take.
    shared: bool = False  # Split-off part voiced by another cue.
    voice_start: float = 0.0
    voice_len: float = 0.0
    natural: float | None = None  # Length of the unfitted take (speed 1.0).
    muted: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def voice_end(self) -> float:
        return self.voice_start + self.voice_len

    @property
    def speed(self) -> float | None:
        if not self.voiced or not self.natural or self.voice_len <= 0:
            return None
        return self.natural / self.voice_len

    @property
    def syllables(self) -> int:
        return vi_text.syllable_count(self.text)

    def copy(self, **changes: Any) -> "CueView":
        return replace(self, **changes)


@dataclass
class Layout:
    chars_per_line: int = 42
    max_lines: int = 2
    duration: float = 0.0
    wrap: Callable[[str, int, int], list[str]] | None = None


@dataclass
class Issue:
    cue_id: str
    type: str
    severity: str
    start: float
    end: float
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def category(self) -> str:
        return "voice" if self.type in VOICE_TYPES else "subtitle"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "category": self.category}


def _default_wrap(text: str, chars_per_line: int, max_lines: int) -> list[str]:
    from ..renderer import _wrap_text_lines

    return _wrap_text_lines(text, chars_per_line, max_lines)


def voice_rate(cue: CueView) -> float | None:
    """Placed speaking rate (syllables per second of the voice as heard)."""
    if not cue.voiced or cue.voice_len <= 0.2:
        return None
    return cue.syllables / cue.voice_len


def natural_rate(cues: list[CueView]) -> float:
    """Median syllables/s of the takes at speed 1.0 — how fast this voice naturally speaks."""
    rates = [
        cue.syllables / cue.natural
        for cue in cues
        if cue.voiced and cue.natural and cue.natural > 0.6 and cue.syllables >= 4
    ]
    rates = [rate for rate in rates if 2.0 <= rate <= 8.0]
    return round(median(rates), 3) if rates else 4.4


def _next_voice(cues: list[CueView], position: int) -> CueView | None:
    return next((cue for cue in cues[position + 1 :] if cue.voiced), None)


def _speed_severity(speed: float | None, rate: float | None, t: Thresholds) -> str | None:
    speed = speed or 0.0
    rate = rate or 0.0
    if speed >= t.speed_error or rate >= t.rate_error:
        return "error"
    if (speed >= t.speed_warn and rate >= t.rate_warn) or rate >= t.rate_alone_warn:
        return "warning"
    return None


def detect(
    cues: list[CueView],
    layout: Layout,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> list[Issue]:
    """All issues, in timeline order. ``cues`` must be sorted by start."""
    t = thresholds
    wrap = layout.wrap or _default_wrap
    issues: list[Issue] = []
    for position, cue in enumerate(cues):
        text = cue.text.strip()
        if not text or cue.muted:
            continue
        if not any(char.isalnum() for char in text):
            # "..." / "-" left over from OCR or a split: shown (and voiced) for nothing.
            issues.append(Issue(cue.id, "sub_empty", "warning", cue.start, cue.end, {"text": text}))
            continue
        previous = cues[position - 1] if position else None
        following = cues[position + 1] if position + 1 < len(cues) else None

        # ---- voice ---------------------------------------------------------
        if cue.voiced:
            speed, rate = cue.speed, voice_rate(cue)
            severity = _speed_severity(speed, rate, t)
            if severity:
                issues.append(
                    Issue(cue.id, "voice_fast", severity, cue.voice_start, cue.voice_end,
                          {"speed": _r(speed), "rate": _r(rate), "natural": _r(cue.natural), "slot": _r(cue.voice_len)})
                )
            elif speed is not None and speed < t.speed_slow_warn:
                issues.append(
                    Issue(cue.id, "voice_slow", "warning" if speed >= t.speed_slow_error else "error",
                          cue.voice_start, cue.voice_end, {"speed": _r(speed), "rate": _r(rate)})
                )
            later = _next_voice(cues, position)
            if later is not None:
                overlap = cue.voice_end - later.voice_start
                if overlap > t.overlap_tolerance:
                    issues.append(
                        Issue(cue.id, "voice_overlap", "error" if overlap >= t.overlap_error else "warning",
                              later.voice_start, cue.voice_end, {"overlap": _r(overlap), "next_id": later.id})
                    )
            late = cue.voice_start - cue.start
            early = cue.start - cue.voice_start
            # Split-off parts of this cue are voiced by this take: compare with the next own cue.
            independent = next((later for later in cues[position + 1 :] if not later.shared), None)
            own_end = max([cue.end] + [later.end for later in cues[position + 1 :] if later.shared and (independent is None or later.start < independent.start)])
            next_sub_start = independent.start if independent else layout.duration or own_end
            tail = cue.voice_end - max(own_end, next_sub_start)
            if late > t.offset_late or early > t.offset_early or (independent and tail > t.tail_over):
                issues.append(
                    Issue(cue.id, "voice_offset", "warning", min(cue.start, cue.voice_start), max(cue.end, cue.voice_end),
                          {"late": _r(late), "early": _r(early), "tail": _r(max(0.0, tail))})
                )
            if cue.duration >= t.dead_air_min_cue:
                covered = max(0.0, min(cue.end, cue.voice_end) - max(cue.start, cue.voice_start))
                coverage = covered / cue.duration
                silent = cue.duration - covered
                if coverage < t.dead_air_coverage:
                    issues.append(
                        Issue(cue.id, "dead_air", "warning" if silent >= t.dead_air_error_gap else "info",
                              cue.start, cue.end, {"coverage": _r(coverage), "silent": _r(silent)})
                    )
        elif not cue.shared:
            issues.append(Issue(cue.id, "voice_missing", "warning", cue.start, cue.end, {}))

        # ---- subtitle ------------------------------------------------------
        duration = cue.duration
        chars = len(text)
        cps = chars / duration if duration > 0 else 99.0
        if cps > t.cps_warn:
            issues.append(
                Issue(cue.id, "sub_fast", "error" if cps > t.cps_error else "warning", cue.start, cue.end,
                      {"cps": _r(cps), "chars": chars, "duration": _r(duration)})
            )
        if duration < t.min_duration:
            issues.append(
                Issue(cue.id, "sub_short", "warning" if duration < t.min_duration_warn else "info", cue.start, cue.end,
                      {"duration": _r(duration)})
            )
        elif duration > t.max_duration:
            issues.append(Issue(cue.id, "sub_long", "warning", cue.start, cue.end, {"duration": _r(duration)}))

        lines = wrap(text, layout.chars_per_line, layout.max_lines)
        longest = max((len(line) for line in lines), default=0)
        if len(lines) > layout.max_lines or longest > layout.chars_per_line + max(2, round(layout.chars_per_line * 0.12)):
            issues.append(
                Issue(cue.id, "sub_lines", "warning", cue.start, cue.end,
                      {"lines": len(lines), "max_lines": layout.max_lines, "longest": longest, "limit": layout.chars_per_line})
            )
        broken = vi_text.wrap_break_issue(lines[: layout.max_lines])
        if broken:
            issues.append(
                Issue(cue.id, "sub_bad_break", "warning", cue.start, cue.end, {"reason": broken[0], "at": broken[1]})
            )

        word_count = len(text.split())
        continues_prev = bool(
            previous and previous.text.strip()
            and cue.start - previous.end <= t.fragment_join_gap
            and (not vi_text.ends_sentence(previous.text) or vi_text.starts_lowercase(text))
        )
        continues_next = bool(
            following and following.text.strip()
            and following.start - cue.end <= t.fragment_join_gap
            and not vi_text.ends_sentence(text)
            and vi_text.starts_lowercase(following.text)
        )
        if word_count <= t.orphan_words and duration <= t.orphan_max_duration and (continues_prev or continues_next):
            issues.append(
                Issue(cue.id, "sub_orphan", "warning", cue.start, cue.end,
                      {"words": word_count, "joins": "next" if continues_next else "previous"})
            )
        elif continues_next and following is not None:
            tail = vi_text.words(text)[-1] if text else ""
            head = vi_text.words(following.text)[0] if following.text.strip() else ""
            tiny_next = following.duration < t.min_duration or len(following.text.split()) <= 3
            penalty, reason = vi_text.break_penalty(tail, head)
            bad_cut = bool(reason) and penalty > 0
            if bad_cut or tiny_next or duration < t.min_duration:
                issues.append(
                    Issue(cue.id, "sub_fragment", "warning", cue.start, following.end,
                          {"next_id": following.id, "reason": reason if bad_cut else "tiny", "at": f"{tail} | {head}"})
                )
    return issues


def summarize(cues: list[CueView], issues: list[Issue]) -> dict[str, Any]:
    """Counts per type / severity / category and a 0–100 quality score."""
    by_type: dict[str, int] = {}
    by_severity = {"error": 0, "warning": 0, "info": 0}
    penalty: dict[str, float] = {}
    for issue in issues:
        by_type[issue.type] = by_type.get(issue.type, 0) + 1
        by_severity[issue.severity] += 1
        penalty[issue.cue_id] = penalty.get(issue.cue_id, 0.0) + SEVERITY_WEIGHT[issue.severity]
    spoken = [cue for cue in cues if cue.text.strip() and not cue.muted] or cues
    total = max(1, len(spoken))
    loss = sum(min(3.0, value) for value in penalty.values()) / (3.0 * total)
    voiced = [cue for cue in cues if cue.voiced and cue.speed]
    return {
        "score": round(100 * (1 - loss)),
        "cues": len(spoken),
        "cues_with_issues": len(penalty),
        "issues": len(issues),
        "by_type": by_type,
        "by_severity": by_severity,
        "by_category": {
            "voice": sum(1 for issue in issues if issue.category == "voice"),
            "subtitle": sum(1 for issue in issues if issue.category == "subtitle"),
        },
        "median_speed": _r(median([cue.speed for cue in voiced])) if voiced else None,
        "natural_rate": natural_rate(cues),
    }


def issues_by_cue(issues: list[Issue]) -> dict[str, list[Issue]]:
    grouped: dict[str, list[Issue]] = {}
    for issue in issues:
        grouped.setdefault(issue.cue_id, []).append(issue)
    return grouped


def cue_penalty(issues: list[Issue]) -> float:
    return sum(SEVERITY_WEIGHT[issue.severity] for issue in issues)


def _r(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)
