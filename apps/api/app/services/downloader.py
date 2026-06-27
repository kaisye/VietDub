from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlretrieve

import requests

from app.models import MediaJob

from .storage import ensure_storage


def download_video(
    job: MediaJob,
    download_quality: str | None = None,
    download_subtitles: bool = True,
) -> Path:
    """Download a source video to local storage.

    Supports local file paths, direct media URLs, and common video platforms via
    yt-dlp. In tests only, AETHER_ALLOW_SYNTHETIC_SOURCE=1 can create a short
    local source video when the remote URL is intentionally not downloadable.

    When the job requests a test clip (``test_clip_seconds`` > 0) the result is
    limited to the first N seconds: for yt-dlp sources only that section is
    downloaded (fast), and every source is then trimmed as a safety net so the
    rest of the pipeline only processes the clip.
    """
    clip_seconds = _test_clip_seconds(job)
    path = _acquire_source(job, download_quality, download_subtitles, clip_seconds)
    if clip_seconds > 0:
        path = _trim_to_clip(path, clip_seconds)
    return path


def download_reference_audio(video_url: str, audio_format: str = "wav") -> Path:
    """Download only the audio track of a supported media URL."""
    import yt_dlp

    source = _resolve_video_source(_normalize_video_source(video_url))
    if not source or urlparse(source).scheme not in {"http", "https"}:
        raise ValueError("Video URL must be an HTTP(S) URL.")
    output_format = audio_format.strip().lower()
    if output_format not in {"wav", "mp3"}:
        raise ValueError("Reference audio format must be WAV or MP3.")

    root = ensure_storage()
    reference_id = str(uuid.uuid4())
    output_template = str(root / "voice-references" / f"{reference_id}.%(ext)s")
    options: dict[str, object] = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": output_template,
        "quiet": True,
        "retries": 2,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": output_format,
                **({"preferredquality": "192"} if output_format == "mp3" else {}),
            }
        ],
    }
    options.update(_ytdlp_js_options(source))
    options.update(_ytdlp_cookie_options(source))
    headers = _platform_http_headers(source)
    if headers:
        options["http_headers"] = headers
    _reject_unsupported_live_source(source, options)

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([source])
    except Exception as exc:
        fallback = _try_thirdparty_fallback(source, root, f"voice-{reference_id}")
        if fallback is None:
            raise RuntimeError(f"Unable to download reference audio with yt-dlp: {exc}") from exc
        destination = root / "voice-references" / f"{reference_id}.{output_format}"
        try:
            _extract_audio(fallback, destination, output_format)
        finally:
            fallback.unlink(missing_ok=True)
        return destination

    destination = root / "voice-references" / f"{reference_id}.{output_format}"
    if not destination.exists() or destination.stat().st_size == 0:
        candidates = sorted((root / "voice-references").glob(f"{reference_id}.*"))
        if not candidates:
            raise RuntimeError("Audio download completed without producing a file.")
        destination = candidates[0]
    _validate_audio_file(destination)
    return destination


def trim_reference_audio(
    source: Path,
    start_seconds: float,
    end_seconds: float,
    audio_format: str = "wav",
) -> Path:
    """Create a new, normalized reference clip without modifying the source."""
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("Trim end must be after trim start.")
    duration = _probe_duration(source)
    if duration is None:
        raise ValueError("Unable to read reference audio duration.")
    if start_seconds >= duration or end_seconds > duration + 0.1:
        raise ValueError("Trim range is outside the reference audio duration.")

    output_format = audio_format.strip().lower()
    if output_format not in {"wav", "mp3"}:
        raise ValueError("Reference audio format must be WAV or MP3.")
    destination = source.parent / f"{uuid.uuid4()}.{output_format}"
    _extract_audio(
        source,
        destination,
        output_format,
        start_seconds=start_seconds,
        duration=end_seconds - start_seconds,
    )
    return destination


def reference_audio_duration(path: Path) -> float:
    duration = _probe_duration(path)
    return max(0.0, duration or 0.0)


def reference_audio_waveform(path: Path, points: int = 600) -> list[float]:
    """Return normalized waveform peaks without decoding the full file in the UI."""
    point_count = max(180, min(1200, int(points)))
    command = [
        "ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
        "-ar", "200", "-f", "s16le", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, timeout=600)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"Unable to build reference waveform: {result.stderr[-1000:].decode(errors='replace')}")
    samples = [abs(value[0]) for value in struct.iter_unpack("<h", result.stdout)]
    bucket_size = max(1, len(samples) // point_count)
    peaks = [
        max(samples[index:index + bucket_size], default=0)
        for index in range(0, len(samples), bucket_size)
    ][:point_count]
    maximum = max(peaks, default=1) or 1
    return [max(0.04, peak / maximum) for peak in peaks]


def _test_clip_seconds(job: MediaJob) -> int:
    try:
        value = int(getattr(job, "test_clip_seconds", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _acquire_source(
    job: MediaJob,
    download_quality: str | None,
    download_subtitles: bool,
    clip_seconds: int,
) -> Path:
    root = ensure_storage()
    source = _normalize_video_source(job.video_url)
    source = _resolve_video_source(source)
    if not source:
        raise ValueError("Video URL is required.")

    local_path = Path(source)
    if local_path.exists() and local_path.is_file():
        destination = root / "raw-videos" / f"{job.id}{local_path.suffix or '.mp4'}"
        shutil.copyfile(local_path, destination)
        _validate_video_file(destination)
        return destination

    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        if os.getenv("AETHER_ALLOW_SYNTHETIC_SOURCE") == "1" and parsed.netloc.endswith("example.com"):
            return _create_synthetic_source(root, job.id)
        try:
            return _download_with_ytdlp(
                source,
                root,
                job,
                download_quality,
                download_subtitles=download_subtitles,
                clip_seconds=clip_seconds,
            )
        except Exception as ytdlp_error:
            if not _looks_like_direct_media_url(source):
                if os.getenv("AETHER_ALLOW_SYNTHETIC_SOURCE") == "1":
                    return _create_synthetic_source(root, job.id)
                fallback = _try_thirdparty_fallback(source, root, job.id)
                if fallback is not None:
                    return fallback
                if _is_douyin_url(source) and _needs_douyin_cookies(ytdlp_error):
                    raise RuntimeError(
                        "Unable to download Douyin video: Douyin requires fresh cookies for this URL. "
                        "Export browser cookies to a Netscape cookies.txt file and set AETHER_DOUYIN_COOKIES_FILE, "
                        "or set AETHER_DOUYIN_COOKIES_FROM_BROWSER=chrome/edge after closing that browser."
                    ) from ytdlp_error
                if _is_facebook_profile_url(source):
                    raise RuntimeError(
                        "Không tải được video Facebook: đây là link TRANG CÁ NHÂN (profile), không phải "
                        "link video. Hãy mở đúng video/reel cần dịch, bấm dấu ⋯ (3 chấm) ở góc video rồi "
                        "chọn “Sao chép liên kết”, sau đó dán link đó. Link hợp lệ có dạng: "
                        "facebook.com/.../videos/..., facebook.com/watch?v=..., facebook.com/reel/... "
                        "hoặc fb.watch/..."
                    ) from ytdlp_error
                raise RuntimeError(f"Unable to download video with yt-dlp: {ytdlp_error}") from ytdlp_error
            try:
                return _download_direct(source, root, job.id)
            except Exception as direct_error:
                if os.getenv("AETHER_ALLOW_SYNTHETIC_SOURCE") == "1":
                    return _create_synthetic_source(root, job.id)
                raise RuntimeError(f"Unable to download video. yt-dlp: {ytdlp_error}; direct: {direct_error}") from direct_error

    if os.getenv("AETHER_ALLOW_SYNTHETIC_SOURCE") == "1":
        return _create_synthetic_source(root, job.id)

    raise ValueError("Video URL must be an HTTP(S) URL or an existing local file path.")


def _normalize_video_source(value: str) -> str:
    source = (value or "").strip()
    if not source:
        return ""

    match = re.search(r"https?://[^\s\"'<>]+", source)
    if match:
        return match.group(0).rstrip(").,;]")
    return source


def _resolve_video_source(source: str) -> str:
    canonical = _canonical_douyin_url(source)
    if canonical != source:
        return canonical

    parsed = urlparse(source)
    host = (parsed.hostname or "").lower()
    if host == "v.douyin.com" or host.endswith(".v.douyin.com"):
        try:
            response = requests.get(source, headers=_platform_http_headers(source), allow_redirects=True, timeout=20)
            final_url = response.url or source
            return _canonical_douyin_url(final_url)
        except Exception:
            return source
    return source


def _canonical_douyin_url(source: str) -> str:
    try:
        parsed = urlparse(source)
    except Exception:
        return source

    host = (parsed.hostname or "").lower()
    if not host.endswith("douyin.com"):
        return source

    query = parse_qs(parsed.query)
    video_id = (query.get("modal_id") or query.get("aweme_id") or [None])[0]
    if not video_id:
        match = re.search(r"/(?:video|share/video)/(?P<id>\d+)", parsed.path)
        video_id = match.group("id") if match else None
    if video_id and video_id.isdigit():
        return f"https://www.douyin.com/video/{video_id}"
    return source


def _download_with_ytdlp(
    url: str,
    root: Path,
    job: MediaJob,
    download_quality: str | None = None,
    download_subtitles: bool = True,
    clip_seconds: int = 0,
) -> Path:
    import yt_dlp

    job_id = job.id
    quality = _download_quality(job, download_quality)
    _remove_previous_downloads(root, job_id)
    output_template = str(root / "raw-videos" / f"{job_id}.%(ext)s")
    base_options = {
        "merge_output_format": "mp4",
        "noplaylist": True,
        "outtmpl": output_template,
        "quiet": True,
        "retries": 2,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }
    if clip_seconds > 0:
        # Download only the opening section instead of the full video. The
        # post-download _trim_to_clip is the safety net if a platform/extractor
        # ignores download_ranges and returns the full file.
        try:
            from yt_dlp.utils import download_range_func

            base_options["download_ranges"] = download_range_func(
                None, [(0.0, float(clip_seconds))]
            )
            base_options["force_keyframes_at_cuts"] = True
        except Exception:
            pass
    http_headers = _platform_http_headers(url)
    if http_headers:
        base_options["http_headers"] = http_headers
    js_options = _ytdlp_js_options(url)
    if js_options:
        base_options.update(js_options)
    cookie_options = _ytdlp_cookie_options(url)
    if cookie_options:
        base_options.update(cookie_options)
    formats = _format_selectors_for_quality(quality)
    last_error: Exception | None = None
    _reject_unsupported_live_source(url, base_options)
    for format_selector in formats:
        _remove_previous_downloads(root, job_id)
        options = {**base_options, "format": format_selector}
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([url])
            downloaded_videos = _downloaded_video_files(root, job_id)
            if downloaded_videos:
                video_path = _prefer_video(downloaded_videos)
                _validate_video_file(video_path)
                if download_subtitles:
                    _download_sidecar_subtitle(url, root, job)
                return video_path
            last_error = RuntimeError(f"yt-dlp finished without producing a video file for format selector: {format_selector}")
        except FileNotFoundError as exc:
            tool = Path(exc.filename).name if exc.filename else "ffmpeg/ffprobe"
            raise RuntimeError(
                f"Bundled media tool is unavailable: {tool}. Reinstall VietDub or verify the resources/bin folder."
            ) from exc
        except Exception as exc:
            last_error = exc
            continue

    downloaded_videos = _downloaded_video_files(root, job_id)
    if not downloaded_videos:
        raise RuntimeError(str(last_error) if last_error else "yt-dlp finished without producing a file.")
    video_path = _prefer_video(downloaded_videos)
    _validate_video_file(video_path)
    if download_subtitles:
        _download_sidecar_subtitle(url, root, job)
    return video_path


def _reject_unsupported_live_source(url: str, base_options: dict[str, object]) -> None:
    """Avoid an unbounded FFmpeg process when a URL points to a live stream."""
    import yt_dlp

    probe_options = {
        **base_options,
        "skip_download": True,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(probe_options) as downloader:
            info = downloader.extract_info(url, download=False)
    except Exception:
        # Preserve the existing download/fallback error path when metadata
        # probing is unavailable but the media may still be downloadable.
        return

    if not isinstance(info, dict):
        return
    live_status = str(info.get("live_status") or "").strip().lower()
    is_live = bool(info.get("is_live"))
    if is_live or live_status in {"is_live", "is_upcoming"}:
        title = str(info.get("title") or "this source").strip()
        state = "currently live" if is_live or live_status == "is_live" else "scheduled but not started"
        raise RuntimeError(
            f'Live streams are not supported for finite video jobs: "{title}" is {state}. '
            "Use a completed video/VOD URL, or wait until the stream has ended and YouTube has published the replay."
        )


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}


def _ytdlp_js_options(url: str) -> dict[str, object]:
    """Return js_runtimes + remote_components options for YouTube URLs.

    yt-dlp 2026+ requires a JS runtime and the EJS challenge-solver script to
    extract YouTube videos (n-challenge / SABR bypass). We use Node.js when
    it is available; if Node.js is not found the defaults apply (no runtime,
    yt-dlp may fall back to degraded mode).
    """
    if not _is_youtube_url(url):
        return {}
    node_path = shutil.which("node") or shutil.which("node.exe")
    if not node_path:
        return {}
    return {
        "js_runtimes": {"node": {"path": node_path}},
        "remote_components": ("ejs:github",),
    }


def _ytdlp_cookie_options(url: str) -> dict[str, object]:
    douyin = _is_douyin_url(url)
    cookie_file = (
        os.getenv("AETHER_DOUYIN_COOKIES_FILE", "").strip()
        if douyin
        else ""
    ) or os.getenv("AETHER_YTDLP_COOKIES_FILE", "").strip()
    if cookie_file:
        path = Path(cookie_file).expanduser()
        if path.exists() and path.is_file():
            return {"cookiefile": str(path)}

    browser = (
        os.getenv("AETHER_DOUYIN_COOKIES_FROM_BROWSER", "").strip()
        if douyin
        else ""
    ) or os.getenv("AETHER_YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if browser:
        return {"cookiesfrombrowser": (browser, None, None, None)}
    return {}


def _platform_http_headers(url: str) -> dict[str, str]:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("douyin.com"):
        return {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Referer": "https://www.douyin.com/",
        }
    return {}


def _try_thirdparty_fallback(source: str, root: Path, job_id: str) -> Path | None:
    """Last-resort Douyin/TikTok download via public downloader services.

    Disabled unless AETHER_THIRDPARTY_DOWNLOAD_ENABLED is set. Returns a
    validated video path, or None to preserve the existing yt-dlp error path.
    """
    from .thirdparty_download import (
        download_via_thirdparty,
        is_thirdparty_supported,
        thirdparty_fallback_enabled,
    )

    if not thirdparty_fallback_enabled() or not is_thirdparty_supported(source):
        return None

    downloaded = download_via_thirdparty(source, root, job_id)
    if downloaded is None:
        return None
    try:
        _validate_video_file(downloaded)
    except Exception:
        return None
    return downloaded


def _is_douyin_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "douyin.com" or host.endswith(".douyin.com")


def _needs_douyin_cookies(error: Exception) -> bool:
    message = str(error).casefold()
    return "fresh cookies" in message or "cookies" in message


# Facebook path segments that mark real content (video/reel/etc.) rather than a
# person's profile page. A bare ``/<username>`` outside this set is a profile.
_FACEBOOK_CONTENT_SEGMENTS = {
    "watch", "reel", "reels", "video", "videos", "story.php", "permalink.php",
    "share", "groups", "pages", "marketplace", "events", "gaming", "live",
    "photo.php", "media", "v",
}


def _is_facebook_profile_url(url: str) -> bool:
    """True for a Facebook *profile/page* URL that contains no specific video.

    Users commonly paste a person's profile link (``/people/<name>/<id>/``,
    ``/profile.php?id=...`` or a bare ``/<username>``) expecting a download;
    yt-dlp rightly rejects it as an Unsupported URL because there is no video
    there. Detect that shape so we can show actionable guidance instead of a
    raw error. ``fb.watch`` is always a video link, so it never matches.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.com"):
        return False
    path = parsed.path or "/"
    lowered = path.lower()
    if lowered.startswith("/people/") or lowered.startswith("/profile.php"):
        return True
    segments = [seg for seg in path.split("/") if seg]
    # A single path segment that is not a known content keyword is a username
    # root (the profile page), e.g. facebook.com/Hansa.
    return len(segments) == 1 and segments[0].lower() not in _FACEBOOK_CONTENT_SEGMENTS


def _download_quality(job: MediaJob, override: str | None = None) -> str:
    configured = (
        override
        or getattr(job, "download_quality", "")
        or os.getenv("AETHER_YTDLP_QUALITY", "reliable")
    ).strip().lower()
    aliases = {
        "720": "reliable",
        "720p": "reliable",
        "best": "source",
        "original": "source",
        "origin": "source",
        "1080": "1080p",
        "2160": "4k",
        "2160p": "4k",
        "uhd": "4k",
    }
    return aliases.get(configured, configured if configured in {"reliable", "source", "1080p", "4k"} else "reliable")


def _format_selectors_for_quality(quality: str) -> list[str]:
    if quality == "source":
        return [
            "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/best",
            "best",
        ]
    if quality == "1080p":
        return [
            "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080][ext=mp4]/best[height<=1080]",
            "18/b[height<=720][ext=mp4]/best[height<=720][ext=mp4]",
            "best",
        ]
    if quality == "4k":
        return [
            "bv*[height<=2160][ext=mp4]+ba[ext=m4a]/bv*[height<=2160]+ba/b[height<=2160][ext=mp4]/best[height<=2160]",
            "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080][ext=mp4]/best[height<=1080]",
            "best",
        ]

    # Progressive MP4 is less pretty than DASH, but currently much more
    # reliable for YouTube videos that reject split video/audio downloads.
    return [
        "18/b[height<=720][ext=mp4]/best[height<=720][ext=mp4]",
        "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/best[height<=720]/best",
        "best[height<=720]/best",
    ]


def _subtitle_language_candidates(source_language: str | None) -> list[str]:
    common_candidates = ["en", "zh-Hans", "zh-Hant", "zh", "vi", "ja", "ko", "es", "fr", "de"]
    configured = (source_language or "").strip()
    if not configured or configured.lower() in {"auto", "detect", "unknown"}:
        return common_candidates

    candidates: list[str] = []
    for value in (configured, configured.split("-", 1)[0], *common_candidates):
        language = (value or "").strip()
        if language and language not in candidates:
            candidates.append(language)
    return candidates


def _download_sidecar_subtitle(url: str, root: Path, job: MediaJob) -> None:
    """Best-effort source subtitle download.

    YouTube can rate-limit individual subtitle languages. Source video download
    must not fail because a non-critical sidecar subtitle request got a 429.
    """
    if os.getenv("AETHER_YTDLP_DOWNLOAD_SUBTITLES", "1").strip().lower() in {"0", "false", "off", "no"}:
        return

    try:
        import yt_dlp
    except Exception:
        return

    raw_dir = root / "raw-videos"
    job_id = job.id
    js_options = _ytdlp_js_options(url)
    for language in _subtitle_language_candidates(job.source_language):
        _remove_subtitle_downloads(raw_dir, job_id, language)
        options: dict[str, object] = {
            "noplaylist": True,
            "outtmpl": str(raw_dir / f"{job_id}.%(ext)s"),
            "quiet": True,
            "retries": 1,
            "fragment_retries": 1,
            "socket_timeout": 20,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitlesformat": "srt/vtt/best",
            "subtitleslangs": [language],
            **js_options,
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([url])
        except Exception:
            continue
        if _downloaded_subtitle_files(raw_dir, job_id, language):
            return


def _download_direct(url: str, root: Path, job_id: str) -> Path:
    _remove_previous_downloads(root, job_id)
    suffix = Path(urlparse(url).path).suffix or ".mp4"
    if suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
        suffix = ".mp4"
    destination = root / "raw-videos" / f"{job_id}{suffix}"
    urlretrieve(url, destination)
    if destination.stat().st_size == 0:
        raise RuntimeError("Direct download produced an empty file.")
    _validate_video_file(destination)
    return destination


def _looks_like_direct_media_url(url: str) -> bool:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _remove_previous_downloads(root: Path, job_id: str) -> None:
    for path in (root / "raw-videos").glob(f"{job_id}.*"):
        if path.is_file():
            path.unlink(missing_ok=True)


def _downloaded_video_files(root: Path, job_id: str) -> list[Path]:
    return [
        path
        for path in sorted((root / "raw-videos").glob(f"{job_id}.*"))
        if path.is_file() and _is_video_path(path)
    ]


def _downloaded_subtitle_files(directory: Path, job_id: str, language: str) -> list[Path]:
    language_key = language.lower()
    return [
        path
        for path in sorted(directory.glob(f"{job_id}.*"))
        if path.is_file()
        and path.suffix.lower() in {".srt", ".vtt"}
        and (f".{language_key}." in path.name.lower() or path.name.lower().endswith(f".{language_key}{path.suffix.lower()}"))
    ]


def _remove_subtitle_downloads(directory: Path, job_id: str, language: str) -> None:
    for path in _downloaded_subtitle_files(directory, job_id, language):
        path.unlink(missing_ok=True)


def _prefer_video(paths: list[Path]) -> Path:
    media_paths = [path for path in paths if _is_video_path(path)]
    if not media_paths:
        raise RuntimeError("Download completed without producing a video file.")
    for path in paths:
        if path.suffix.lower() == ".mp4":
            return path
    return media_paths[0]


def _is_video_path(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _validate_video_file(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"Downloaded video is empty: {path.name}")

    header = path.read_bytes()[:512].lstrip().lower()
    if header.startswith(b"<!doctype html") or header.startswith(b"<html"):
        path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded source is an HTML page, not a playable video. Check the URL or retry with yt-dlp access restored.")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or "video" not in result.stdout:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file is not a valid playable video: {result.stderr[-500:] or path.name}")


def _extract_audio(
    source: Path,
    destination: Path,
    audio_format: str,
    *,
    start_seconds: float | None = None,
    duration: float | None = None,
) -> None:
    command = ["ffmpeg", "-y"]
    if start_seconds is not None:
        command.extend(["-ss", f"{start_seconds:.3f}"])
    command.extend(["-i", str(source), "-vn"])
    if duration is not None:
        command.extend(["-t", f"{duration:.3f}"])
    if audio_format == "wav":
        command.extend(["-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le"])
    else:
        command.extend(["-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k"])
    command.append(str(destination))
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Unable to prepare reference audio: {result.stderr[-1000:]}")
    _validate_audio_file(destination)


def _validate_audio_file(path: Path) -> None:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type", "-of",
        "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or "audio" not in result.stdout:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file is not valid audio: {result.stderr[-500:] or path.name}")


def _probe_duration(path: Path) -> float | None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return None


def _trim_to_clip(path: Path, clip_seconds: int) -> Path:
    """Trim ``path`` in place to the first ``clip_seconds`` seconds.

    Used by test/preview mode. If the file is already short enough (e.g. yt-dlp
    honored download_ranges) the source is returned untouched. Tries a fast
    stream copy first, then falls back to a re-encode; if both fail the full
    file is kept so the job can still run.
    """
    if clip_seconds <= 0:
        return path
    duration = _probe_duration(path)
    if duration is not None and duration <= clip_seconds + 1.0:
        return path

    trimmed = path.with_name(f"{path.stem}.clip{path.suffix}")
    copy_command = [
        "ffmpeg", "-y", "-i", str(path), "-t", str(clip_seconds), "-c", "copy", str(trimmed),
    ]
    result = subprocess.run(copy_command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not trimmed.exists() or trimmed.stat().st_size == 0:
        trimmed.unlink(missing_ok=True)
        reencode_command = [
            "ffmpeg", "-y", "-i", str(path), "-t", str(clip_seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(trimmed),
        ]
        result = subprocess.run(reencode_command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not trimmed.exists() or trimmed.stat().st_size == 0:
            trimmed.unlink(missing_ok=True)
            return path

    path.unlink(missing_ok=True)
    trimmed.replace(path)
    return path


def _create_synthetic_source(root: Path, job_id: str) -> Path:
    destination = root / "raw-videos" / f"{job_id}.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x111827:s=1280x720:d=6",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Unable to create synthetic source: {result.stderr[-1000:]}")
    _validate_video_file(destination)
    return destination
