from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.viral import scoring as s

NOW = datetime(2026, 10, 26, 12, 0, 0)


def m(**kw) -> s.VideoMetrics:
    base = dict(platform="youtube", views=100_000, likes=4_000, comments=300, followers=50_000,
                published_at=NOW - timedelta(hours=24), duration=95, title="Easy cooking hacks tutorial")
    base.update(kw)
    return s.VideoMetrics(**base)


def test_weights_sum_to_one():
    assert sum(s.WEIGHTS.values()) == pytest.approx(1.0)


def test_velocity_baseline_is_log_scaled_and_clamped():
    assert s.velocity_baseline(0, "youtube") == 0.0
    assert s.velocity_baseline(10_000_000, "youtube") == 1.0
    mid = s.velocity_baseline(1_000, "youtube")
    assert 0.4 < mid < 0.7
    # Douyin has a higher baseline: same speed scores lower.
    assert s.velocity_baseline(1_000, "douyin") < mid


def test_outlier_component_mapping():
    assert s.outlier_component(None) is None
    assert s.outlier_component(0.1) == pytest.approx(0.0)
    assert s.outlier_component(1) == pytest.approx(1 / 3)
    assert s.outlier_component(10) == pytest.approx(2 / 3)
    assert s.outlier_component(1000) == 1.0
    assert s.outlier_ratio(m(views=1_200_000, followers=100_000)) == pytest.approx(12)
    # Tiny channels are floored to MIN_FOLLOWERS.
    assert s.outlier_ratio(m(views=1000, followers=1)) == pytest.approx(10)


def test_engagement_rate_and_component():
    rate = s.engagement_rate(m(views=100_000, likes=3_000, comments=500, shares=None))
    assert rate == pytest.approx(0.035)
    assert s.engagement_component(0.001) == 0.0
    assert s.engagement_component(0.15) == pytest.approx(1.0)
    assert 0.5 < s.engagement_component(0.03) < 0.75
    assert s.engagement_rate(m(likes=None, comments=None)) is None


def test_freshness_half_life_depends_on_window():
    assert s.freshness_component(12, "24h") == pytest.approx(0.5)
    assert s.freshness_component(48, "7d") == pytest.approx(0.5)
    assert s.freshness_component(None, "7d") is None


def test_douyin_views_estimated_from_likes_and_engagement_skipped():
    d = m(platform="douyin", views=None, likes=10_000, comments=500, shares=200)
    views, est = s.effective_views(d)
    assert est and views == int(10_000 * s.DOUYIN_LIKES_TO_VIEWS)
    assert s.engagement_rate(d) is None
    r = s.score_video(d, now=NOW, window="7d")
    assert r.components["engagement"] is None
    assert r.metrics["views_estimated"] is True


def test_missing_components_redistribute_weight():
    full = s.score_video(m(), now=NOW, window="7d")
    no_followers = s.score_video(m(followers=None), now=NOW, window="7d")
    assert no_followers.components["outlier"] is None
    assert no_followers.confidence == pytest.approx(full.confidence - s.WEIGHTS["outlier"])
    assert 0 <= no_followers.score <= 100


def test_momentum_from_snapshots_marks_accelerating():
    pub = NOW - timedelta(hours=48)
    video = m(views=60_000, published_at=pub)
    snaps = [
        s.Snapshot(at=NOW - timedelta(hours=2), views=20_000),
        s.Snapshot(at=NOW, views=60_000),  # +40k in 2h vs 60k/48h lifetime
    ]
    info = s.momentum(snaps, video, NOW)
    assert info["delta_views"] == 40_000
    assert info["recent_vph"] == pytest.approx(20_000)
    assert info["ratio"] == pytest.approx(16, rel=0.01)
    r = s.score_video(video, now=NOW, window="7d", snapshots=snaps)
    assert "accelerating" in r.badges
    assert r.components["momentum"] == 1.0


def test_momentum_needs_gap_between_snapshots():
    video = m()
    snaps = [s.Snapshot(at=NOW - timedelta(minutes=5), views=1), s.Snapshot(at=NOW, views=2)]
    assert s.momentum(snaps, video, NOW) is None
    assert s.momentum([s.Snapshot(at=NOW, views=1)], video, NOW) is None


def test_viral_outlier_beats_average_video():
    hot = m(views=2_000_000, followers=20_000, likes=150_000, comments=9_000, published_at=NOW - timedelta(hours=20))
    meh = m(views=3_000, followers=2_000_000, likes=30, comments=2, published_at=NOW - timedelta(days=6))
    rs = s.score_batch([(hot, []), (meh, [])], now=NOW, window="7d")
    assert rs[0].score > 75 and rs[0].tier in {"very_high", "high"}
    assert rs[1].score < 35 and rs[1].tier == "low"
    assert "outlier" in rs[0].badges and "outlier" not in rs[1].badges


def test_rank_blend_only_with_enough_items():
    items = [(m(views=v), []) for v in (1_000, 10_000, 100_000, 1_000_000, 5_000_000)]
    rs = s.score_batch(items, now=NOW, window="7d")
    scores = [r.score for r in rs]
    assert scores == sorted(scores)
    alone = s.score_video(items[1][0], now=NOW, window="7d")
    assert alone.components["velocity"] != rs[1].components["velocity"]


def test_tiers():
    assert s.tier_for(85) == "very_high"
    assert s.tier_for(60) == "high"
    assert s.tier_for(40) == "medium"
    assert s.tier_for(10) == "low"


@pytest.mark.parametrize(
    "kwargs,good,reason",
    [
        (dict(title="iPhone 17 review: worth it?", duration=120), True, "has_speech"),
        (dict(title="Taylor Swift - Song (Official Music Video)", duration=200), False, "music_only"),
        (dict(title="Mẹo nấu ăn siêu nhanh cho người bận rộn", duration=60), False, "already_vietnamese"),
        (dict(title="做饭技巧 教程 分享", duration=90), True, "has_speech"),
        (dict(title="Long podcast", duration=3 * 3600), False, "duration_very_long"),
    ],
)
def test_dubbability(kwargs, good, reason):
    d = s.dubbability(m(**kwargs))
    assert d["good"] is good
    assert reason in d["reasons"]


def test_score_result_serializes():
    data = s.score_video(m(), now=NOW, window="30d").as_dict()
    assert set(data) >= {"score", "tier", "confidence", "components", "weights", "metrics", "badges", "dub"}
    assert data["metrics"]["views_per_hour"] == pytest.approx(100_000 / 24, rel=1e-3)
