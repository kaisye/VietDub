from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.viral import language as lang
from app.services.viral import scoring
from app.services.viral.providers import VideoItem, utcnow
from test_viral_api import FakeYouTube, _scan, env  # noqa: F401  (fixture import)


@pytest.mark.parametrize(
    "title,description,hint,expected",
    [
        ("5 Kitchen Hacks You Need to Know", "", None, "en"),
        ("ಇಷ್ಟು ದಿನ ಈ ವಿಷಯ ತಿಳಿಯದೇ ಎಷ್ಟು ಕಷ್ಟಪಟ್ಟೆವು! Kitchen tips in Kannada", "", "en", "kn"),
        ("சமையல் குறிப்புகள் | Cooking tips", "", None, "ta"),
        ("Bartan Chamkane Ka Jadoi Paste", "برتن چمکانے کا جادوئی پیسٹ اور پالک کاٹنے کا تیز طریقہ", None, "ar"),
        ("Bartan chamkane ka asaan tarika jo kisi ko nahi pata", "", None, "hi"),
        ("आसान रसोई टिप्स", "", None, "hi"),
        ("做饭小技巧 #美食", "", None, "zh"),
        ("簡単！お弁当の作り方", "", None, "ja"),
        ("초간단 요리 꿀팁", "", None, "ko"),
        ("Cómo hacer pan casero fácil", "", None, "es"),
        ("Mẹo nấu ăn siêu nhanh cho người bận rộn", "", None, "vi"),
        ("เคล็ดลับทำอาหาร", "", None, "th"),
        ("Recette facile du gâteau au chocolat", "", "fr", "fr"),
        ("", "", "en-US", "en"),
        ("", "", None, None),
    ],
)
def test_detect_language(title, description, hint, expected):
    assert lang.detect_language(title, description, hint) == expected


def test_hint_does_not_override_dominant_script():
    assert lang.detect_language("ಅಡುಗೆ ಮನೆ ಟಿಪ್ಸ್", "", "en") == "kn"
    assert lang.detect_language("Morning routine", "", "de") == "de"


def test_filter_helpers():
    assert lang.expected_language("auto", "youtube") == "en"
    assert lang.expected_language("auto", "douyin") == "zh"
    assert lang.expected_language("any", "youtube") is None
    assert lang.expected_language("ja", "douyin") == "ja"
    assert lang.matches("en", "en") and lang.matches(None, "en")
    assert not lang.matches("kn", "en")
    assert lang.matches("kn", "other") and not lang.matches("en", "other")
    assert lang.filter_bucket("pt") == "other"


def test_dub_fit_false_on_language_mismatch():
    base = dict(platform="youtube", views=10_000, title="Kitchen hacks tutorial", duration=90)
    ok = scoring.dubbability(scoring.VideoMetrics(**base, language="en", expected_language="en"))
    assert ok["good"] and not ok["language_mismatch"]
    bad = scoring.dubbability(scoring.VideoMetrics(**base, language="kn", expected_language="en"))
    assert not bad["good"] and "language_mismatch" in bad["reasons"]
    vi = scoring.dubbability(scoring.VideoMetrics(**base, language="vi", expected_language="en"))
    assert not vi["good"] and "already_vietnamese" in vi["reasons"]


def test_scan_detects_language_and_filters_listing(env, monkeypatch):  # noqa: F811
    original = FakeYouTube.search

    def search(self, q):
        items = original(self, q)
        items.append(
            VideoItem(platform="youtube", external_id="vid3", url="https://www.youtube.com/watch?v=vid3",
                      title="ಇಷ್ಟು ದಿನ ಈ ವಿಷಯ ತಿಳಿಯದೇ! Kitchen tips", followers=1000, views=90_000, likes=900,
                      comments=10, published_at=utcnow() - timedelta(hours=30), duration=300, language="en",
                      source=self.id)
        )
        return items

    monkeypatch.setattr(FakeYouTube, "search", search)
    c = env["client"]
    data = _scan(c, query="cooking", platforms=["youtube"])
    assert data["language_mismatched"] == 1
    all_items = c.get("/api/viral/videos").json()["items"]
    kn = next(v for v in all_items if v["external_id"] == "vid3")
    assert kn["language"] == "kn" and "dub_fit" not in kn["badges"]
    assert kn["dub"]["language_mismatch"] is True
    en_only = c.get("/api/viral/videos", params={"source_language": "auto"}).json()
    assert {v["external_id"] for v in en_only["items"]} == {"vid1", "vid2"}
    other = c.get("/api/viral/videos", params={"source_language": "other"}).json()
    assert [v["external_id"] for v in other["items"]] == ["vid3"]
    # The choice is remembered for later re-scoring (refresh keeps the mismatch).
    c.post("/api/viral/videos/refresh", json={"video_ids": [kn["id"]]})
    assert c.get(f"/api/viral/videos/{kn['id']}").json()["dub"]["language_mismatch"] is True
