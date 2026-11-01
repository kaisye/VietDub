"""Shared music / sound-effect library (all projects) plus free online sources.

Layout under ``storage/music-library/``::

    index.json          list of assets (metadata, license, attribution, tags)
    files/<id>.mp3      the audio, normalized to 44.1 kHz stereo MP3 (plays in every webview)
    peaks/<id>.json     waveform peaks for the timeline (0–255, ``rate`` values per second)
    settings.json       optional provider settings (Jamendo client_id)

Editor clips reference assets by id; nothing is copied into the project, so deleting
an asset that a project still uses makes that project's render fail with a clear
message (see ``music_mix.MusicAssetMissing``).

Online sources:
* Openverse (https://api.openverse.org/v1/audio/) — no key; anonymous limits are
  20 requests/minute and 200/day, so searches are cached for 10 minutes.
* Jamendo (https://api.jamendo.com/v3.0/) — only when the user saved a free client_id.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .storage import ensure_storage

AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".aif", ".aiff", ".webm", ".mp4"}
MAX_DURATION = 3600.0
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
KINDS = ("music", "sfx")
# Licenses that allow use in a monetized video (attribution may still be required).
COMMERCIAL_LICENSES = ("cc0", "pdm", "by", "by-sa")
LICENSE_LABELS = {
    "cc0": "CC0",
    "pdm": "Public Domain Mark",
    "by": "CC BY",
    "by-sa": "CC BY-SA",
    "by-nd": "CC BY-ND",
    "by-nc": "CC BY-NC",
    "by-nc-sa": "CC BY-NC-SA",
    "by-nc-nd": "CC BY-NC-ND",
    "sampling+": "Sampling+",
    "nc-sampling+": "NC Sampling+",
}
OPENVERSE_API = "https://api.openverse.org/v1"
JAMENDO_API = "https://api.jamendo.com/v3.0"
USER_AGENT = "VietDubStudio/1.0 (desktop video dubbing app; music library)"
_LOCK = threading.RLock()
_SEARCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
# Raw Openverse records seen in searches: the import falls back to them when the
# detail endpoint is down (it intermittently answers 500 while search works).
_OPENVERSE_SEEN: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 600.0


class LibraryError(RuntimeError):
    """User-facing error (bad file, provider unavailable…)."""


class ProviderError(LibraryError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---- storage --------------------------------------------------------------------------


def library_dir() -> Path:
    root = ensure_storage() / "music-library"
    for sub in ("files", "peaks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _index_path() -> Path:
    return library_dir() / "index.json"


def load_index() -> list[dict[str, Any]]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in data.get("assets", []) if isinstance(item, dict) and item.get("id")]


def _save_index(assets: list[dict[str, Any]]) -> None:
    path = _index_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "assets": assets}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def get_asset(asset_id: str) -> dict[str, Any] | None:
    return next((item for item in load_index() if item["id"] == asset_id), None)


def asset_path(asset_id: str) -> Path | None:
    """Local file of an asset, or ``None`` when the asset or its file is gone."""
    asset = get_asset(asset_id)
    if not asset:
        return None
    path = library_dir() / "files" / Path(str(asset.get("filename") or "")).name
    return path if path.is_file() else None


def public(asset: dict[str, Any]) -> dict[str, Any]:
    """API view of an asset (adds the license label and whether credit is required)."""
    license_code = asset.get("license") or "unknown"
    return {
        **asset,
        "license_label": license_label(license_code, asset.get("license_version")),
        "attribution_required": requires_attribution(license_code),
        "commercial_ok": commercial_ok(license_code),
        "missing": not (library_dir() / "files" / Path(str(asset.get("filename") or "")).name).is_file(),
    }


def license_label(code: str, version: str | None = None) -> str:
    label = LICENSE_LABELS.get(code)
    if not label:
        return "Tự tải lên" if code == "own" else "Không rõ"
    return f"{label} {version}".strip() if version and code not in {"cc0", "pdm"} else label


def requires_attribution(code: str) -> bool:
    return code.startswith("by") or "sampling" in code


def commercial_ok(code: str) -> bool:
    return code in COMMERCIAL_LICENSES or code == "own"


# ---- import / edit ----------------------------------------------------------------------


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=title,artist",
            "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    if result.returncode != 0 or not data.get("streams"):
        raise LibraryError("File này không có luồng âm thanh đọc được.")
    fmt = data.get("format") or {}
    tags = {key.lower(): value for key, value in (fmt.get("tags") or {}).items()}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"duration": duration, "title": tags.get("title", ""), "artist": tags.get("artist", "")}


def compute_peaks(path: Path, duration: float) -> dict[str, Any]:
    import numpy as np

    rate = 20 if duration <= 120 else 10
    sample_rate = 8000
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True,
        timeout=600,
    )
    samples = np.frombuffer(result.stdout, dtype=np.int16)
    window = sample_rate // rate
    usable = len(samples) // window * window
    if not usable:
        return {"rate": rate, "peaks": []}
    peaks = np.abs(samples[:usable].astype(np.int32)).reshape(-1, window).max(axis=1)
    scaled = np.sqrt(peaks / 32768.0) * 255  # Same curve as the original-audio waveform.
    return {"rate": rate, "peaks": [int(value) for value in scaled.clip(0, 255)]}


def import_file(
    source: Path,
    *,
    title: str = "",
    artist: str = "",
    kind: str = "music",
    tags: list[str] | None = None,
    source_name: str = "upload",
    source_id: str = "",
    source_url: str = "",
    license_code: str = "own",
    license_version: str = "",
    license_url: str = "",
    attribution: str = "",
    original_filename: str = "",
) -> dict[str, Any]:
    """Normalize ``source`` with ffmpeg into the library and index it."""
    probe = _probe(source)
    if probe["duration"] > MAX_DURATION:
        raise LibraryError("File quá dài (tối đa 60 phút).")
    asset_id = f"mus-{uuid.uuid4().hex[:12]}"
    root = library_dir()
    destination = root / "files" / f"{asset_id}.mp3"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn", "-map_metadata", "-1",
            "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k", str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise LibraryError(f"Không chuyển đổi được file âm thanh: {result.stderr[-300:]}")
    duration = _probe(destination)["duration"] or probe["duration"]
    (root / "peaks" / f"{asset_id}.json").write_text(
        json.dumps(compute_peaks(destination, duration), separators=(",", ":")), encoding="utf-8"
    )
    stem = Path(original_filename or source.name).stem
    asset = {
        "id": asset_id,
        "title": (title or probe["title"] or stem or asset_id).strip()[:200],
        "artist": (artist or probe["artist"] or "").strip()[:200],
        "kind": kind if kind in KINDS else "music",
        "duration": round(duration, 3),
        "tags": clean_tags(tags or []),
        "source": source_name,
        "source_id": source_id,
        "source_url": source_url,
        "license": license_code,
        "license_version": license_version,
        "license_url": license_url,
        "attribution": attribution.strip(),
        "filename": destination.name,
        "file_url": f"/storage/music-library/files/{destination.name}",
        "original_filename": original_filename or source.name,
        "size": destination.stat().st_size,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    with _LOCK:
        assets = load_index()
        assets.insert(0, asset)
        _save_index(assets)
    return asset


def import_upload(stream: Any, filename: str, kind: str = "music") -> dict[str, Any]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise LibraryError("Chỉ nhận file âm thanh MP3, WAV, M4A, OGG hoặc FLAC.")
    with tempfile.TemporaryDirectory(dir=library_dir()) as tmp:
        temporary = Path(tmp) / f"upload{suffix}"
        with temporary.open("wb") as output:
            shutil.copyfileobj(stream, output)
        if temporary.stat().st_size == 0:
            raise LibraryError("File tải lên rỗng.")
        return import_file(temporary, kind=kind, original_filename=filename)


def clean_tags(tags: list[Any]) -> list[str]:
    seen: list[str] = []
    for tag in tags:
        value = re.sub(r"\s+", " ", str(tag)).strip().lower()[:40]
        if value and value not in seen:
            seen.append(value)
    return seen[:20]


def update_asset(asset_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        assets = load_index()
        asset = next((item for item in assets if item["id"] == asset_id), None)
        if not asset:
            raise KeyError(asset_id)
        if patch.get("title") is not None:
            title = str(patch["title"]).strip()
            if not title:
                raise LibraryError("Tên không được để trống.")
            asset["title"] = title[:200]
        if patch.get("artist") is not None:
            asset["artist"] = str(patch["artist"]).strip()[:200]
        if patch.get("tags") is not None:
            asset["tags"] = clean_tags(list(patch["tags"]))
        if patch.get("kind") in KINDS:
            asset["kind"] = patch["kind"]
        if patch.get("attribution") is not None:
            asset["attribution"] = str(patch["attribution"]).strip()[:1000]
        _save_index(assets)
        return asset


def delete_asset(asset_id: str) -> None:
    with _LOCK:
        assets = load_index()
        asset = next((item for item in assets if item["id"] == asset_id), None)
        if not asset:
            raise KeyError(asset_id)
        assets.remove(asset)
        _save_index(assets)
    root = library_dir()
    (root / "files" / Path(str(asset.get("filename") or "")).name).unlink(missing_ok=True)
    (root / "peaks" / f"{asset_id}.json").unlink(missing_ok=True)


def peaks(asset_id: str) -> dict[str, Any]:
    path = library_dir() / "peaks" / f"{Path(asset_id).name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    file = asset_path(asset_id)
    if not file:
        raise KeyError(asset_id)
    data = compute_peaks(file, float((get_asset(asset_id) or {}).get("duration") or 0))
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return data


def _fold(text: str) -> str:
    """Lower-case, accent-free (so "nhe nhang" finds "nhẹ nhàng")."""
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(char for char in text if unicodedata.category(char) != "Mn").lower()


def list_assets(query: str = "", tag: str = "", kind: str = "") -> dict[str, Any]:
    assets = load_index()
    words = _fold(query).split()
    wanted_tag = tag.strip().lower()
    matches = []
    for asset in assets:
        if kind in KINDS and asset.get("kind") != kind:
            continue
        if wanted_tag and wanted_tag not in asset.get("tags", []):
            continue
        haystack = _fold(" ".join([asset.get("title", ""), asset.get("artist", ""), *asset.get("tags", [])]))
        if any(word not in haystack for word in words):
            continue
        matches.append(public(asset))
    tags = sorted({tag for asset in assets for tag in asset.get("tags", [])})
    return {"items": matches, "tags": tags, "total": len(assets)}


# ---- settings -------------------------------------------------------------------------


def _settings_path() -> Path:
    return library_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    if "jamendo_client_id" in patch:
        value = str(patch["jamendo_client_id"] or "").strip()
        if value and not re.fullmatch(r"[A-Za-z0-9]{4,64}", value):
            raise LibraryError("Jamendo client_id không hợp lệ.")
        settings["jamendo_client_id"] = value
    _settings_path().write_text(json.dumps(settings), encoding="utf-8")
    return public_settings()


def public_settings() -> dict[str, Any]:
    client_id = load_settings().get("jamendo_client_id") or ""
    return {
        "jamendo_configured": bool(client_id),
        # Masked: never send the stored value back in full.
        "jamendo_client_id": f"{client_id[:2]}…{client_id[-2:]}" if len(client_id) > 4 else "",
    }


# ---- online providers -----------------------------------------------------------------


def _http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(35.0, connect=10.0), headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def _cached(key: str) -> dict[str, Any] | None:
    hit = _SEARCH_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


def _remember(key: str, data: dict[str, Any]) -> dict[str, Any]:
    if len(_SEARCH_CACHE) > 200:
        _SEARCH_CACHE.clear()
    _SEARCH_CACHE[key] = (time.monotonic(), data)
    return data


def _get_json(url: str, params: dict[str, Any] | None, provider: str) -> dict[str, Any]:
    try:
        with _http() as client:
            response = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Không kết nối được {provider}: {exc}") from exc
    if response.status_code == 429:
        raise ProviderError(
            f"{provider} đang giới hạn số lượt tìm kiếm (ẩn danh: 20 lượt/phút, 200 lượt/ngày). Thử lại sau ít phút.",
            429,
        )
    if response.status_code == 404:
        raise ProviderError(f"{provider}: không tìm thấy bản nhạc này.", 404)
    if response.status_code >= 400:
        body = response.text[:200] if "html" not in response.headers.get("content-type", "") else ""
        raise ProviderError(f"{provider} trả lỗi {response.status_code}{': ' + body if body else ''}. Thử lại sau ít phút.")
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(f"{provider} trả dữ liệu không đọc được.") from exc


def openverse_search(
    query: str,
    *,
    category: str = "music",
    commercial_only: bool = True,
    length: str = "",
    page: int = 1,
) -> dict[str, Any]:
    params: dict[str, Any] = {"q": query.strip()[:200], "page": max(1, page), "page_size": 20}
    if category == "music":
        params["category"] = "music"
    elif category == "sound_effect":
        # Openverse's sound_effect category is sparsely tagged (Freesound items carry no
        # category), so effects are searched on Freesound, where they actually live.
        params["source"] = "freesound"
    if commercial_only:
        params["license"] = ",".join(COMMERCIAL_LICENSES)
    if length in {"shortest", "short", "medium", "long"}:
        params["length"] = length
    key = "ov:" + json.dumps(params, sort_keys=True)
    cached = _cached(key)
    if cached:
        return cached
    data = _get_json(f"{OPENVERSE_API}/audio/", params, "Openverse")
    imported = {asset.get("source_id") for asset in load_index() if asset.get("source") == "openverse"}
    if len(_OPENVERSE_SEEN) > 2000:
        _OPENVERSE_SEEN.clear()
    for item in data.get("results") or []:
        if item.get("id"):
            _OPENVERSE_SEEN[str(item["id"])] = item
    items = [_openverse_item(item, imported) for item in data.get("results") or []]
    return _remember(
        key,
        {
            "items": items,
            "page": data.get("page", page),
            "page_count": data.get("page_count", 1),
            "result_count": data.get("result_count", len(items)),
        },
    )


def _openverse_item(item: dict[str, Any], imported: set[Any] | None = None) -> dict[str, Any]:
    code = str(item.get("license") or "").lower()
    duration_ms = item.get("duration")
    return {
        "provider": "openverse",
        "id": item.get("id"),
        "title": item.get("title") or "",
        "artist": item.get("creator") or "",
        "duration": round(float(duration_ms) / 1000.0, 3) if duration_ms else None,
        "license": code,
        "license_version": item.get("license_version") or "",
        "license_url": item.get("license_url") or "",
        "license_label": license_label(code, item.get("license_version")),
        "commercial_ok": commercial_ok(code),
        "attribution_required": requires_attribution(code),
        "attribution": item.get("attribution") or "",
        "preview_url": item.get("url") or "",
        "landing_url": item.get("foreign_landing_url") or "",
        "source": item.get("source") or item.get("provider") or "",
        "category": item.get("category") or "",
        "tags": [tag.get("name") for tag in item.get("tags") or [] if isinstance(tag, dict) and tag.get("name")][:8],
        "imported": item.get("id") in (imported or set()),
    }


def _download(url: str, suffix_hint: str = "") -> Path:
    if not url.startswith(("https://", "http://")):
        raise ProviderError("Địa chỉ tải nhạc không hợp lệ.")
    directory = Path(tempfile.mkdtemp(dir=library_dir()))
    target = directory / f"download{suffix_hint if suffix_hint.startswith('.') else '.bin'}"
    try:
        with _http() as client, client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise ProviderError(f"Không tải được file nhạc (HTTP {response.status_code}).")
            size = 0
            with target.open("wb") as output:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise ProviderError("File nhạc quá lớn (tối đa 200 MB).")
                    output.write(chunk)
    except httpx.HTTPError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise ProviderError(f"Không tải được file nhạc: {exc}") from exc
    except ProviderError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return target


def _existing(source: str, source_id: str) -> dict[str, Any] | None:
    return next(
        (asset for asset in load_index() if asset.get("source") == source and asset.get("source_id") == source_id),
        None,
    )


def openverse_import(audio_id: str, kind: str | None = None) -> dict[str, Any]:
    """Download an Openverse track into the library (metadata re-read from the API, not the client)."""
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", audio_id or ""):
        raise ProviderError("Mã Openverse không hợp lệ.", 400)
    existing = _existing("openverse", audio_id)
    if existing:
        return existing
    try:
        detail = _get_json(f"{OPENVERSE_API}/audio/{audio_id}/", None, "Openverse")
    except ProviderError as exc:
        if exc.status_code == 404 or audio_id not in _OPENVERSE_SEEN:
            raise
        detail = _OPENVERSE_SEEN[audio_id]
    item = _openverse_item(detail)
    if not item["preview_url"]:
        raise ProviderError("Bản nhạc này không có file để tải.")
    downloaded = _download(item["preview_url"], "." + str(detail.get("filetype") or "").lstrip(".").replace("mp32", "mp3"))
    try:
        return import_file(
            downloaded,
            title=item["title"],
            artist=item["artist"],
            kind=kind or ("sfx" if item["category"] == "sound_effect" else "music"),
            tags=item["tags"],
            source_name="openverse",
            source_id=audio_id,
            source_url=item["landing_url"],
            license_code=item["license"],
            license_version=item["license_version"],
            license_url=item["license_url"],
            attribution=item["attribution"] or build_attribution(item),
            original_filename=downloaded.name,
        )
    finally:
        shutil.rmtree(downloaded.parent, ignore_errors=True)


def _jamendo_client_id() -> str:
    client_id = load_settings().get("jamendo_client_id") or ""
    if not client_id:
        raise ProviderError("Chưa nhập Jamendo client_id trong cài đặt thư viện nhạc.", 400)
    return client_id


def _jamendo_license(url: str) -> tuple[str, str]:
    match = re.search(r"/licenses/([a-z-]+)/([\d.]+)", url or "")
    if match:
        return match.group(1), match.group(2)
    if "publicdomain/zero" in (url or ""):
        return "cc0", "1.0"
    return "unknown", ""


def _jamendo_item(track: dict[str, Any], imported: set[Any] | None = None) -> dict[str, Any]:
    code, version = _jamendo_license(track.get("license_ccurl") or "")
    item = {
        "provider": "jamendo",
        "id": str(track.get("id") or ""),
        "title": track.get("name") or "",
        "artist": track.get("artist_name") or "",
        "duration": float(track.get("duration") or 0) or None,
        "license": code,
        "license_version": version,
        "license_url": track.get("license_ccurl") or "",
        "license_label": license_label(code, version),
        "commercial_ok": commercial_ok(code),
        "attribution_required": requires_attribution(code),
        "preview_url": track.get("audio") or "",
        "download_url": track.get("audiodownload") if track.get("audiodownload_allowed", True) else "",
        "landing_url": track.get("shareurl") or "",
        "source": "jamendo",
        "category": "music",
        "tags": list(((track.get("musicinfo") or {}).get("tags") or {}).get("genres") or [])[:8],
        "imported": str(track.get("id")) in (imported or set()),
    }
    item["attribution"] = build_attribution(item)
    return item


def jamendo_search(query: str, *, commercial_only: bool = True, page: int = 1) -> dict[str, Any]:
    client_id = _jamendo_client_id()
    params = {
        "client_id": client_id,
        "format": "json",
        "limit": 20,
        "offset": (max(1, page) - 1) * 20,
        "search": query.strip()[:200],
        "include": "licenses musicinfo",
        "audioformat": "mp32",
        "order": "relevance",
    }
    key = "jm:" + json.dumps({k: v for k, v in params.items() if k != "client_id"}, sort_keys=True) + str(commercial_only)
    cached = _cached(key)
    if cached:
        return cached
    data = _get_json(f"{JAMENDO_API}/tracks/", params, "Jamendo")
    headers = data.get("headers") or {}
    if headers.get("status") not in (None, "success"):
        raise ProviderError(f"Jamendo: {headers.get('error_message') or headers.get('status')}")
    imported = {asset.get("source_id") for asset in load_index() if asset.get("source") == "jamendo"}
    items = [_jamendo_item(track, imported) for track in data.get("results") or []]
    if commercial_only:
        items = [item for item in items if item["commercial_ok"]]
    return _remember(key, {"items": items, "page": page, "page_count": None, "result_count": headers.get("results_count")})


def jamendo_import(track_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"\d{1,12}", track_id or ""):
        raise ProviderError("Mã Jamendo không hợp lệ.", 400)
    existing = _existing("jamendo", track_id)
    if existing:
        return existing
    data = _get_json(
        f"{JAMENDO_API}/tracks/",
        {"client_id": _jamendo_client_id(), "format": "json", "id": track_id, "include": "licenses musicinfo", "audioformat": "mp32"},
        "Jamendo",
    )
    results = data.get("results") or []
    if not results:
        raise ProviderError("Jamendo: không tìm thấy bản nhạc này.", 404)
    item = _jamendo_item(results[0])
    downloaded = _download(item.get("download_url") or item["preview_url"], ".mp3")
    try:
        return import_file(
            downloaded,
            title=item["title"],
            artist=item["artist"],
            tags=item["tags"],
            source_name="jamendo",
            source_id=track_id,
            source_url=item["landing_url"],
            license_code=item["license"],
            license_version=item["license_version"],
            license_url=item["license_url"],
            attribution=item["attribution"],
            original_filename=downloaded.name,
        )
    finally:
        shutil.rmtree(downloaded.parent, ignore_errors=True)


# ---- attribution --------------------------------------------------------------------------


def build_attribution(item: dict[str, Any]) -> str:
    """TASL line (Title, Author, Source, License) for one asset."""
    title = item.get("title") or "Untitled"
    artist = item.get("artist") or ""
    code = item.get("license") or ""
    label = license_label(code, item.get("license_version"))
    if code in {"cc0", "pdm"}:
        text = f"\"{title}\"{f' by {artist}' if artist else ''} is in the public domain ({label})."
    else:
        text = f"\"{title}\"{f' by {artist}' if artist else ''} is licensed under {label}."
        if item.get("license_url"):
            text += f" {item['license_url']}"
    return text


def project_credits(workspace: dict[str, Any], lang: str = "vi") -> dict[str, Any]:
    """Credits text for every library asset used on the workspace's audio tracks."""
    used: list[str] = []
    for track in workspace.get("audio_tracks") or []:
        for clip in track.get("clips") or []:
            if clip.get("asset_id") and clip["asset_id"] not in used:
                used.append(clip["asset_id"])
    index = {asset["id"]: asset for asset in load_index()}
    items, missing = [], []
    for asset_id in used:
        asset = index.get(asset_id)
        if not asset:
            missing.append(asset_id)
            continue
        items.append(public(asset))
    credited = [item for item in items if item["attribution_required"]]
    lines = []
    for item in credited:
        line = item.get("attribution") or build_attribution(item)
        if item.get("source_url") and item["source_url"] not in line:
            line += f" Source: {item['source_url']}"
        lines.append(f"- {line}")
    header = "Nhạc sử dụng trong video:" if lang == "vi" else "Music used in this video:"
    return {
        "text": (header + "\n" + "\n".join(lines)) if lines else "",
        "items": items,
        "credited_count": len(credited),
        "missing": missing,
        "non_commercial": [item["id"] for item in items if not item["commercial_ok"]],
    }
