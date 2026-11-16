"""Conversation orchestration: persistence, tool loop, structured generation."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, AsyncIterator, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from ... import models
from . import prompts, tools
from .providers import (
    ProviderError,
    TextDelta,
    TurnResult,
    build_provider,
    looks_like_tools_unsupported,
    strip_tool_turns,
)
from .settings import ANTHROPIC, OPENAI_COMPATIBLE, PROVIDERS, content_model_for, load_settings, provider_configured

MAX_TOOL_ROUNDS = 8
# (base_url, model) of OpenAI-compatible models that rejected the tools field.
_TOOLS_UNSUPPORTED: set[tuple[str, str]] = set()

SessionFactory = Callable[[], Session]


# ------------------------------------------------------------------ helpers


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def make_title(text: str) -> str:
    line = " ".join((text or "").split())
    return (line[:60].rstrip() + "…") if len(line) > 60 else (line or "Cuộc trò chuyện mới")


def job_brief(db: Session, job_id: str | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    job = db.get(models.MediaJob, job_id)
    if not job:
        return None
    return {
        "id": job.id,
        "title": tools.job_title(job),
        "video_url": job.video_url,
        "source_language": job.source_language,
        "target_language": job.target_language,
        "description": (job.content or "")[:2000],
    }


def build_history(messages: list[models.AssistantMessage]) -> list[dict[str, Any]]:
    """Neutral history from persisted rows. Repairs interrupted tool rounds
    (a tool call without a result) so providers never reject the replay."""
    history: list[dict[str, Any]] = []
    open_calls: list[dict[str, Any]] = []

    def close_open_calls() -> None:
        for call in open_calls:
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": json.dumps({"error": "Bị gián đoạn trước khi có kết quả."}, ensure_ascii=False),
                    "is_error": True,
                }
            )
        open_calls.clear()

    for msg in messages:
        if msg.role == "tool":
            open_calls[:] = [c for c in open_calls if c["id"] != msg.tool_call_id]
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "name": msg.tool_name or "",
                    "content": msg.content,
                    "is_error": bool(msg.is_error),
                }
            )
            continue
        close_open_calls()
        if msg.role == "user":
            history.append({"role": "user", "content": msg.llm_content or msg.content})
        elif msg.role == "assistant":
            calls = _loads(msg.tool_calls_json, [])
            native = _loads(msg.native_json, None) if msg.provider == ANTHROPIC else None
            if not msg.content and not calls and not native:
                continue  # error placeholder
            history.append({"role": "assistant", "content": msg.content, "tool_calls": calls, "native": native})
            open_calls.extend(calls)
    close_open_calls()
    return history


def serialize_message(msg: models.AssistantMessage) -> dict[str, Any]:
    return {
        "id": msg.id,
        "seq": msg.seq,
        "role": msg.role,
        "content": msg.content,
        "mode": msg.mode,
        "tool_calls": _loads(msg.tool_calls_json, []),
        "tool_call_id": msg.tool_call_id,
        "tool_name": msg.tool_name,
        "is_error": bool(msg.is_error),
        "provider": msg.provider,
        "model": msg.model,
        "stop_reason": msg.stop_reason,
        "error": msg.error or None,
        "usage": _loads(msg.usage_json, {}),
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


def serialize_conversation(conv: models.AssistantConversation, *, message_count: int | None = None) -> dict[str, Any]:
    data = {
        "id": conv.id,
        "title": conv.title,
        "mode": conv.mode,
        "job_id": conv.job_id,
        "provider": conv.provider,
        "model": conv.model,
        "options": _loads(conv.options_json, {}),
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }
    if message_count is not None:
        data["message_count"] = message_count
    return data


def _next_seq(db: Session, conversation_id: str) -> int:
    current = db.query(func.max(models.AssistantMessage.seq)).filter(
        models.AssistantMessage.conversation_id == conversation_id
    ).scalar()
    return int(current or 0) + 1


def _add_message(db: Session, conversation_id: str, **fields: Any) -> models.AssistantMessage:
    msg = models.AssistantMessage(conversation_id=conversation_id, seq=_next_seq(db, conversation_id), **fields)
    db.add(msg)
    db.flush()
    return msg


def resolve_provider_name(settings: dict[str, Any], requested: str | None) -> str:
    name = requested or settings.get("active_provider") or ANTHROPIC
    if name not in PROVIDERS:
        raise ProviderError("not_configured", f"Nhà cung cấp không hợp lệ: {name}")
    ok, reason = provider_configured(settings, name)
    if not ok:
        raise ProviderError("not_configured", reason)
    return name


# --------------------------------------------------------------------- chat


async def run_chat(session_factory: SessionFactory, request: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield ``(event, data)`` pairs for the SSE endpoint. Never raises for
    provider problems — they become an ``error`` event."""
    settings = load_settings()
    mode = request.get("mode") or "chat"
    if mode not in prompts.MODES:
        mode = "chat"
    options = prompts.normalize_options(request.get("options"))
    text = (request.get("message") or "").strip()
    try:
        provider_name = resolve_provider_name(settings, request.get("provider"))
    except ProviderError as error:
        yield "error", error.as_dict()
        return
    model = request.get("model") or (content_model_for(settings, provider_name) if mode == "write" else "")
    provider = build_provider(settings, provider_name, model=model)

    db = session_factory()
    assistant_text: list[str] = []
    pending_partial: dict[str, Any] | None = None
    conversation_id = ""
    try:
        conv_id = request.get("conversation_id")
        created = False
        if conv_id:
            conv = db.get(models.AssistantConversation, conv_id)
            if not conv:
                yield "error", {"code": "not_found", "message": "Không tìm thấy cuộc trò chuyện."}
                return
        else:
            conv = models.AssistantConversation(title=make_title(text), mode=mode, job_id=request.get("job_id"))
            db.add(conv)
            db.flush()
            created = True
        conversation_id = conv.id
        if request.get("job_id"):
            conv.job_id = request["job_id"]
        job = job_brief(db, conv.job_id)
        header = prompts.mode_header(mode, options, job)
        user_msg = _add_message(
            db, conv.id, role="user", content=text, llm_content=header + text, mode=mode, provider=provider_name
        )
        conv.mode = mode
        conv.options_json = json.dumps(options, ensure_ascii=False)
        conv.provider = provider_name
        conv.model = provider.model
        conv.updated_at = datetime.utcnow()
        if not conv.title:
            conv.title = make_title(text)
        db.commit()
        yield "conversation", {
            "conversation_id": conv.id,
            "title": conv.title,
            "created": created,
            "user_message_id": user_msg.id,
        }

        history = build_history(
            db.query(models.AssistantMessage)
            .filter(models.AssistantMessage.conversation_id == conv.id)
            .order_by(models.AssistantMessage.seq)
            .all()
        )

        use_tools = True
        if provider_name == OPENAI_COMPATIBLE:
            key = (provider.base_url, provider.model)
            use_tools = provider.tools_mode == "on" or (provider.tools_mode == "auto" and key not in _TOOLS_UNSUPPORTED)
        effort = prompts.MODE_EFFORT.get(mode, "low")
        last_message_id = None
        stop_reason = ""
        usage_total: dict[str, int] = {}
        model_used = provider.model

        for round_index in range(MAX_TOOL_ROUNDS):
            if use_tools:
                system, turn_history, turn_tools = prompts.SYSTEM_PROMPT, history, tools.TOOLS
            else:
                summary = tools.data_summary(db, conv.job_id)
                system = prompts.SYSTEM_PROMPT + prompts.NO_TOOLS_ADDENDUM.format(summary=summary)
                turn_history, turn_tools = strip_tool_turns(history), None

            result: TurnResult | None = None
            round_text: list[str] = []
            pending_partial = {"round_text": round_text}
            try:
                async for event in provider.stream_turn(system, turn_history, turn_tools, effort=effort):
                    if isinstance(event, TextDelta):
                        round_text.append(event.text)
                        yield "text", {"delta": event.text}
                    else:
                        result = event
            except ProviderError as error:
                if (
                    provider_name == OPENAI_COMPATIBLE
                    and use_tools
                    and provider.tools_mode == "auto"
                    and not round_text
                    and looks_like_tools_unsupported(error)
                ):
                    _TOOLS_UNSUPPORTED.add((provider.base_url, provider.model))
                    use_tools = False
                    yield "notice", {
                        "code": "tools_unsupported",
                        "message": "Model này không hỗ trợ gọi công cụ; trợ lý dùng bản tóm tắt dữ liệu thay thế.",
                    }
                    result = None
                    async for event in _retry_without_tools(provider, db, conv.job_id, history, effort, round_text):
                        if isinstance(event, TextDelta):
                            yield "text", {"delta": event.text}
                        else:
                            result = event
                else:
                    raise
            pending_partial = None
            assert result is not None
            model_used = result.model or model_used
            for k, v in result.usage.items():
                if isinstance(v, int):
                    usage_total[k] = usage_total.get(k, 0) + v
            assistant_text.append(result.text)
            calls = result.tool_calls if use_tools else []
            msg = _add_message(
                db,
                conv.id,
                role="assistant",
                content=result.text,
                mode=mode,
                tool_calls_json=json.dumps(calls, ensure_ascii=False),
                native_json=json.dumps(result.native, ensure_ascii=False) if provider_name == ANTHROPIC and result.native else None,
                provider=provider_name,
                model=result.model or provider.model,
                stop_reason=result.stop_reason,
                usage_json=json.dumps(result.usage),
            )
            db.commit()
            last_message_id = msg.id
            stop_reason = result.stop_reason
            history.append({"role": "assistant", "content": result.text, "tool_calls": calls, "native": result.native if provider_name == ANTHROPIC else None})

            if result.stop_reason == "refusal":
                details = result.refusal or {}
                msg.error = "refusal"
                db.commit()
                yield "error", {
                    "code": "refusal",
                    "message": "Mô hình từ chối yêu cầu này. Hãy diễn đạt lại hoặc thử nhà cung cấp/model khác.",
                    "detail": {"category": details.get("category")},
                }
                return
            if not calls:
                break
            for call in calls:
                yield "tool_call", {"id": call["id"], "name": call["name"], "input": call.get("input") or {}}
                output, is_error = await asyncio.to_thread(_run_tool_isolated, session_factory, call["name"], call.get("input") or {})
                content = tools.dumps(output)
                _add_message(
                    db,
                    conv.id,
                    role="tool",
                    content=content,
                    mode=mode,
                    tool_call_id=call["id"],
                    tool_name=call["name"],
                    is_error=is_error,
                    provider=provider_name,
                )
                db.commit()
                history.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": content, "is_error": is_error})
                yield "tool_result", {
                    "id": call["id"],
                    "name": call["name"],
                    "ok": not is_error,
                    "summary": tools.summarize_result(call["name"], output),
                }
        else:
            yield "notice", {"code": "tool_limit", "message": "Đã đạt giới hạn số lần tra cứu dữ liệu trong một lượt."}

        conv.updated_at = datetime.utcnow()
        conv.model = model_used
        db.commit()
        yield "done", {
            "conversation_id": conv.id,
            "message_id": last_message_id,
            "stop_reason": stop_reason,
            "provider": provider_name,
            "model": model_used,
            "usage": usage_total,
            "text": "".join(assistant_text),
        }
    except ProviderError as error:
        _persist_partial(db, conversation_id, pending_partial, provider_name, provider.model, mode, error=error.code)
        yield "error", error.as_dict()
    except (asyncio.CancelledError, GeneratorExit):
        _persist_partial(db, conversation_id, pending_partial, provider_name, provider.model, mode, error="cancelled")
        raise
    finally:
        db.close()


async def _retry_without_tools(provider, db, job_id, history, effort, round_text):
    summary = tools.data_summary(db, job_id)
    system = prompts.SYSTEM_PROMPT + prompts.NO_TOOLS_ADDENDUM.format(summary=summary)
    async for event in provider.stream_turn(system, strip_tool_turns(history), None, effort=effort):
        if isinstance(event, TextDelta):
            round_text.append(event.text)
        yield event


def _run_tool_isolated(session_factory: SessionFactory, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    db = session_factory()
    try:
        return tools.run_tool(db, name, args)
    finally:
        db.close()


def _persist_partial(db, conversation_id, pending, provider_name, model, mode, *, error: str) -> None:
    """Keep whatever text streamed before a failure/cancel, flagged with the error."""
    if not conversation_id:
        return
    try:
        db.rollback()
        text = "".join((pending or {}).get("round_text") or [])
        _add_message(
            db,
            conversation_id,
            role="assistant",
            content=text,
            mode=mode,
            provider=provider_name,
            model=model,
            stop_reason="error" if error != "cancelled" else "cancelled",
            error=error,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - best effort; never mask the original error
        db.rollback()


# ------------------------------------------------------ structured generation


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = _FENCE.sub("", (text or "").strip())
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(candidate[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n]+|\s+(?=#)", value)
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def normalize_job_content(data: dict[str, Any]) -> dict[str, Any]:
    hashtags = []
    for tag in _as_list(data.get("hashtags")):
        tag = "#" + re.sub(r"\s+", "", tag.lstrip("#"))
        if len(tag) > 1 and tag not in hashtags:
            hashtags.append(tag)
    return {
        "title": str(data.get("title") or "").strip(),
        "alt_titles": _as_list(data.get("alt_titles")),
        "description": str(data.get("description") or "").strip(),
        "hashtags": hashtags,
        "tags": [t.lstrip("#") for t in _as_list(data.get("tags"))],
        "tiktok_caption": str(data.get("tiktok_caption") or "").strip(),
        "facebook_post": str(data.get("facebook_post") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
    }


def _job_and_transcript(session_factory: SessionFactory, job_id: str, max_chars: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """The job brief and its translated transcript (source when not translated). Raises ``LookupError``."""
    db = session_factory()
    try:
        job = job_brief(db, job_id)
        if not job:
            raise LookupError(job_id)
        transcript = tools.read_transcript(job_id, "translated", max_chars)["translated"]
        if not transcript.get("available"):
            transcript = tools.read_transcript(job_id, "source", max_chars)["source"]
    finally:
        db.close()
    return job, transcript


async def _json_answer(provider, system: str, user: str, schema: dict[str, Any], accept: Callable[[dict[str, Any]], Any]):
    """Ask for one JSON object; retry once when the reply is not usable. Returns ``(accepted, model)``."""
    history: list[dict[str, Any]] = [{"role": "user", "content": user}]
    raw = ""
    model_used = provider.model
    for _attempt in range(2):
        result: TurnResult | None = None
        async for event in provider.stream_turn(system, history, None, effort="low", json_schema=schema):
            if isinstance(event, TurnResult):
                result = event
        assert result is not None
        if result.stop_reason == "refusal":
            raise ProviderError("refusal", "Mô hình từ chối yêu cầu này.")
        raw = result.text
        model_used = result.model or model_used
        parsed = parse_json_object(raw)
        accepted = accept(parsed) if parsed is not None else None
        if accepted:
            return accepted, model_used
        history = history + [
            {"role": "assistant", "content": raw or "(trống)"},
            {"role": "user", "content": "Phản hồi trước không phải JSON hợp lệ. Chỉ trả về đúng một đối tượng JSON theo yêu cầu, không thêm chữ nào khác."},
        ]
    raise ProviderError("invalid_output", "Mô hình không trả về JSON hợp lệ.", detail={"raw": raw[:2000]})


def _content_provider(provider_name: str | None, model: str | None):
    settings = load_settings()
    name = resolve_provider_name(settings, provider_name)
    return name, build_provider(settings, name, model=model or content_model_for(settings, name))


async def generate_job_content(
    session_factory: SessionFactory,
    job_id: str,
    *,
    options: dict[str, Any] | None = None,
    extra_instructions: str = "",
    provider_name: str | None = None,
    model: str | None = None,
    max_transcript_chars: int = 20000,
) -> dict[str, Any]:
    """Raises ``LookupError`` for a missing job and ``ProviderError`` otherwise."""
    name, provider = _content_provider(provider_name, model)
    job, transcript = _job_and_transcript(session_factory, job_id, max_transcript_chars)
    opts = prompts.normalize_options(options)
    user = prompts.job_content_user_prompt(
        job, transcript.get("text") or "", bool(transcript.get("truncated")), opts, (extra_instructions or "").strip()[:2000]
    )

    def accept(parsed: dict[str, Any]) -> dict[str, Any] | None:
        content = normalize_job_content(parsed)
        return content if content["title"] or content["description"] else None

    content, model_used = await _json_answer(provider, prompts.JOB_CONTENT_SYSTEM, user, prompts.JOB_CONTENT_SCHEMA, accept)
    return {
        "job_id": job_id,
        "provider": name,
        "model": model_used,
        "transcript_used": bool(transcript.get("available")),
        "transcript_truncated": bool(transcript.get("truncated")),
        "content": content,
    }


def normalize_post_drafts(data: dict[str, Any], count: int) -> list[dict[str, Any]]:
    drafts = []
    for item in data.get("drafts") or []:
        if not isinstance(item, dict):
            continue
        caption = str(item.get("caption") or "").strip()
        title = str(item.get("title") or "").strip()
        if not caption and not title:
            continue
        hashtags = normalize_job_content({"hashtags": item.get("hashtags")})["hashtags"]
        drafts.append({"title": title, "caption": caption, "hashtags": hashtags})
    return drafts[:count]


async def generate_post_drafts(
    session_factory: SessionFactory,
    job_id: str,
    *,
    platform: str | None = None,
    tone: str | None = None,
    count: int = 2,
    extra_instructions: str = "",
    provider_name: str | None = None,
    model: str | None = None,
    max_transcript_chars: int = 20000,
) -> dict[str, Any]:
    """A few ready-to-post versions (title, caption, hashtags) of a dubbed video for one platform.

    Raises ``LookupError`` for a missing job and ``ProviderError`` otherwise.
    """
    count = max(1, min(3, count))
    name, provider = _content_provider(provider_name, model)
    job, transcript = _job_and_transcript(session_factory, job_id, max_transcript_chars)
    user = prompts.post_drafts_user_prompt(
        job,
        transcript.get("text") or "",
        bool(transcript.get("truncated")),
        prompts.normalize_options({"platform": platform, "tone": tone}),
        count,
        (extra_instructions or "").strip()[:2000],
    )
    drafts, model_used = await _json_answer(
        provider, prompts.POST_DRAFTS_SYSTEM, user, prompts.POST_DRAFTS_SCHEMA, lambda parsed: normalize_post_drafts(parsed, count)
    )
    return {
        "job_id": job_id,
        "provider": name,
        "model": model_used,
        "transcript_used": bool(transcript.get("available")),
        "drafts": drafts,
    }
