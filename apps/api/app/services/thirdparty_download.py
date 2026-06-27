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

Observed request shapes (verified 2026-06-27 against live Douyin/TikTok videos):

* tikwm: ``GET https://www.tikwm.com/api/?url=<url>&hd=1`` returns
  ``{"code": 0, "data": {"hdplay": ..., "play": ..., "wmplay": ...}}``. Best for
  TikTok (returns direct CDN URLs); often refuses Douyin ("Url parsing is
  failed"), so Douyin falls through to the next provider. Relative ``/video/...``
  values are tikwm-hosted and must be prefixed with the tikwm origin.
* snapvideotools: ``POST /vi/api/snap`` with JSON ``{"text": ...}`` returns
  ``{"code": 0, "data": {"mediaUrls": [{"type": "video", "url": "<direct CDN
  url>", "suffix": "mp4"}]}}``. Handles Douyin (returns a ``*.zjcdn.com`` URL).
* unduhtiktok: GET ``.../app-snaptik/api/check.php`` first for a ``PHPSESSID``
  cookie, then ``POST .../app-snaptik/api/tiktok.php`` JSON ``{"url": ...}``.
  Returns ``{"video": "<proxy url>", ...}`` where ``video`` is the site's OWN
  ``download.php`` proxy and must be fetched with the SAME session + referer.

IMPORTANT referer rule (this was the long-standing Douyin breakage):
Direct CDN URLs (tikwm / snapvideotools) must be streamed WITHOUT the provider
site's referer — Douyin's CDN returns 403 when the referer is some downloader
site. Stream those with no referer (or the douyin.com referer for Douyin CDNs).
Only the unduhtiktok self-proxy needs its own site referer + session cookie.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

logger = logging.getLogger(__name__)

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_TIKWM_BASE = "https://www.tikwm.com"
_TIKWM_API = f"{_TIKWM_BASE}/api/"
_UNDUHTIKTOK_BASE = "https://unduhtiktok.com"
_UNDUHTIKTOK_CHECK = f"{_UNDUHTIKTOK_BASE}/wp-content/plugins/app-snaptik/api/check.php"
_UNDUHTIKTOK_API = f"{_UNDUHTIKTOK_BASE}/wp-content/plugins/app-snaptik/api/tiktok.php"
_SNAPVIDEOTOOLS_BASE = "https://snapvideotools.com"

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
# on Douyin; snapvideotools then handles Douyin; unduhtiktok is the last resort.
_PROVIDERS = (
    ("tikwm", lambda url, dst: _provider_tikwm(url, dst)),
    ("snapvideotools", lambda url, dst: _provider_snapvideotools(url, dst)),
    ("unduhtiktok", lambda url, dst: _provider_unduhtiktok(url, dst)),
)


def download_via_thirdparty(url: str, root: Path, job_id: str) -> Path | None:
    """Try each third-party provider in turn; return a saved file or None.

    Never raises: a provider error is logged and we move on to the next one, and
    an exhausted list returns ``None`` so the caller's existing error path runs.
    """
    if not is_thirdparty_supported(url):
        return None

    raw_dir = root / "raw-videos"
    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / f"{job_id}.mp4"

    for name, provider in _PROVIDERS:
        try:
            _remove_existing(destination)
            if provider(url, destination) and _has_content(destination):
                logger.info("third-party download succeeded via %s for %s", name, url)
                return destination
            logger.info("third-party provider %s yielded no video for %s", name, url)
        except Exception as exc:
            logger.warning("third-party provider %s failed for %s: %s", name, url, exc)
            continue

    _remove_existing(destination)
    return None


def _provider_tikwm(url: str, destination: Path) -> bool:
    payload = _tikwm_request(url)
    if payload is None:
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    # Prefer no-watermark HD, then SD, then watermarked as a last resort.
    for key in ("hdplay", "play", "wmplay"):
        media = str(data.get(key) or "").strip()
        if not media:
            continue
        if media.startswith("/"):
            media = f"{_TIKWM_BASE}{media}"
        if _stream_cdn(media, destination) and _has_content(destination):
            return True
        _remove_existing(destination)
    return False


def _tikwm_request(url: str) -> dict | None:
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
            return None
        if payload.get("code") == 0:
            return payload
        message = str(payload.get("msg") or "")
        # tikwm throttles anonymous callers; one short backoff usually clears it.
        if attempt == 0 and "limit" in message.lower():
            time.sleep(1.5)
            continue
        logger.info("tikwm declined %s: %s", url, message or payload.get("code"))
        return None
    return None


def _provider_snapvideotools(url: str, destination: Path) -> bool:
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
    if not isinstance(payload, dict) or payload.get("code") == -1:
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    media = data.get("mediaUrls")
    if not isinstance(media, list):
        return False

    video_url = next(
        (
            str(item.get("url") or "").strip()
            for item in media
            if isinstance(item, dict)
            and item.get("type") == "video"
            and str(item.get("url") or "").strip()
        ),
        "",
    )
    if not video_url:
        return False
    # mediaUrls carry direct CDN URLs (e.g. *.zjcdn.com): stream WITHOUT the
    # snapvideotools referer, which the Douyin CDN rejects with 403.
    return _stream_cdn(video_url, destination)


def _provider_unduhtiktok(url: str, destination: Path) -> bool:
    session = requests.Session()
    session.headers.update({"User-Agent": _DESKTOP_UA})
    referer = f"{_UNDUHTIKTOK_BASE}/vi/douyin/"
    # check.php establishes the PHPSESSID that tiktok.php and the download
    # proxy validate; without it tiktok.php returns "Invalid token".
    session.get(_UNDUHTIKTOK_CHECK, headers={"Referer": referer}, timeout=25)

    response = session.post(
        _UNDUHTIKTOK_API,
        json={"url": url},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
            "Origin": _UNDUHTIKTOK_BASE,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return False
    video_url = str(payload.get("video") or "").strip()
    if not video_url:
        return False
    # The download.php proxy is currently broken (returns 0 bytes). Its ?token=
    # param is base64 of the real CDN/snapcdn URL — decode and stream that
    # directly; fall back to the proxy (with session) only if decoding fails.
    direct = _decode_unduhtiktok_target(video_url)
    if direct:
        return _stream_cdn(direct, destination)
    return _stream_proxy(video_url, destination, session=session, referer=referer)


def _decode_unduhtiktok_target(proxy_url: str) -> str | None:
    """Recover the real media URL from an unduhtiktok download.php proxy link.

    The ``?token=`` value is base64 of the underlying ``https://...`` URL. Returns
    that URL, or None if there is no token or it does not decode to an HTTP URL.
    """
    token = parse_qs(urlparse(proxy_url).query).get("token", [""])[0]
    if not token:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", "replace")
    except Exception:
        return None
    return decoded if decoded.startswith(("http://", "https://")) else None


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


def _stream_proxy(
    media_url: str,
    destination: Path,
    session: requests.Session,
    referer: str,
) -> bool:
    """Stream a provider's own proxy URL using its session + site referer."""
    stream = session.get(
        media_url,
        headers={"User-Agent": _DESKTOP_UA, "Referer": referer},
        stream=True,
        timeout=180,
    )
    return _write_stream(stream, destination)


def _write_stream(stream: requests.Response, destination: Path) -> bool:
    if not stream.ok:
        logger.info("third-party stream %s returned HTTP %s", stream.url, stream.status_code)
        return False
    content_type = (stream.headers.get("Content-Type") or "").lower()
    # Reject HTML error pages masquerading as a download.
    if "text/html" in content_type:
        logger.info("third-party stream %s returned HTML, not a video", stream.url)
        return False
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
