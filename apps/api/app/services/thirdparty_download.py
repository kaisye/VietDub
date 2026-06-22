"""Best-effort third-party download fallbacks for Douyin/TikTok.

These providers scrape public "video downloader" web services and are used only
as a LAST RESORT when yt-dlp fails (it lags behind Douyin's X-Bogus/A-Bogus
signature changes). They are:

* Enabled by default for public Douyin/TikTok URLs. Set
  ``AETHER_THIRDPARTY_DOWNLOAD_ENABLED=0`` to disable it.
* Fail-safe. Any error returns ``None`` so the normal error path is preserved.
* Fragile by nature. They depend on undocumented HTML/JSON endpoints that can
  change or add Cloudflare challenges at any time. Treat breakage as expected
  and gate everything behind the env flag so it can be turned off instantly.
* Public-only. These services use their own cookie pool, so they cannot reach
  private / follower-only videos (use AETHER_DOUYIN_COOKIES_FILE for those).

Observed request shapes (verified 2026-06 against a live Douyin video):

* unduhtiktok: GET ``.../app-snaptik/api/check.php`` first to obtain a
  ``PHPSESSID`` cookie, then ``POST .../app-snaptik/api/tiktok.php`` with JSON
  ``{"url": ...}`` (same session). Returns ``{"video": "<proxy url>", "music":
  ..., "desc": ..., "imagePost": [...]}``. ``video`` points at the site's own
  ``download.php`` proxy and must be fetched with the SAME session cookie.
* snapvideotools: ``POST /vi/api/snap`` with JSON ``{"text": ...}`` returns
  ``{"code": 0, "data": {"mediaUrls": [{"type": "video", "url": "<direct CDN
  url>", "suffix": "mp4"}]}}``. Each item carries a direct, token-signed CDN
  URL that can be streamed without a session.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import requests

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_UNDUHTIKTOK_BASE = "https://unduhtiktok.com"
_UNDUHTIKTOK_CHECK = f"{_UNDUHTIKTOK_BASE}/wp-content/plugins/app-snaptik/api/check.php"
_UNDUHTIKTOK_API = f"{_UNDUHTIKTOK_BASE}/wp-content/plugins/app-snaptik/api/tiktok.php"
_SNAPVIDEOTOOLS_BASE = "https://snapvideotools.com"


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


def download_via_thirdparty(url: str, root: Path, job_id: str) -> Path | None:
    """Try each third-party provider in turn; return a saved file or None.

    Never raises: a provider error simply moves on to the next one, and an
    exhausted list returns ``None`` so the caller's existing error path runs.
    """
    if not is_thirdparty_supported(url):
        return None

    raw_dir = root / "raw-videos"
    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / f"{job_id}.mp4"

    for provider in (_provider_snapvideotools, _provider_unduhtiktok):
        try:
            _remove_existing(destination)
            if provider(url, destination) and _has_content(destination):
                return destination
        except Exception:
            continue

    _remove_existing(destination)
    return None


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
    # video is the site's own download.php proxy; it needs the same session.
    return _stream_to_file(video_url, destination, session=session, referer=referer)


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
    return _stream_to_file(video_url, destination, referer=f"{_SNAPVIDEOTOOLS_BASE}/vi")


def _stream_to_file(
    media_url: str,
    destination: Path,
    session: requests.Session | None = None,
    referer: str | None = None,
) -> bool:
    getter = session.get if session is not None else requests.get
    headers = {"User-Agent": _DESKTOP_UA}
    if referer:
        headers["Referer"] = referer
    stream = getter(media_url, headers=headers, stream=True, timeout=180)
    return _write_stream(stream, destination)


def _write_stream(stream: requests.Response, destination: Path) -> bool:
    if not stream.ok:
        return False
    content_type = (stream.headers.get("Content-Type") or "").lower()
    # Reject HTML error pages masquerading as a download.
    if "text/html" in content_type:
        return False
    written = 0
    with destination.open("wb") as handle:
        for chunk in stream.iter_content(chunk_size=1 << 16):
            if chunk:
                handle.write(chunk)
                written += len(chunk)
    return written > 0


def _has_content(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _remove_existing(path: Path) -> None:
    if path.exists():
        path.unlink(missing_ok=True)
