"""AI assistant API: provider settings, conversations, streaming chat (SSE)
and structured content generation for dubbing jobs. See
``app/services/assistant`` for the implementation."""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models
from .database import SessionLocal, get_db
from .services.assistant import engine, prompts
from .services.assistant.providers import ProviderError, build_provider
from .services.assistant.settings import (
    ANTHROPIC,
    DEFAULT_ANTHROPIC_MODEL,
    OPENAI_COMPATIBLE,
    OPENAI_PRESETS,
    SettingsError,
    apply_patch,
    load_settings,
    public_settings,
    update_settings,
)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

ProviderName = Literal["anthropic", "openai_compatible"]
Mode = Literal["chat", "write", "strategy"]


def get_session_factory():
    """Dependency returning a session factory; the SSE generator outlives the
    request scope, so it opens its own sessions. Overridden in tests."""
    return SessionLocal


# ------------------------------------------------------------------ schemas


class AnthropicSettingsIn(BaseModel):
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    effort: Literal["auto", "low", "medium", "high"] | None = None
    refusal_fallback: bool | None = None


class OpenAISettingsIn(BaseModel):
    preset: Literal["openrouter", "openai", "ollama", "lmstudio", "custom"] | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    content_model: str | None = Field(default=None, max_length=200)
    tools_mode: Literal["auto", "on", "off"] | None = None


class SettingsPatch(BaseModel):
    active_provider: ProviderName | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=128000)
    anthropic: AnthropicSettingsIn | None = None
    openai_compatible: OpenAISettingsIn | None = None


class TestConnectionIn(SettingsPatch):
    provider: ProviderName | None = None


class ChatOptions(BaseModel):
    platform: str | None = None
    tone: str | None = None
    length: str | None = None
    language: str | None = None
    content_type: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None
    mode: Mode = "chat"
    options: ChatOptions | None = None
    job_id: str | None = None
    provider: ProviderName | None = None
    model: str | None = Field(default=None, max_length=200, description="Use this model instead of the provider's saved one.")


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    mode: Mode = "chat"
    job_id: str | None = None
    options: ChatOptions | None = None


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    mode: Mode | None = None
    job_id: str | None = None
    options: ChatOptions | None = None


class PostDraftsRequest(BaseModel):
    platform: str | None = None
    tone: str | None = None
    count: int = Field(default=2, ge=1, le=3)
    extra_instructions: str = Field(default="", max_length=2000)
    provider: ProviderName | None = None
    model: str | None = Field(default=None, max_length=200)


class JobContentRequest(BaseModel):
    platform: str | None = None
    tone: str | None = None
    language: str | None = None
    extra_instructions: str = Field(default="", max_length=2000)
    provider: ProviderName | None = None
    model: str | None = Field(default=None, max_length=200)


# ------------------------------------------------------------------ helpers


_STATUS = {
    "not_configured": 400,
    "invalid_output": 502,
    "refusal": 422,
    "not_found": 502,  # upstream model/endpoint not found
}


def _provider_http_error(error: ProviderError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(error.code, 502), detail=error.as_dict())


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _get_conversation(db: Session, conversation_id: str) -> models.AssistantConversation:
    conv = db.get(models.AssistantConversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Không tìm thấy cuộc trò chuyện."})
    return conv


# ------------------------------------------------------------ providers/settings


@router.get("/providers")
def list_providers() -> dict[str, Any]:
    return {
        "providers": [
            {
                "id": ANTHROPIC,
                "label": "Anthropic Claude",
                "requires_key": True,
                "supports_tools": True,
                "supports_model_list": True,
                "default_model": DEFAULT_ANTHROPIC_MODEL,
                "suggested_models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
            },
            {
                "id": OPENAI_COMPATIBLE,
                "label": "OpenAI-compatible (OpenAI, Ollama, LM Studio)",
                "requires_key": False,
                "supports_tools": "model-dependent",
                "supports_model_list": True,
                "presets": [{"id": key, **value} for key, value in OPENAI_PRESETS.items()],
            },
        ]
    }


@router.get("/settings")
def read_settings() -> dict[str, Any]:
    return public_settings(load_settings())


@router.put("/settings")
def write_settings(payload: SettingsPatch) -> dict[str, Any]:
    try:
        settings = update_settings(payload.model_dump(exclude_none=True))
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_settings", "message": str(exc)}) from exc
    return public_settings(settings)


@router.post("/settings/test")
async def test_connection(payload: TestConnectionIn) -> dict[str, Any]:
    """Test saved settings, optionally overlaid with unsaved form values."""
    patch = payload.model_dump(exclude_none=True)
    provider_name = patch.pop("provider", None)
    try:
        settings = apply_patch(load_settings(), patch)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_settings", "message": str(exc)}) from exc
    name = provider_name or settings["active_provider"]
    provider = build_provider(settings, name)
    try:
        result = await provider.test()
    except ProviderError as error:
        return {"ok": False, "provider": name, "model": provider.model, "error_code": error.code, "message": error.message}
    return {"provider": name, **result}


@router.get("/models")
async def list_models(provider: ProviderName | None = None) -> dict[str, Any]:
    settings = load_settings()
    name = provider or settings["active_provider"]
    try:
        items = await build_provider(settings, name).list_models()
    except ProviderError as error:
        raise _provider_http_error(error) from error
    return {"provider": name, "models": items}


@router.get("/quick-actions")
def quick_actions() -> dict[str, Any]:
    return {
        "modes": [
            {"id": "chat", "label": "Trò chuyện"},
            {"id": "write", "label": "Viết nội dung"},
            {"id": "strategy", "label": "Chiến lược"},
        ],
        "platforms": [{"id": k, "label": v} for k, v in prompts.PLATFORMS.items()],
        "tones": [{"id": k, "label": v} for k, v in prompts.TONES.items()],
        "lengths": [{"id": k, "label": v} for k, v in prompts.LENGTHS.items()],
        "actions": prompts.QUICK_ACTIONS,
    }


# ------------------------------------------------------------- conversations


@router.get("/conversations")
def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    job_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = db.query(models.AssistantConversation)
    if job_id:
        query = query.filter(models.AssistantConversation.job_id == job_id)
    total = query.count()
    rows = query.order_by(models.AssistantConversation.updated_at.desc()).offset(offset).limit(limit).all()
    counts = dict(
        db.query(models.AssistantMessage.conversation_id, func.count(models.AssistantMessage.id))
        .filter(
            models.AssistantMessage.conversation_id.in_([c.id for c in rows] or [""]),
            models.AssistantMessage.role != "tool",
        )
        .group_by(models.AssistantMessage.conversation_id)
        .all()
    )
    return {
        "total": total,
        "items": [engine.serialize_conversation(c, message_count=int(counts.get(c.id, 0))) for c in rows],
    }


@router.post("/conversations", status_code=201)
def create_conversation(payload: ConversationCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    conv = models.AssistantConversation(
        title=(payload.title or "").strip() or "Cuộc trò chuyện mới",
        mode=payload.mode,
        job_id=payload.job_id,
        options_json=json.dumps(prompts.normalize_options(payload.options.model_dump() if payload.options else {}), ensure_ascii=False),
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {**engine.serialize_conversation(conv, message_count=0), "messages": []}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    include_tools: bool = True,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conv = _get_conversation(db, conversation_id)
    messages = [m for m in conv.messages if include_tools or m.role != "tool"]
    return {
        **engine.serialize_conversation(conv, message_count=sum(1 for m in conv.messages if m.role != "tool")),
        "messages": [engine.serialize_message(m) for m in messages],
    }


@router.patch("/conversations/{conversation_id}")
def patch_conversation(conversation_id: str, payload: ConversationPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    conv = _get_conversation(db, conversation_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("title"):
        conv.title = data["title"].strip()
    if data.get("mode"):
        conv.mode = data["mode"]
    if "job_id" in data:
        conv.job_id = data["job_id"]
    if data.get("options") is not None:
        conv.options_json = json.dumps(prompts.normalize_options(data["options"]), ensure_ascii=False)
    db.commit()
    db.refresh(conv)
    return engine.serialize_conversation(conv)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)) -> None:
    conv = _get_conversation(db, conversation_id)
    db.delete(conv)
    db.commit()


# ---------------------------------------------------------------------- chat


@router.post("/chat")
async def chat(payload: ChatRequest, session_factory=Depends(get_session_factory)) -> StreamingResponse:
    request = payload.model_dump()
    if payload.options:
        request["options"] = payload.options.model_dump(exclude_none=True)

    async def stream():
        async for event, data in engine.run_chat(session_factory, request):
            yield _sse(event, data)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/generate-content")
async def generate_job_content(
    job_id: str,
    payload: JobContentRequest,
    session_factory=Depends(get_session_factory),
) -> dict[str, Any]:
    try:
        return await engine.generate_job_content(
            session_factory,
            job_id,
            options=payload.model_dump(include={"platform", "tone", "language"}, exclude_none=True),
            extra_instructions=payload.extra_instructions,
            provider_name=payload.provider,
            model=payload.model,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "Không tìm thấy video."}) from exc
    except ProviderError as error:
        raise _provider_http_error(error) from error


@router.post("/jobs/{job_id}/post-drafts")
async def generate_post_drafts(
    job_id: str,
    payload: PostDraftsRequest,
    session_factory=Depends(get_session_factory),
) -> dict[str, Any]:
    """Ready-to-post versions of a dubbed video for one platform (calendar post form)."""
    try:
        return await engine.generate_post_drafts(
            session_factory,
            job_id,
            platform=payload.platform,
            tone=payload.tone,
            count=payload.count,
            extra_instructions=payload.extra_instructions,
            provider_name=payload.provider,
            model=payload.model,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "Không tìm thấy video."}) from exc
    except ProviderError as error:
        raise _provider_http_error(error) from error
