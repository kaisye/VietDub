"""Viral score: one pure, dependency-free module (no DB, no network).

Every component is a number in ``[0, 1]`` (or ``None`` when the data needed for it
is missing). The final score is a weighted mean over the components that are
available, scaled to 0-100. Missing components do not count as zero: their
weight is redistributed, and ``confidence`` (sum of the available weights)
tells the UI how much of the formula was actually used.

Components and weights (sum = 1.0)::

    velocity    0.35  views per hour since publish, log-scaled; blended 60/40 between a
                      platform baseline and the rank inside the current result set
    outlier     0.25  views / creator followers ("outlier" ratio, vidIQ style): a video
                      that beats its own channel size is topic-driven and replicable
    engagement  0.15  (likes + comments + shares + collects) / views, log-scaled
    momentum    0.15  real growth between scans: recent views/hour (from the last two
                      snapshots) vs. the lifetime average views/hour
    freshness   0.10  exponential decay with a half-life tied to the time window

Douyin public APIs usually hide play counts. When views are missing but likes are
known we estimate ``views = likes * DOUYIN_LIKES_TO_VIEWS`` (a ~3 % like rate,
typical for Douyin) and flag ``views_estimated``; the engagement component is then
skipped because it would be circular.

Dubbability ("Phù hợp lồng tiếng") is reported next to the score, not inside it:
it describes whether *VietDub* can use the video, not how viral it is.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .language import filter_bucket

WEIGHTS: dict[str, float] = {
    "velocity": 0.35,
    "outlier": 0.25,
    "engagement": 0.15,
    "momentum": 0.15,
    "freshness": 0.10,
}

# Views/hour that map to 0 and 1 on the log scale, per platform.
VELOCITY_BASELINES: dict[str, tuple[float, float]] = {
    "youtube": (10.0, 50_000.0),
    "douyin": (50.0, 200_000.0),
}
DEFAULT_BASELINE = (10.0, 50_000.0)
RANK_BLEND = 0.4  # share of the in-set rank in the velocity component
MIN_SET_FOR_RANK = 5

DOUYIN_LIKES_TO_VIEWS = 33.0
MIN_FOLLOWERS = 100  # avoid absurd outlier ratios for brand-new channels

# Momentum needs two snapshots at least this far apart.
MOMENTUM_MIN_GAP_HOURS = 0.5
ACCELERATING_RATIO = 1.5

WINDOW_HOURS = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30, "90d": 24 * 90}
HALF_LIFE_HOURS = {"24h": 12.0, "7d": 48.0, "30d": 168.0, "90d": 480.0}
DEFAULT_HALF_LIFE = 72.0

TIERS = ((80, "very_high"), (60, "high"), (40, "medium"), (0, "low"))

SHORT_MAX_SECONDS = 180


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass
class VideoMetrics:
    platform: str
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    collects: int | None = None
    followers: int | None = None
    published_at: datetime | None = None
    duration: float | None = None
    title: str = ""
    description: str = ""
    language: str | None = None
    # Source language the user asked for ("en" for YouTube, "zh" for Douyin by default).
    expected_language: str | None = None


@dataclass
class Snapshot:
    at: datetime
    views: int | None


@dataclass
class ScoreResult:
    score: int
    tier: str
    confidence: float
    components: dict[str, float | None]
    weights: dict[str, float]
    metrics: dict[str, Any]
    badges: list[str] = field(default_factory=list)
    dub: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "tier": self.tier,
            "confidence": round(self.confidence, 3),
            "components": {k: (None if v is None else round(v, 4)) for k, v in self.components.items()},
            "weights": self.weights,
            "metrics": self.metrics,
            "badges": self.badges,
            "dub": self.dub,
        }


# --------------------------------------------------------------------------- helpers


def hours_since(published_at: datetime | None, now: datetime) -> float | None:
    if published_at is None:
        return None
    return max((now - published_at).total_seconds() / 3600.0, 1.0)


def effective_views(m: VideoMetrics) -> tuple[int | None, bool]:
    """Views, or an estimate from likes for platforms that hide play counts."""
    if m.views:
        return int(m.views), False
    if m.platform == "douyin" and m.likes:
        return int(m.likes * DOUYIN_LIKES_TO_VIEWS), True
    return (int(m.views) if m.views is not None else None), False


def views_per_hour(m: VideoMetrics, now: datetime) -> float | None:
    views, _ = effective_views(m)
    hours = hours_since(m.published_at, now)
    if views is None or hours is None:
        return None
    return views / hours


def velocity_baseline(vph: float, platform: str) -> float:
    lo, hi = VELOCITY_BASELINES.get(platform, DEFAULT_BASELINE)
    return _clamp((math.log10(vph + 1) - math.log10(lo + 1)) / (math.log10(hi + 1) - math.log10(lo + 1)))


def percentile_rank(value: float, population: list[float]) -> float:
    """Fraction of the population strictly below ``value`` (ties count half)."""
    if len(population) <= 1:
        return 0.5
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value) - 1  # exclude self
    return _clamp((below + 0.5 * max(equal, 0)) / (len(population) - 1))


def outlier_ratio(m: VideoMetrics) -> float | None:
    views, _ = effective_views(m)
    if views is None or not m.followers:
        return None
    return views / max(m.followers, MIN_FOLLOWERS)


def outlier_component(ratio: float | None) -> float | None:
    """ratio 0.1x -> 0, 1x -> 0.33, 10x -> 0.67, 100x+ -> 1."""
    if ratio is None:
        return None
    if ratio <= 0:
        return 0.0
    return _clamp((math.log10(ratio) + 1.0) / 3.0)


def engagement_rate(m: VideoMetrics) -> float | None:
    views, estimated = effective_views(m)
    if estimated or not views:
        return None
    parts = [m.likes, m.comments, m.shares, m.collects]
    if all(p is None for p in parts):
        return None
    return sum(p or 0 for p in parts) / views


def engagement_component(rate: float | None) -> float | None:
    """0.2 % -> 0, ~3 % -> 0.63, 15 %+ -> 1 (log scale)."""
    if rate is None:
        return None
    if rate <= 0.002:
        return 0.0
    return _clamp(math.log10(rate / 0.002) / math.log10(75))


def freshness_component(hours: float | None, window: str | None) -> float | None:
    if hours is None:
        return None
    half_life = HALF_LIFE_HOURS.get(window or "", DEFAULT_HALF_LIFE)
    return _clamp(0.5 ** (hours / half_life))


def momentum(snapshots: Iterable[Snapshot], m: VideoMetrics, now: datetime) -> dict[str, Any] | None:
    """Recent views/hour (last two usable snapshots) vs. lifetime average."""
    usable = sorted((s for s in snapshots if s.views is not None), key=lambda s: s.at)
    if len(usable) < 2:
        return None
    last = usable[-1]
    prev = None
    for snap in reversed(usable[:-1]):
        if (last.at - snap.at).total_seconds() / 3600.0 >= MOMENTUM_MIN_GAP_HOURS:
            prev = snap
            break
    if prev is None:
        return None
    gap_h = (last.at - prev.at).total_seconds() / 3600.0
    delta = int(last.views or 0) - int(prev.views or 0)
    recent_vph = max(delta, 0) / gap_h
    hours = hours_since(m.published_at, last.at)
    lifetime_vph = (last.views or 0) / hours if hours else None
    ratio = (recent_vph / lifetime_vph) if lifetime_vph else None
    return {
        "delta_views": delta,
        "gap_hours": round(gap_h, 2),
        "recent_vph": round(recent_vph, 2),
        "lifetime_vph": round(lifetime_vph, 2) if lifetime_vph is not None else None,
        "ratio": round(ratio, 3) if ratio is not None else None,
    }


def momentum_component(info: dict[str, Any] | None) -> float | None:
    """ratio 1 (steady) -> 0.5, 4x faster -> 1, 4x slower -> 0."""
    if not info or info.get("ratio") is None:
        return None
    ratio = info["ratio"]
    if ratio <= 0:
        return 0.0
    return _clamp(0.5 + math.log2(ratio) / 4.0)


def tier_for(score: int) -> str:
    for threshold, name in TIERS:
        if score >= threshold:
            return name
    return "low"


# ------------------------------------------------------------------- dubbability

_VI_CHARS = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.I)
_MUSIC_HINTS = re.compile(
    r"\b(official (music )?video|official audio|lyrics?|lyric video|mv|karaoke|instrumental|"
    r"remix|nightcore|bgm|ost|cover song|asmr no talking|no talking)\b|纯音乐|歌曲|翻唱|音乐|伴奏|歌词|BGM",
    re.I,
)
_SPEECH_HINTS = re.compile(
    r"\b(how to|tutorial|review|explained?|story|podcast|interview|vlog|tips?|hacks?|guide|reaction|"
    r"recipe|news|documentary|lesson|unboxing|challenge|prank|debate|commentary|stand.?up)\b|"
    r"教程|讲解|解说|教学|测评|评测|开箱|故事|访谈|采访|干货|技巧|攻略|科普|做法|分享|揭秘|吐槽|vlog",
    re.I,
)


def dubbability(m: VideoMetrics) -> dict[str, Any]:
    """Heuristic hints: can VietDub turn this into a good Vietnamese dub?"""
    text = f"{m.title}\n{m.description}"
    reasons: list[str] = []
    score = 0.5
    fmt = None
    if m.duration:
        if m.duration <= SHORT_MAX_SECONDS:
            fmt = "short"
            score += 0.2
            reasons.append("duration_short")
        elif m.duration <= 30 * 60:
            fmt = "long"
            score += 0.15
            reasons.append("duration_long")
        else:
            fmt = "very_long"
            score -= 0.15
            reasons.append("duration_very_long")
        if m.duration < 10:
            score -= 0.2
            reasons.append("too_short")
    is_vietnamese = (m.language or "").lower().startswith("vi") or len(_VI_CHARS.findall(m.title or "")) >= 2
    if is_vietnamese:
        score -= 0.6
        reasons.append("already_vietnamese")
    mismatch = False
    if m.expected_language and m.language and not is_vietnamese:
        mismatch = filter_bucket(m.language) != m.expected_language
        if mismatch:
            score -= 0.3
            reasons.append("language_mismatch")
    music = bool(_MUSIC_HINTS.search(text))
    if music:
        score -= 0.35
        reasons.append("music_only")
    speech = bool(_SPEECH_HINTS.search(text))
    if speech:
        score += 0.2
        reasons.append("has_speech")
    score = _clamp(score)
    return {
        "fit": round(score, 3),
        "good": score >= 0.6 and not is_vietnamese and not music and not mismatch,
        "language": m.language,
        "language_mismatch": mismatch,
        "format": fmt,
        "speech": True if speech else None,
        "music_only": music,
        "vietnamese": is_vietnamese,
        "reasons": reasons,
    }


# --------------------------------------------------------------------- scoring


def score_video(
    m: VideoMetrics,
    *,
    now: datetime,
    window: str | None = None,
    population_vph: list[float] | None = None,
    snapshots: Iterable[Snapshot] = (),
) -> ScoreResult:
    hours = hours_since(m.published_at, now)
    views, estimated = effective_views(m)
    vph = views_per_hour(m, now)

    velocity = None
    if vph is not None:
        velocity = velocity_baseline(vph, m.platform)
        if population_vph and len(population_vph) >= MIN_SET_FOR_RANK:
            velocity = (1 - RANK_BLEND) * velocity + RANK_BLEND * percentile_rank(vph, population_vph)

    ratio = outlier_ratio(m)
    rate = engagement_rate(m)
    mom = momentum(snapshots, m, now)
    components: dict[str, float | None] = {
        "velocity": velocity,
        "outlier": outlier_component(ratio),
        "engagement": engagement_component(rate),
        "momentum": momentum_component(mom),
        "freshness": freshness_component(hours, window),
    }
    available = {k: v for k, v in components.items() if v is not None}
    weight_sum = sum(WEIGHTS[k] for k in available)
    raw = sum(WEIGHTS[k] * v for k, v in available.items()) / weight_sum if weight_sum else 0.0
    score = int(round(raw * 100))

    badges: list[str] = []
    if ratio is not None and ratio >= 3:
        badges.append("outlier")
    if mom and mom.get("ratio") is not None and mom["ratio"] >= ACCELERATING_RATIO and mom["delta_views"] > 0:
        badges.append("accelerating")
    dub = dubbability(m)
    if dub["good"]:
        badges.append("dub_fit")
    if hours is not None and hours <= 48:
        badges.append("new")

    metrics = {
        "views": views,
        "views_estimated": estimated,
        "hours_since_publish": round(hours, 2) if hours is not None else None,
        "views_per_hour": round(vph, 2) if vph is not None else None,
        "outlier_ratio": round(ratio, 3) if ratio is not None else None,
        "engagement_rate": round(rate, 5) if rate is not None else None,
        "momentum": mom,
    }
    return ScoreResult(
        score=score,
        tier=tier_for(score),
        confidence=weight_sum,
        components=components,
        weights=dict(WEIGHTS),
        metrics=metrics,
        badges=badges,
        dub=dub,
    )


def score_batch(
    items: list[tuple[VideoMetrics, list[Snapshot]]],
    *,
    now: datetime,
    window: str | None = None,
) -> list[ScoreResult]:
    """Score a result set together so velocity can be ranked within it (per platform)."""
    by_platform: dict[str, list[float]] = {}
    for m, _ in items:
        vph = views_per_hour(m, now)
        if vph is not None:
            by_platform.setdefault(m.platform, []).append(vph)
    return [
        score_video(m, now=now, window=window, population_vph=by_platform.get(m.platform), snapshots=snaps)
        for m, snaps in items
    ]
