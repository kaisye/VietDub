"""Read-only data tools the assistant can call to ground its answers.

Every tool returns a JSON-serialisable dict and never raises for "no data":
missing tables (e.g. the channels feature not migrated yet) degrade to
``{"available": false, ...}`` so the model can tell the user what's missing.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ... import models
from .. import storage as storage_mod
from .providers import ToolSpec

MAX_TRANSCRIPT_CHARS = 30000
DEFAULT_TRANSCRIPT_CHARS = 8000


# ------------------------------------------------------------------ helpers


def _iso(value: datetime | None) -> str | None:
    return value.replace(microsecond=0).isoformat() if value else None


def job_title(job: Any) -> str:
    if getattr(job, "source_title", None):
        return job.source_title
    content = (getattr(job, "content", "") or "").strip()
    if content:
        return content.splitlines()[0][:120]
    return getattr(job, "video_url", "") or job.id


_SRT_TIME = re.compile(r"^\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->")
_TAGS = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def srt_to_text(raw: str) -> str:
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip().lstrip("\ufeff")
        if not stripped or stripped.isdigit() or _SRT_TIME.match(stripped):
            continue
        cleaned = _TAGS.sub("", stripped).strip()
        if cleaned and (not lines or lines[-1] != cleaned):
            lines.append(cleaned)
    return "\n".join(lines)


def _newest(paths: list[Path]) -> Path | None:
    files = [p for p in paths if p.is_file() and p.stat().st_size > 0]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def transcript_paths(job_id: str) -> dict[str, Path | None]:
    subtitles = Path(storage_mod.STORAGE_ROOT) / "subtitles"
    if not subtitles.exists():
        return {"translated": None, "source": None}
    translated = _newest(list(subtitles.glob(f"{job_id}*.render.display.srt"))) or _newest(
        list(subtitles.glob(f"{job_id}*.render.srt"))
    )
    source = _newest(list(subtitles.glob(f"{job_id}*.render.source.srt"))) or _newest(
        [p for p in subtitles.glob(f"{job_id}*.srt") if ".render." not in p.name]
    )
    return {"translated": translated, "source": source}


def read_transcript(job_id: str, kind: str = "translated", max_chars: int = DEFAULT_TRANSCRIPT_CHARS) -> dict[str, Any]:
    paths = transcript_paths(job_id)
    max_chars = max(500, min(int(max_chars or DEFAULT_TRANSCRIPT_CHARS), MAX_TRANSCRIPT_CHARS))
    kinds = ["translated", "source"] if kind == "both" else [kind if kind in paths else "translated"]
    out: dict[str, Any] = {}
    budget = max_chars // len(kinds)
    for name in kinds:
        path = paths.get(name)
        if not path:
            out[name] = {"available": False}
            continue
        text = srt_to_text(path.read_text(encoding="utf-8", errors="ignore"))
        out[name] = {
            "available": True,
            "chars": len(text),
            "truncated": len(text) > budget,
            "text": text[:budget],
        }
    return out


# -------------------------------------------------------------------- tools


def tool_list_dubbing_jobs(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit") or 20), 100))
    stmt = select(models.MediaJob).order_by(models.MediaJob.created_at.desc())
    status = (args.get("status") or "").strip()
    if status:
        try:
            stmt = stmt.where(models.MediaJob.status == models.MediaJobStatus(status))
        except ValueError:
            return {"error": f"status không hợp lệ: {status}", "valid": [s.value for s in models.MediaJobStatus]}
    search = (args.get("search") or "").strip()
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            models.MediaJob.source_title.ilike(like)
            | models.MediaJob.video_url.ilike(like)
            | models.MediaJob.content.ilike(like)
        )
    jobs = db.scalars(stmt.limit(limit)).all()
    total = db.query(models.MediaJob).count()
    items = []
    for job in jobs:
        paths = transcript_paths(job.id)
        items.append(
            {
                "id": job.id,
                "title": job_title(job),
                "video_url": job.video_url,
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                "source_language": job.source_language,
                "target_language": job.target_language,
                "platform": job.platform,
                "project_id": job.project_id,
                "created_at": _iso(job.created_at),
                "completed_at": _iso(job.completed_at),
                "has_output": bool(job.output_url),
                "has_translated_transcript": paths["translated"] is not None,
                "has_source_transcript": paths["source"] is not None,
            }
        )
    return {"total_jobs": total, "returned": len(items), "jobs": items}


def tool_get_job_transcript(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    job_id = (args.get("job_id") or "").strip()
    job = db.get(models.MediaJob, job_id) if job_id else None
    if not job:
        return {"error": f"Không tìm thấy job '{job_id}'. Dùng list_dubbing_jobs để lấy id."}
    kind = args.get("kind") or "translated"
    data = read_transcript(job.id, kind, int(args.get("max_chars") or DEFAULT_TRANSCRIPT_CHARS))
    return {
        "job_id": job.id,
        "title": job_title(job),
        "source_language": job.source_language,
        "target_language": job.target_language,
        "description": (job.content or "")[:2000],
        "transcript": data,
    }


def tool_list_projects(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    projects = db.scalars(
        select(models.Project).where(models.Project.archived_at.is_(None)).order_by(models.Project.updated_at.desc()).limit(50)
    ).all()
    items = []
    for project in projects:
        job_count = db.query(models.MediaJob).filter(models.MediaJob.project_id == project.id).count()
        items.append(
            {
                "id": project.id,
                "name": project.name,
                "description": (project.description or "")[:500],
                "source_language": project.source_language,
                "target_language": project.target_language,
                "job_count": job_count,
                "updated_at": _iso(project.updated_at),
            }
        )
    return {"projects": items}


def _channel_models() -> tuple[Any, Any, Any] | None:
    channel = getattr(models, "Channel", None)
    snapshot = getattr(models, "ChannelSnapshot", None)
    video = getattr(models, "ChannelVideo", None)
    if channel is None:
        return None
    return channel, snapshot, video


_CHANNELS_UNAVAILABLE = {
    "available": False,
    "message": "Tính năng kênh chưa sẵn sàng hoặc chưa có kênh nào được kết nối. Gợi ý người dùng kết nối kênh ở màn hình Kênh.",
}


def _snapshot_dict(snap: Any) -> dict[str, Any]:
    return {
        k: getattr(snap, k, None)
        for k in ("date", "followers", "views", "video_count", "likes", "comments", "shares")
        if getattr(snap, k, None) is not None
    }


def tool_get_channels_overview(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    resolved = _channel_models()
    if not resolved:
        return dict(_CHANNELS_UNAVAILABLE)
    Channel, Snapshot, Video = resolved
    try:
        channels = db.scalars(select(Channel).order_by(Channel.platform, Channel.name)).all()
        if not channels:
            return dict(_CHANNELS_UNAVAILABLE)
        days = max(1, min(int(args.get("days") or 28), 365))
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        items = []
        for channel in channels:
            entry: dict[str, Any] = {
                "id": channel.id,
                "platform": channel.platform,
                "name": channel.name,
                "handle": getattr(channel, "handle", ""),
                "status": getattr(channel, "status", ""),
                "last_synced_at": _iso(getattr(channel, "last_synced_at", None)),
            }
            if Snapshot is not None:
                snaps = db.scalars(
                    select(Snapshot).where(Snapshot.channel_id == channel.id).order_by(Snapshot.date.desc()).limit(400)
                ).all()
                if snaps:
                    latest = snaps[0]
                    entry["latest"] = _snapshot_dict(latest)
                    window = [s for s in snaps if s.date >= since]
                    oldest = window[-1] if window else None
                    if oldest is not None and oldest is not latest:
                        growth = {}
                        for key in ("followers", "views", "video_count"):
                            a, b = getattr(oldest, key, None), getattr(latest, key, None)
                            if a is not None and b is not None:
                                growth[key] = b - a
                        entry[f"growth_last_{days}_days"] = {"from": oldest.date, "to": latest.date, **growth}
            if Video is not None:
                entry["videos_tracked"] = db.query(Video).filter(Video.channel_id == channel.id).count()
            items.append(entry)
        return {"available": True, "channels": items}
    except SQLAlchemyError:
        db.rollback()
        return dict(_CHANNELS_UNAVAILABLE)


def tool_get_top_videos(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    resolved = _channel_models()
    if not resolved or resolved[2] is None:
        return dict(_CHANNELS_UNAVAILABLE)
    Channel, _Snapshot, Video = resolved
    sort_by = args.get("sort_by") or "views"
    if sort_by not in ("views", "likes", "comments", "shares", "recent"):
        sort_by = "views"
    limit = max(1, min(int(args.get("limit") or 10), 50))
    try:
        stmt = select(Video, Channel).join(Channel, Channel.id == Video.channel_id)
        if args.get("channel_id"):
            stmt = stmt.where(Video.channel_id == args["channel_id"])
        if args.get("platform"):
            stmt = stmt.where(Channel.platform == args["platform"])
        if args.get("days"):
            since = datetime.utcnow() - timedelta(days=max(1, int(args["days"])))
            stmt = stmt.where(Video.published_at >= since)
        if sort_by == "recent":
            stmt = stmt.order_by(Video.published_at.desc())
        else:
            column = getattr(Video, sort_by)
            stmt = stmt.order_by(column.is_(None), column.desc())
        rows = db.execute(stmt.limit(limit)).all()
        videos = []
        for video, channel in rows:
            item = {
                "id": video.id,
                "channel": channel.name,
                "platform": channel.platform,
                "kind": getattr(video, "kind", "video"),
                "title": video.title,
                "published_at": _iso(video.published_at),
                "url": getattr(video, "url", ""),
                "duration_seconds": getattr(video, "duration_seconds", None),
            }
            for key in ("views", "likes", "comments", "shares"):
                value = getattr(video, key, None)
                if value is not None:
                    item[key] = value
            if getattr(video, "media_job_id", None):
                item["media_job_id"] = video.media_job_id
            videos.append(item)
        if not videos:
            return {"available": True, "videos": [], "message": "Chưa có dữ liệu video. Có thể cần đồng bộ kênh."}
        return {"available": True, "sort_by": sort_by, "videos": videos}
    except SQLAlchemyError:
        db.rollback()
        return dict(_CHANNELS_UNAVAILABLE)


# ------------------------------------------------------------------ registry

TOOLS: list[ToolSpec] = [
    ToolSpec(
        "list_dubbing_jobs",
        "Liệt kê các video lồng tiếng (dubbing job) của người dùng trong VietDub: tiêu đề, ngôn ngữ nguồn/đích, "
        "trạng thái, nền tảng, có transcript hay không. Dùng khi người dùng hỏi về video của họ, hoặc cần id để đọc transcript.",
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Lọc theo trạng thái: ready, failed, queued, draft, rendering, ...",
                },
                "search": {"type": "string", "description": "Tìm theo tiêu đề / URL / mô tả."},
                "limit": {"type": "integer", "description": "Số job tối đa (1-100, mặc định 20)."},
            },
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "get_job_transcript",
        "Đọc transcript/phụ đề của một dubbing job (bản dịch và/hoặc bản gốc) dưới dạng văn bản thuần. "
        "Dùng khi cần viết tiêu đề, mô tả, hashtag, caption, tóm tắt hoặc kịch bản dựa trên nội dung video.",
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Id của job (lấy từ list_dubbing_jobs)."},
                "kind": {"type": "string", "enum": ["translated", "source", "both"], "description": "Mặc định translated."},
                "max_chars": {"type": "integer", "description": "Giới hạn ký tự (500-30000, mặc định 8000)."},
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "list_projects",
        "Liệt kê các dự án (project) lồng tiếng: tên, mô tả, ngôn ngữ, số video.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "get_channels_overview",
        "Tổng quan các kênh mạng xã hội đã kết nối (YouTube, Facebook Page, TikTok): người theo dõi, lượt xem, số video "
        "và mức tăng trưởng trong N ngày gần nhất. Dùng cho câu hỏi chiến lược, tăng trưởng, lịch đăng.",
        {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "Cửa sổ tính tăng trưởng (mặc định 28)."}},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "get_top_videos",
        "Các video/bài đăng nổi bật trên kênh của người dùng, sắp xếp theo lượt xem, thích, bình luận, chia sẻ hoặc mới nhất.",
        {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Lọc theo kênh (id từ get_channels_overview)."},
                "platform": {"type": "string", "enum": ["youtube", "facebook", "tiktok"]},
                "sort_by": {"type": "string", "enum": ["views", "likes", "comments", "shares", "recent"]},
                "days": {"type": "integer", "description": "Chỉ lấy video đăng trong N ngày gần đây."},
                "limit": {"type": "integer", "description": "1-50, mặc định 10."},
            },
            "additionalProperties": False,
        },
    ),
]

HANDLERS: dict[str, Callable[[Session, dict[str, Any]], dict[str, Any]]] = {
    "list_dubbing_jobs": tool_list_dubbing_jobs,
    "get_job_transcript": tool_get_job_transcript,
    "list_projects": tool_list_projects,
    "get_channels_overview": tool_get_channels_overview,
    "get_top_videos": tool_get_top_videos,
}

_HANDLERS_FOLDED = {name.casefold(): name for name in HANDLERS}


def run_tool(db: Session, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Execute a tool. Returns ``(result, is_error)``; never raises."""
    resolved = HANDLERS.get(name) and name or _HANDLERS_FOLDED.get((name or "").casefold())
    if not resolved:
        return {"error": f"Không có công cụ '{name}'. Công cụ hợp lệ: {', '.join(HANDLERS)}"}, True
    if not isinstance(args, dict) or "__invalid_json__" in args:
        return {"error": "Tham số công cụ không phải JSON hợp lệ, hãy gọi lại."}, True
    try:
        result = HANDLERS[resolved](db, args)
    except (ValueError, TypeError) as exc:
        return {"error": f"Tham số không hợp lệ: {exc}"}, True
    except SQLAlchemyError:
        db.rollback()
        return {"error": "Không đọc được dữ liệu cục bộ."}, True
    return result, "error" in result


def summarize_result(name: str, result: dict[str, Any]) -> str:
    """Short human-readable line for the UI's tool chip."""
    if result.get("error"):
        return str(result["error"])[:200]
    if result.get("available") is False:
        return "Chưa có dữ liệu kênh"
    if name == "list_dubbing_jobs":
        return f"{result.get('returned', 0)}/{result.get('total_jobs', 0)} video lồng tiếng"
    if name == "get_job_transcript":
        t = result.get("transcript") or {}
        chars = sum((v or {}).get("chars", 0) for v in t.values())
        return f"Transcript “{result.get('title', '')[:60]}” ({chars} ký tự)"
    if name == "list_projects":
        return f"{len(result.get('projects') or [])} dự án"
    if name == "get_channels_overview":
        return f"{len(result.get('channels') or [])} kênh"
    if name == "get_top_videos":
        return f"{len(result.get('videos') or [])} video nổi bật"
    return "OK"


def dumps(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


# --------------------------------------------- fallback for models w/o tools


def data_summary(db: Session, job_id: str | None = None) -> str:
    """Compact text snapshot of the user's data, injected into the system
    prompt when the model cannot call tools."""
    parts: list[str] = []
    jobs = tool_list_dubbing_jobs(db, {"limit": 15})
    if jobs.get("jobs"):
        lines = [
            f"- [{j['status']}] {j['title'][:90]} ({j['source_language']}→{j['target_language']}, {j['platform']}, id={j['id']})"
            for j in jobs["jobs"]
        ]
        parts.append(f"Video lồng tiếng gần đây ({jobs['returned']}/{jobs['total_jobs']}):\n" + "\n".join(lines))
    else:
        parts.append("Người dùng chưa có video lồng tiếng nào.")
    channels = tool_get_channels_overview(db, {})
    if channels.get("available"):
        lines = []
        for c in channels["channels"]:
            latest = c.get("latest") or {}
            stats = ", ".join(f"{k}={v}" for k, v in latest.items() if k != "date")
            lines.append(f"- {c['platform']}: {c['name']} ({stats or 'chưa có số liệu'})")
        parts.append("Kênh đã kết nối:\n" + "\n".join(lines))
        top = tool_get_top_videos(db, {"limit": 5})
        if top.get("videos"):
            parts.append(
                "Video nổi bật:\n"
                + "\n".join(f"- {v['title'][:90]} ({v['platform']}, views={v.get('views', '?')})" for v in top["videos"])
            )
    else:
        parts.append("Chưa có kênh mạng xã hội nào được kết nối.")
    if job_id:
        detail = tool_get_job_transcript(db, {"job_id": job_id, "kind": "translated", "max_chars": 6000})
        if not detail.get("error"):
            text = ((detail.get("transcript") or {}).get("translated") or {}).get("text") or ""
            if text:
                parts.append(f"Transcript (bản dịch) của video “{detail['title']}”:\n{text}")
    return "\n\n".join(parts)
