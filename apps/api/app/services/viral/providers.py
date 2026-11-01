"""Pluggable video sources for viral discovery.

Each provider reports its own availability (``availability()``) so the UI can show
which modes work, and raises :class:`SourceError` with a stable ``code`` instead of
crashing a scan. Reliability, as verified on 2026-10-26:

* ``youtube_api``   — YouTube Data API v3 with the user's API key. Stable, official,
  quota-limited (search.list = 100 units, videos/channels.list = 1 unit; 10 000/day).
* ``youtube_ytdlp`` — no key: yt-dlp over the public search results page (sorted by
  views, upload-date filter) + per-video metadata. Works today; can break when
  YouTube changes or rate-limits ("not a bot" checks).
* ``douyin_public`` — no key: Douyin's public hot-search board (keywords, no
  videos) and link metadata (title + cover, NO stats) via the downloader fallback
  service. Douyin keyword search and stats are NOT available without signing.
* ``douyin_tikhub`` — optional third-party API key (TikHub-style REST): keyword
  search, video stats, creator posts. Paid service; request shape from its public
  docs, not live-verified here (no key).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

import httpx

from . import settings as viral_settings

logger = logging.getLogger(__name__)

# Tests swap these (no network in unit tests).
client_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))


def ytdlp_extract(url: str, options: dict[str, Any]) -> dict[str, Any]:
    import yt_dlp

    options = dict(options)
    no_process = bool(options.pop("_no_process", False))
    base = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15}
    base.update(options)
    with yt_dlp.YoutubeDL(base) as ydl:
        return ydl.extract_info(url, download=False, process=not no_process) or {}


def snapvideotools_lookup(url: str) -> dict[str, Any]:
    from ..thirdparty_download import _snapvideotools_request

    return _snapvideotools_request(url)


WINDOW_HOURS = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30, "90d": 24 * 90}
SHORT_MAX_SECONDS = 180
YT_SEARCH_COST = 100


class SourceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class VideoItem:
    platform: str
    external_id: str
    url: str
    title: str = ""
    description: str = ""
    creator_name: str = ""
    creator_id: str = ""
    creator_url: str = ""
    followers: int | None = None
    published_at: datetime | None = None  # naive UTC
    duration: float | None = None
    thumbnail: str = ""
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    collects: int | None = None
    language: str | None = None
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        return data


@dataclass
class SearchQuery:
    keyword: str
    window: str = "7d"
    region: str = ""
    duration: str = "any"  # any | short | long
    limit: int = 20
    order: str = "views"  # views | relevance | date
    language: str = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- URL parsing

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_source_url(url: str) -> dict[str, str] | None:
    """Classify a pasted link: ``{"platform", "kind": video|creator, "id", "url"}``."""
    text = (url or "").strip()
    match = re.search(r"https?://[^\s\"'<>]+", text)
    if not match:
        return None
    url = match.group(0).rstrip(").,;]")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host in {"youtu.be"}:
        vid = path.strip("/").split("/")[0]
        if _YT_ID.match(vid):
            return {"platform": "youtube", "kind": "video", "id": vid, "url": f"https://www.youtube.com/watch?v={vid}"}
    if host.endswith("youtube.com"):
        query = parse_qs(parsed.query)
        vid = (query.get("v") or [""])[0]
        m = re.match(r"^/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})", path)
        if m:
            vid = m.group(1)
        if vid and _YT_ID.match(vid):
            return {"platform": "youtube", "kind": "video", "id": vid, "url": f"https://www.youtube.com/watch?v={vid}"}
        m = re.match(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)", path)
        if m:
            ident = m.group(1)
            return {"platform": "youtube", "kind": "creator", "id": ident, "url": f"https://www.youtube.com/{ident}"}
        return None
    if host == "douyin.com" or host.endswith(".douyin.com") or host.endswith("iesdouyin.com"):
        if host == "v.douyin.com" or host.endswith(".v.douyin.com"):
            return {"platform": "douyin", "kind": "short_link", "id": "", "url": url}
        query = parse_qs(parsed.query)
        vid = (query.get("modal_id") or query.get("aweme_id") or [""])[0]
        if not vid:
            m = re.search(r"/(?:video|note|share/video)/(\d+)", path)
            vid = m.group(1) if m else ""
        if vid.isdigit():
            return {"platform": "douyin", "kind": "video", "id": vid, "url": f"https://www.douyin.com/video/{vid}"}
        m = re.search(r"/(?:user|share/user)/([^/?#]+)", path)
        if m and m.group(1) != "self":
            return {"platform": "douyin", "kind": "creator", "id": m.group(1), "url": f"https://www.douyin.com/user/{m.group(1)}"}
    return None


def resolve_short_link(url: str) -> dict[str, str] | None:
    """Follow a v.douyin.com share link (one HTTP request) and classify the target."""
    from ..downloader import _platform_http_headers

    try:
        with client_factory() as client:
            response = client.get(url, headers=_platform_http_headers(url), follow_redirects=True)
            final = str(response.url)
    except httpx.HTTPError:
        return None
    parsed = parse_source_url(final)
    return parsed if parsed and parsed["kind"] != "short_link" else None


# ------------------------------------------------------------------------------- cache

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 3 * 3600


def _cache_get(key: str) -> Any:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        _cache.pop(key, None)
        return None


def _cache_put(key: str, value: Any, ttl: float = CACHE_TTL_SECONDS) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _within(item: VideoItem, q: SearchQuery, now: datetime) -> bool:
    hours = WINDOW_HOURS.get(q.window)
    if hours and item.published_at and item.published_at < now - timedelta(hours=hours):
        return False
    if item.duration:
        if q.duration == "short" and item.duration > SHORT_MAX_SECONDS:
            return False
        if q.duration == "long" and item.duration <= SHORT_MAX_SECONDS:
            return False
    return True


# ----------------------------------------------------------------------------- base


class Provider:
    id = ""
    platform = ""
    label = ""
    reliability = "stable"  # stable | best_effort | unverified

    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings if settings is not None else viral_settings.load_settings()

    def availability(self) -> dict[str, Any]:
        ok, reason = self.is_available()
        return {
            "id": self.id,
            "platform": self.platform,
            "label": self.label,
            "available": ok,
            "reason": reason,
            "reliability": self.reliability,
            "capabilities": self.capabilities(),
            "limits": self.limits(),
        }

    def is_available(self) -> tuple[bool, str]:
        return True, ""

    def capabilities(self) -> list[str]:
        return []

    def limits(self) -> dict[str, Any]:
        return {}

    def search(self, q: SearchQuery) -> list[VideoItem]:
        raise SourceError("unsupported", f"{self.label}: không hỗ trợ tìm theo từ khóa.")

    def fetch_videos(self, ids: list[str]) -> list[VideoItem]:
        raise SourceError("unsupported", f"{self.label}: không hỗ trợ phân tích link.")

    def fetch_creator(self, ident: str, limit: int = 20) -> list[VideoItem]:
        raise SourceError("unsupported", f"{self.label}: không hỗ trợ theo dõi kênh.")


# -------------------------------------------------------------------- YouTube Data API

YT_API = "https://www.googleapis.com/youtube/v3"
_ISO_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_iso_duration(value: str | None) -> float | None:
    if not value:
        return None
    m = _ISO_DURATION.fullmatch(value)
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + s)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _best_thumb(thumbs: dict[str, Any] | None) -> str:
    for key in ("maxres", "standard", "high", "medium", "default"):
        if thumbs and isinstance(thumbs.get(key), dict) and thumbs[key].get("url"):
            return thumbs[key]["url"]
    return ""


class YouTubeApiProvider(Provider):
    id = "youtube_api"
    platform = "youtube"
    label = "YouTube Data API v3"
    reliability = "stable"

    @property
    def key(self) -> str:
        return self.settings.get("youtube_api_key") or ""

    def is_available(self) -> tuple[bool, str]:
        if not self.key:
            return False, "no_key"
        if viral_settings.quota_used_today() >= viral_settings.YOUTUBE_DAILY_QUOTA:
            return False, "quota_exhausted"
        return True, ""

    def capabilities(self) -> list[str]:
        return ["search", "video", "creator"]

    def limits(self) -> dict[str, Any]:
        return {
            "quota_used_today": viral_settings.quota_used_today(),
            "quota_daily": viral_settings.YOUTUBE_DAILY_QUOTA,
            "search_cost": YT_SEARCH_COST,
        }

    def _get(self, path: str, params: dict[str, Any], cost: int) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        params["key"] = self.key
        try:
            with client_factory() as client:
                response = client.get(f"{YT_API}/{path}", params=params)
        except httpx.TimeoutException as exc:
            raise SourceError("timeout", "YouTube API không phản hồi (timeout).") from exc
        except httpx.HTTPError as exc:
            raise SourceError("network", "Không kết nối được tới YouTube API.") from exc
        viral_settings.add_quota(cost)
        if response.status_code >= 400:
            reason = ""
            try:
                err = response.json().get("error", {})
                reason = ((err.get("errors") or [{}])[0].get("reason") or "") if isinstance(err, dict) else ""
            except ValueError:
                pass
            if reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
                raise SourceError("quota_exceeded", "Đã hết quota YouTube API hôm nay (reset lúc 0h giờ Thái Bình Dương).")
            if reason in {"keyInvalid", "keyExpired"} or response.status_code in (400, 401, 403) and "key" in reason.lower():
                raise SourceError("auth", "YouTube API key không hợp lệ.")
            if response.status_code == 403:
                raise SourceError("auth", f"YouTube API từ chối yêu cầu ({reason or 'forbidden'}). Kiểm tra key đã bật YouTube Data API v3.")
            raise SourceError("provider_error", f"YouTube API lỗi {response.status_code} {reason}".strip())
        return response.json()

    def search(self, q: SearchQuery) -> list[VideoItem]:
        now = utcnow()
        hours = WINDOW_HOURS.get(q.window)
        params = {
            "part": "snippet",
            "type": "video",
            "q": q.keyword,
            "order": {"views": "viewCount", "date": "date"}.get(q.order, "relevance"),
            "maxResults": max(1, min(int(q.limit), 50)),
            "publishedAfter": (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ") if hours else None,
            "regionCode": q.region or None,
            "relevanceLanguage": q.language or None,
            "videoDuration": "short" if q.duration == "short" else None,
            "safeSearch": "moderate",
        }
        cache_key = "yt_api:" + json.dumps(params, sort_keys=True)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        data = self._get("search", params, YT_SEARCH_COST)
        ids = [it.get("id", {}).get("videoId") for it in data.get("items", []) if isinstance(it, dict)]
        items = self.fetch_videos([i for i in ids if i])
        items = [it for it in items if _within(it, q, now)]
        _cache_put(cache_key, items)
        return items

    def fetch_videos(self, ids: list[str]) -> list[VideoItem]:
        items: list[VideoItem] = []
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            data = self._get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk)}, 1)
            for raw in data.get("items", []):
                sn, st, cd = raw.get("snippet", {}), raw.get("statistics", {}), raw.get("contentDetails", {})
                vid = raw.get("id")
                if not vid or sn.get("liveBroadcastContent") in {"live", "upcoming"}:
                    continue
                items.append(
                    VideoItem(
                        platform="youtube",
                        external_id=vid,
                        url=f"https://www.youtube.com/watch?v={vid}",
                        title=sn.get("title") or "",
                        description=(sn.get("description") or "")[:2000],
                        creator_name=sn.get("channelTitle") or "",
                        creator_id=sn.get("channelId") or "",
                        creator_url=f"https://www.youtube.com/channel/{sn.get('channelId')}" if sn.get("channelId") else "",
                        published_at=_parse_dt(sn.get("publishedAt")),
                        duration=parse_iso_duration(cd.get("duration")),
                        thumbnail=_best_thumb(sn.get("thumbnails")),
                        views=_int(st.get("viewCount")),
                        likes=_int(st.get("likeCount")),
                        comments=_int(st.get("commentCount")),
                        language=sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
                        source=self.id,
                    )
                )
        self._attach_followers(items)
        return items

    def _attach_followers(self, items: list[VideoItem]) -> None:
        channel_ids = sorted({it.creator_id for it in items if it.creator_id})
        followers: dict[str, int | None] = {}
        for start in range(0, len(channel_ids), 50):
            chunk = channel_ids[start : start + 50]
            data = self._get("channels", {"part": "statistics", "id": ",".join(chunk)}, 1)
            for raw in data.get("items", []):
                st = raw.get("statistics", {})
                followers[raw.get("id")] = None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount"))
        for it in items:
            it.followers = followers.get(it.creator_id)

    def fetch_creator(self, ident: str, limit: int = 20) -> list[VideoItem]:
        params: dict[str, Any] = {"part": "contentDetails"}
        if ident.startswith("@"):
            params["forHandle"] = ident
        elif ident.startswith("channel/"):
            params["id"] = ident.split("/", 1)[1]
        elif ident.startswith("user/"):
            params["forUsername"] = ident.split("/", 1)[1]
        else:
            params["forHandle"] = "@" + ident.split("/", 1)[-1]
        data = self._get("channels", params, 1)
        rows = data.get("items") or []
        if not rows:
            raise SourceError("not_found", "Không tìm thấy kênh YouTube.")
        uploads = rows[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads:
            return []
        data = self._get("playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": min(limit, 50)}, 1)
        ids = [r.get("contentDetails", {}).get("videoId") for r in data.get("items", [])]
        return self.fetch_videos([i for i in ids if i])


# --------------------------------------------------------------------- YouTube yt-dlp


def _pb_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def youtube_search_sp(order: str, window: str, duration: str) -> str:
    """Encode the ``sp`` filter of youtube.com/results (sort + upload date + type + duration)."""
    sort = {"views": 3, "date": 2}.get(order, 0)
    upload = {"24h": 2, "7d": 3, "30d": 4, "90d": 5}.get(window, 0)
    dur = {"short": 1}.get(duration, 0)
    filters = b""
    if upload:
        filters += b"\x08" + _pb_varint(upload)
    # type=video: without it YouTube mixes shelves into filtered results and yt-dlp
    # returns only a handful of entries (verified 2026-10-26).
    filters += b"\x10\x01"
    if dur:
        filters += b"\x18" + _pb_varint(dur)
    msg = b""
    if sort:
        msg += b"\x08" + _pb_varint(sort)
    if filters:
        msg += b"\x12" + _pb_varint(len(filters)) + filters
    return base64.b64encode(msg).decode("ascii") if msg else ""


def _yt_cookie_options() -> dict[str, Any]:
    try:
        from ..downloader import _ytdlp_cookie_options

        return _ytdlp_cookie_options("https://www.youtube.com/")
    except Exception:  # pragma: no cover - defensive
        return {}


def _ts_to_dt(timestamp: Any, upload_date: Any) -> datetime | None:
    ts = _int(timestamp)
    if ts:
        return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
    if upload_date and re.fullmatch(r"\d{8}", str(upload_date)):
        day = datetime.strptime(str(upload_date), "%Y%m%d")
        # Only the day is known: assume midday UTC, never in the future.
        return min(day + timedelta(hours=12), utcnow())
    return None


class YtDlpYouTubeProvider(Provider):
    id = "youtube_ytdlp"
    platform = "youtube"
    label = "YouTube (yt-dlp, không cần key)"
    reliability = "best_effort"
    concurrency = 4
    detail_deadline = 75.0

    def is_available(self) -> tuple[bool, str]:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return False, "ytdlp_missing"
        return True, ""

    def capabilities(self) -> list[str]:
        return ["search", "video", "creator"]

    def limits(self) -> dict[str, Any]:
        return {"max_results": 50, "concurrency": self.concurrency}

    def _flat(self, url: str, limit: int) -> list[dict[str, Any]]:
        try:
            data = ytdlp_extract(url, {"extract_flat": "in_playlist", "playlistend": limit, **_yt_cookie_options()})
        except Exception as exc:
            raise SourceError("provider_error", f"yt-dlp không đọc được kết quả YouTube: {str(exc)[:200]}") from exc
        return [e for e in (data.get("entries") or []) if isinstance(e, dict) and e.get("id")]

    def _detail(self, video_id: str) -> dict[str, Any]:
        return ytdlp_extract(
            f"https://www.youtube.com/watch?v={video_id}",
            {
                "_no_process": True,
                "ignore_no_formats_error": True,
                "extractor_args": {"youtube": {"player_skip": ["webpage", "configs", "js"], "skip": ["dash", "hls", "translated_subs"]}},
                **_yt_cookie_options(),
            },
        )

    def _item(self, entry: dict[str, Any], detail: dict[str, Any] | None) -> VideoItem:
        d = detail or {}
        vid = entry.get("id") or d.get("id")
        thumbs = entry.get("thumbnails") or []
        thumb = (thumbs[-1].get("url") if thumbs and isinstance(thumbs[-1], dict) else "") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        channel_id = d.get("channel_id") or entry.get("channel_id") or ""
        return VideoItem(
            platform="youtube",
            external_id=vid,
            url=f"https://www.youtube.com/watch?v={vid}",
            title=d.get("title") or entry.get("title") or "",
            description=(d.get("description") or entry.get("description") or "")[:2000],
            creator_name=d.get("channel") or entry.get("channel") or entry.get("uploader") or "",
            creator_id=channel_id,
            creator_url=d.get("channel_url") or entry.get("channel_url") or (f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""),
            followers=_int(d.get("channel_follower_count") or entry.get("channel_follower_count")),
            published_at=_ts_to_dt(d.get("timestamp") or entry.get("timestamp"), d.get("upload_date") or entry.get("upload_date")),
            duration=float(entry.get("duration") or d.get("duration") or 0) or None,
            thumbnail=thumb,
            views=_int(d.get("view_count")) or _int(entry.get("view_count")),
            likes=_int(d.get("like_count")),
            comments=_int(d.get("comment_count")),
            language=d.get("language"),
            source=self.id,
        )

    def _enrich(self, entries: list[dict[str, Any]]) -> list[VideoItem]:
        details: dict[str, dict[str, Any]] = {}
        pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="viral-ytdlp")
        try:
            futures = {pool.submit(self._detail, e["id"]): e["id"] for e in entries}
            done, _ = wait(futures, timeout=self.detail_deadline)
            for fut in done:
                try:
                    details[futures[fut]] = fut.result()
                except Exception as exc:  # one bad video must not sink the scan
                    logger.info("yt-dlp detail failed for %s: %s", futures[fut], exc)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return [self._item(e, details.get(e["id"])) for e in entries]

    def search(self, q: SearchQuery) -> list[VideoItem]:
        now = utcnow()
        limit = max(1, min(int(q.limit), 50))
        url = f"https://www.youtube.com/results?search_query={quote(q.keyword)}"
        sp = youtube_search_sp(q.order, q.window, q.duration)
        if sp:
            url += f"&sp={quote(sp, safe='')}"
        if q.region:
            url += f"&gl={q.region}"
        cache_key = f"yt_dlp:{url}:{limit}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        entries = [e for e in self._flat(url, limit) if e.get("live_status") not in {"is_live", "is_upcoming"}]
        if q.duration == "short":
            entries = [e for e in entries if not e.get("duration") or e["duration"] <= SHORT_MAX_SECONDS]
        elif q.duration == "long":
            entries = [e for e in entries if not e.get("duration") or e["duration"] > SHORT_MAX_SECONDS]
        items = [it for it in self._enrich(entries[:limit]) if _within(it, q, now)]
        _cache_put(cache_key, items)
        return items

    def fetch_videos(self, ids: list[str]) -> list[VideoItem]:
        return self._enrich([{"id": i} for i in ids])

    def fetch_creator(self, ident: str, limit: int = 20) -> list[VideoItem]:
        entries = self._flat(f"https://www.youtube.com/{ident}/videos", limit)
        return self._enrich(entries[:limit])


# ------------------------------------------------------------------------------ Douyin

DOUYIN_HOT_URL = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def douyin_hot_words(limit: int = 30) -> list[dict[str, Any]]:
    """Douyin's public hot-search board (keywords only; no signing needed)."""
    cached = _cache_get("douyin_hot")
    if cached is None:
        try:
            with client_factory() as client:
                response = client.get(DOUYIN_HOT_URL, headers={"User-Agent": _MOBILE_UA, "Referer": "https://www.douyin.com/"})
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError("network", "Không tải được bảng hot search Douyin.") from exc
        words = data.get("word_list") if isinstance(data, dict) else None
        if not isinstance(words, list):
            raise SourceError("provider_error", "Douyin không trả về bảng hot search.")
        cached = [
            {"word": w.get("word"), "hot_value": _int(w.get("hot_value")), "label": w.get("label")}
            for w in words
            if isinstance(w, dict) and w.get("word")
        ]
        _cache_put("douyin_hot", cached, ttl=600)
    return cached[:limit]


class DouyinPublicProvider(Provider):
    id = "douyin_public"
    platform = "douyin"
    label = "Douyin công khai (không cần key)"
    reliability = "best_effort"

    def is_available(self) -> tuple[bool, str]:
        if self.settings.get("douyin_mode") == "off":
            return False, "disabled"
        return True, ""

    def capabilities(self) -> list[str]:
        return ["hot_words", "video_basic"]

    def limits(self) -> dict[str, Any]:
        return {"stats": False, "keyword_search": False}

    def search(self, q: SearchQuery) -> list[VideoItem]:
        raise SourceError(
            "needs_key",
            "Tìm video Douyin theo từ khóa cần API bên thứ ba (TikHub). Không có key: dùng bảng từ khóa hot hoặc dán link.",
        )

    def fetch_videos(self, ids: list[str]) -> list[VideoItem]:
        items = []
        for vid in ids:
            url = f"https://www.douyin.com/video/{vid}"
            try:
                payload = snapvideotools_lookup(url)
            except Exception as exc:
                logger.info("Douyin public lookup failed for %s: %s", vid, exc)
                items.append(VideoItem(platform="douyin", external_id=vid, url=url, source=self.id))
                continue
            data = payload.get("data") if isinstance(payload, dict) else None
            data = data if isinstance(data, dict) else {}
            items.append(
                VideoItem(
                    platform="douyin",
                    external_id=vid,
                    url=url,
                    title=str(data.get("title") or ""),
                    thumbnail=str(data.get("cover") or ""),
                    source=self.id,
                )
            )
        return items


def _walk_awemes(node: Any, out: list[dict[str, Any]], seen: set[str]) -> None:
    if isinstance(node, dict):
        aweme_id = str(node.get("aweme_id") or "")
        if aweme_id and ("statistics" in node or "desc" in node) and aweme_id not in seen:
            seen.add(aweme_id)
            out.append(node)
            return
        for value in node.values():
            _walk_awemes(value, out, seen)
    elif isinstance(node, list):
        for value in node:
            _walk_awemes(value, out, seen)


def parse_aweme(raw: dict[str, Any], source: str) -> VideoItem:
    stats = raw.get("statistics") or {}
    author = raw.get("author") or {}
    video = raw.get("video") or {}
    duration = _int(raw.get("duration") or video.get("duration"))
    if duration and duration > 1000:  # Douyin reports milliseconds
        duration = duration / 1000.0
    cover = ""
    for key in ("origin_cover", "cover", "dynamic_cover"):
        urls = (video.get(key) or {}).get("url_list") if isinstance(video.get(key), dict) else None
        if urls:
            cover = urls[0]
            break
    created = _int(raw.get("create_time"))
    sec_uid = author.get("sec_uid") or ""
    aweme_id = str(raw.get("aweme_id"))
    return VideoItem(
        platform="douyin",
        external_id=aweme_id,
        url=f"https://www.douyin.com/video/{aweme_id}",
        title=str(raw.get("desc") or raw.get("preview_title") or ""),
        creator_name=author.get("nickname") or "",
        creator_id=sec_uid or str(author.get("uid") or ""),
        creator_url=f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "",
        followers=_int(author.get("follower_count")),
        published_at=datetime.fromtimestamp(created, timezone.utc).replace(tzinfo=None) if created else None,
        duration=float(duration) if duration else None,
        thumbnail=cover,
        views=_int(stats.get("play_count")) or None,
        likes=_int(stats.get("digg_count")),
        comments=_int(stats.get("comment_count")),
        shares=_int(stats.get("share_count")),
        collects=_int(stats.get("collect_count")),
        language="zh",
        source=source,
    )


class TikHubDouyinProvider(Provider):
    id = "douyin_tikhub"
    platform = "douyin"
    label = "Douyin qua TikHub API (cần key)"
    reliability = "unverified"

    @property
    def key(self) -> str:
        return self.settings.get("tikhub_api_key") or ""

    @property
    def base_url(self) -> str:
        return (self.settings.get("tikhub_base_url") or viral_settings.DEFAULT_TIKHUB_BASE_URL).rstrip("/")

    def is_available(self) -> tuple[bool, str]:
        if self.settings.get("douyin_mode") == "off":
            return False, "disabled"
        if not self.key:
            return False, "no_key"
        return True, ""

    def capabilities(self) -> list[str]:
        return ["search", "video", "creator"]

    def limits(self) -> dict[str, Any]:
        return {"paid": True}

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            with client_factory() as client:
                response = client.get(
                    f"{self.base_url}{path}",
                    params={k: v for k, v in params.items() if v is not None},
                    headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise SourceError("timeout", "API Douyin bên thứ ba không phản hồi (timeout).") from exc
        except httpx.HTTPError as exc:
            raise SourceError("network", "Không kết nối được tới API Douyin bên thứ ba.") from exc
        if response.status_code in (401, 403):
            raise SourceError("auth", "API key bên thứ ba không hợp lệ hoặc hết hạn.")
        if response.status_code == 402:
            raise SourceError("payment_required", "Tài khoản API bên thứ ba đã hết số dư.")
        if response.status_code == 429:
            raise SourceError("rate_limited", "API bên thứ ba đang giới hạn tần suất, thử lại sau.")
        if response.status_code >= 400:
            raise SourceError("provider_error", f"API bên thứ ba lỗi {response.status_code}.")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError("provider_error", "API bên thứ ba trả về dữ liệu không hợp lệ.") from exc

    def _items(self, payload: Any) -> list[VideoItem]:
        raws: list[dict[str, Any]] = []
        _walk_awemes(payload, raws, set())
        return [parse_aweme(r, self.id) for r in raws]

    def search(self, q: SearchQuery) -> list[VideoItem]:
        now = utcnow()
        params = {
            "keyword": q.keyword,
            "offset": 0,
            "count": max(1, min(int(q.limit), 30)),
            "sort_type": {"views": 1, "date": 2}.get(q.order, 0),  # 0 general, 1 most liked, 2 newest
            "publish_time": {"24h": 1, "7d": 7, "30d": 180, "90d": 180}.get(q.window, 0),
            "filter_duration": 0,
        }
        cache_key = "tikhub:" + json.dumps(params, sort_keys=True, ensure_ascii=False)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        items = self._items(self._get("/api/v1/douyin/web/fetch_video_search_result", params))
        items = [it for it in items if _within(it, q, now)]
        _cache_put(cache_key, items)
        return items

    def fetch_videos(self, ids: list[str]) -> list[VideoItem]:
        out = []
        for vid in ids:
            out.extend(self._items(self._get("/api/v1/douyin/web/fetch_one_video", {"aweme_id": vid}))[:1])
        return out

    def fetch_creator(self, ident: str, limit: int = 20) -> list[VideoItem]:
        payload = self._get(
            "/api/v1/douyin/web/fetch_user_post_videos", {"sec_user_id": ident, "max_cursor": 0, "count": min(limit, 30)}
        )
        return self._items(payload)[:limit]


# ----------------------------------------------------------------------------- registry

ALL_PROVIDERS = (YouTubeApiProvider, YtDlpYouTubeProvider, DouyinPublicProvider, TikHubDouyinProvider)


def availability_report(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = settings if settings is not None else viral_settings.load_settings()
    return [cls(settings).availability() for cls in ALL_PROVIDERS]


def pick_provider(platform: str, settings: dict[str, Any] | None = None) -> Provider:
    """The provider a scan uses for ``platform`` given the user's settings."""
    settings = settings if settings is not None else viral_settings.load_settings()
    if platform == "youtube":
        mode = settings.get("youtube_mode") or "auto"
        api = YouTubeApiProvider(settings)
        if mode == "api" or (mode == "auto" and api.is_available()[0]):
            return api
        return YtDlpYouTubeProvider(settings)
    if platform == "douyin":
        tikhub = TikHubDouyinProvider(settings)
        if settings.get("douyin_mode") == "tikhub" and tikhub.is_available()[0]:
            return tikhub
        return DouyinPublicProvider(settings)
    raise SourceError("unsupported", f"Nền tảng không hỗ trợ: {platform}")


def estimate_youtube_quota(keyword_count: int, limit: int) -> int:
    """Units one YouTube API scan costs: search per keyword + videos/channels lists."""
    return keyword_count * (YT_SEARCH_COST + 2 * max(1, -(-int(limit) // 50)))
