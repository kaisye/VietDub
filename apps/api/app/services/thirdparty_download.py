"""Best-effort third-party download fallbacks for Douyin/TikTok.

These providers scrape public "video downloader" web services and are used only
as a LAST RESORT when yt-dlp fails (it lags behind Douyin's X-Bogus/A-Bogus
signature changes). They are:

* Enabled by default for public Douyin/TikTok URLs. Set
  ``AETHER_THIRDPARTY_DOWNLOAD_ENABLED=0`` to disable it.
* Fail-safe. Any error is logged and returns ``None`` so the normal error path
  is preserved.
* Fragile by nature. They depend on undocumented HTML/JSON endpoints that can
  change or add Cloudflare challenges at any time. Treat breakage as expected
  and gate everything behind the env flag so it can be turned off instantly.
* Public-only. These services use their own cookie pool, so they cannot reach
  private / follower-only videos (use AETHER_DOUYIN_COOKIES_FILE for those).

Observed request shapes (re-verified 2026-07-19 against live Douyin videos):

* tikwm: ``GET https://www.tikwm.com/api/?url=<url>&hd=1`` returns
  ``{"code": 0, "data": {"hdplay": ..., "play": ..., "wmplay": ...}}``. Best for
  TikTok (returns direct CDN URLs); consistently refuses Douyin ("Url parsing is
  failed"), so Douyin falls through to the next provider. Relative ``/video/...``
  values are tikwm-hosted and must be prefixed with the tikwm origin.
* snapvideotools: ``POST /vi/api/snap`` with JSON ``{"text": ...}`` returns
  ``{"code": 0, "data": {"mediaUrls": [{"type": "video", "url": "<direct CDN
  url>", "suffix": "mp4"}]}}``. Handles Douyin (returns a ``*.zjcdn.com`` URL)
  and is currently the ONLY provider that does, so its transient ``code: -1``
  ("Video parsing failed, please try again later") is retried with a backoff
  instead of being treated as a hard decline.

A third provider, unduhtiktok, was removed on 2026-07-19: its ``check.php`` no
longer issues a PHPSESSID and ``tiktok.php`` returns a 404 HTML page, so it only
ever burned request timeouts before failing.

IMPORTANT referer rule (this was the long-standing Douyin breakage):
Direct CDN URLs must be streamed WITHOUT the provider site's referer — Douyin's
CDN returns 403 when the referer is some downloader site. Stream those with no
referer, or the douyin.com referer for Douyin CDNs.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """A provider declined or failed with a reason worth showing the user."""

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_TIKWM_BASE = "https://www.tikwm.com"
_TIKWM_API = f"{_TIKWM_BASE}/api/"
_SNAPVIDEOTOOLS_BASE = "https://snapvideotools.com"

# snapvideotools is a free web tool that rejects bursts; these back-offs turn its
# transient "try again later" into a retry instead of a failed download.
_SNAPVIDEOTOOLS_RETRY_DELAYS = (2.0, 5.0)

# Hosts whose CDN expects a douyin.com referer (others are streamed referer-less).
_DOUYIN_CDN_HINTS = ("zjcdn.com", "douyinvod.com", "douyincdn.com", "bytecdn", "amemv", "ixigua")

# Reject obvious error stubs masquerading as a video (real clips are far larger).
_MIN_VIDEO_BYTES = 10_000


def thirdparty_fallback_enabled() -> bool:
    return os.getenv("AETHER_THIRDPARTY_DOWNLOAD_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }


def is_thirdparty_supported(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (
        host.endswith("douyin.com")
        or host.endswith("tiktok.com")
        or host.endswith("iesdouyin.com")
    )


# Order matters: tikwm is fastest/most reliable for TikTok and bows out quickly
# on Douyin; snapvideotools then handles Douyin and is its only working provider.
_PROVIDERS = (
    ("tikwm", lambda url, dst: _provider_tikwm(url, dst)),
    ("snapvideotools", lambda url, dst: _provider_snapvideotools(url, dst)),
)


def download_via_thirdparty(
    url: str, root: Path, job_id: str, *alternate_urls: str
) -> tuple[Path | None, list[str]]:
    """Try each third-party provider in turn, over each URL form given.

    ``alternate_urls`` matters more than it looks: the URL *form* decides which
    signed CDN link snapvideotools mints, and the two forms are not equivalent.
    For the same Douyin video, at the same moment, the share form
    ``/jingxuan?modal_id=<id>`` yielded links that streamed (HTTP 206) while the
    canonical ``/video/<id>`` yielded links the CDN rejected with 403 — so the
    caller passes the user's original URL alongside the canonical one that
    yt-dlp needs, and we take whichever produces a playable file.

    Returns the saved file (or ``None``) together with the failure reasons, so
    the caller can tell the user what actually went wrong instead of guessing.
    Never raises: a provider error is recorded and we move on.
    """
    candidates: list[str] = []
    for candidate in (url, *alternate_urls):
        if candidate and candidate not in candidates and is_thirdparty_supported(candidate):
            candidates.append(candidate)
    if not candidates:
        return None, []

    raw_dir = root / "raw-videos"
    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / f"{job_id}.mp4"

    failures: list[str] = []
    for candidate in candidates:
        for name, provider in _PROVIDERS:
            try:
                _remove_existing(destination)
                if provider(candidate, destination) and _has_content(destination):
                    logger.info(
                        "third-party download succeeded via %s for %s", name, candidate
                    )
                    return destination, failures
                reason = f"{name}: không trả về video nào"
                logger.info(
                    "third-party provider %s yielded no video for %s", name, candidate
                )
            except Exception as exc:
                reason = f"{name}: {exc}"
                logger.warning(
                    "third-party provider %s failed for %s: %s", name, candidate, exc
                )
            # Both URL forms usually fail the same way; report each reason once.
            if reason not in failures:
                failures.append(reason)

    _remove_existing(destination)
    return None, failures


def _provider_tikwm(url: str, destination: Path) -> bool:
    data = _tikwm_request(url).get("data")
    if not isinstance(data, dict):
        raise ProviderError("phản hồi thiếu trường data")
    # Prefer no-watermark HD, then SD, then watermarked as a last resort. A dead
    # link on one quality must not abort the others, so streaming errors are held
    # back and only reported if every quality fails.
    last_error: Exception | None = None
    for key in ("hdplay", "play", "wmplay"):
        media = str(data.get(key) or "").strip()
        if not media:
            continue
        if media.startswith("/"):
            media = f"{_TIKWM_BASE}{media}"
        try:
            if _stream_cdn(media, destination) and _has_content(destination):
                return True
        except Exception as exc:
            last_error = exc
        _remove_existing(destination)
    if last_error is not None:
        raise last_error
    return False


def _tikwm_request(url: str) -> dict:
    headers = {
        "User-Agent": _DESKTOP_UA,
        "Accept": "application/json",
        "Referer": f"{_TIKWM_BASE}/",
    }
    for attempt in range(2):
        response = requests.get(
            _TIKWM_API, params={"url": url, "hd": 1}, headers=headers, timeout=45
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderError("phản hồi không phải JSON hợp lệ")
        if payload.get("code") == 0:
            return payload
        message = str(payload.get("msg") or "")
        # tikwm throttles anonymous callers; one short backoff usually clears it.
        if attempt == 0 and "limit" in message.lower():
            time.sleep(1.5)
            continue
        logger.info("tikwm declined %s: %s", url, message or payload.get("code"))
        raise ProviderError(message or f"từ chối với code {payload.get('code')}")
    raise ProviderError("bị giới hạn tần suất")


def _provider_snapvideotools(url: str, destination: Path) -> bool:
    data = _snapvideotools_request(url).get("data")
    if not isinstance(data, dict):
        raise ProviderError("phản hồi thiếu trường data")
    media = data.get("mediaUrls")
    if not isinstance(media, list):
        raise ProviderError("phản hồi thiếu danh sách mediaUrls")

    video_urls = [
        str(item.get("url") or "").strip()
        for item in media
        if isinstance(item, dict)
        and item.get("type") == "video"
        and str(item.get("url") or "").strip()
    ]
    if not video_urls:
        # A Douyin photo post (图集) parses fine but carries only image entries.
        kinds = sorted({str(item.get("type")) for item in media if isinstance(item, dict)})
        raise ProviderError(
            f"bài đăng không có video (chỉ có: {', '.join(kinds) or 'không có media'})"
        )

    # A response mixes direct CDN links with snapvideotools' own dl.snapcdn.app
    # proxy, in no fixed order, and any single one may be rejected by the CDN.
    # Taking only the first entry was enough to sink the whole download, so try
    # them all and keep the first that actually streams. mediaUrls carry direct
    # CDN URLs (e.g. *.zjcdn.com): stream WITHOUT the snapvideotools referer,
    # which the Douyin CDN rejects with 403.
    last_error: Exception | None = None
    for video_url in video_urls:
        try:
            if _stream_cdn(video_url, destination) and _has_content(destination):
                return True
        except Exception as exc:
            last_error = exc
        _remove_existing(destination)
    if last_error is not None:
        raise last_error
    return False


def _snapvideotools_request(url: str) -> dict:
    """POST the snap endpoint, retrying its transient ``code: -1`` refusals.

    snapvideotools is the only provider that still handles Douyin, so a burst
    refusal here used to sink the whole download. Its message does not separate
    "busy" from "no such video", hence the blind retry with a short backoff.
    """
    message = ""
    for attempt in range(1 + len(_SNAPVIDEOTOOLS_RETRY_DELAYS)):
        response = requests.post(
            f"{_SNAPVIDEOTOOLS_BASE}/vi/api/snap",
            json={"text": url},
            headers={
                "User-Agent": _DESKTOP_UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Referer": f"{_SNAPVIDEOTOOLS_BASE}/vi",
                "Origin": _SNAPVIDEOTOOLS_BASE,
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderError("phản hồi không phải JSON hợp lệ")
        if payload.get("code") != -1:
            return payload
        message = str(payload.get("message") or "").strip()
        if attempt < len(_SNAPVIDEOTOOLS_RETRY_DELAYS):
            logger.info("snapvideotools refused %s (%s), retrying", url, message)
            time.sleep(_SNAPVIDEOTOOLS_RETRY_DELAYS[attempt])
    raise ProviderError(message or "từ chối phân tích video sau nhiều lần thử")


def _cdn_referer(media_url: str) -> str | None:
    """Referer to use when streaming a direct CDN URL.

    Douyin CDNs want a douyin.com referer; everything else is streamed without a
    referer (sending a downloader-site referer is exactly what triggers 403s).
    """
    host = (urlparse(media_url).hostname or "").lower()
    if any(hint in host for hint in _DOUYIN_CDN_HINTS):
        return "https://www.douyin.com/"
    return None


def _stream_cdn(media_url: str, destination: Path) -> bool:
    """Stream a direct CDN URL (no session, host-appropriate referer)."""
    headers = {"User-Agent": _DESKTOP_UA, "Range": "bytes=0-"}
    referer = _cdn_referer(media_url)
    if referer:
        headers["Referer"] = referer
    stream = requests.get(media_url, headers=headers, stream=True, timeout=180)
    return _write_stream(stream, destination)


def _write_stream(stream: requests.Response, destination: Path) -> bool:
    if not stream.ok:
        logger.info("third-party stream %s returned HTTP %s", stream.url, stream.status_code)
        raise ProviderError(f"tải media thất bại với HTTP {stream.status_code}")
    content_type = (stream.headers.get("Content-Type") or "").lower()
    # Reject HTML error pages masquerading as a download.
    if "text/html" in content_type:
        logger.info("third-party stream %s returned HTML, not a video", stream.url)
        raise ProviderError("link media trả về trang HTML, không phải video")
    written = 0
    with destination.open("wb") as handle:
        for chunk in stream.iter_content(chunk_size=1 << 16):
            if chunk:
                handle.write(chunk)
                written += len(chunk)
    return written > 0


def _has_content(path: Path) -> bool:
    return path.exists() and path.stat().st_size > _MIN_VIDEO_BYTES


def _remove_existing(path: Path) -> None:
    if path.exists():
        path.unlink(missing_ok=True)
