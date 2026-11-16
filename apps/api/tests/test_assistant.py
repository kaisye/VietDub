from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import assistant_api, models
from app.database import Base, get_db
from app.services import storage
from app.services.assistant import engine as assistant_engine
from app.services.assistant import providers, settings as assistant_settings, tools

ANTHROPIC_KEY = "sk-ant-api03-secretvalue-1234567890-WXYZ"


# ------------------------------------------------------------------ fixtures


class Recorder:
    """httpx MockTransport handler serving queued responses and recording requests."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.responses: list = []

    def queue(self, item) -> None:
        self.responses.append(item)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(request)
        return item

    def body(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path / "storage")
    storage.ensure_storage()
    monkeypatch.setenv("AETHER_ASSISTANT_SETTINGS_PATH", str(tmp_path / "cfg" / "assistant-settings.json"))
    monkeypatch.delenv("AETHER_ASSISTANT_SECRET_KEY", raising=False)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(assistant_api.router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[assistant_api.get_session_factory] = lambda: Session
    recorder = Recorder()
    monkeypatch.setattr(providers, "HTTP_TRANSPORT", httpx.MockTransport(recorder))
    assistant_engine._TOOLS_UNSUPPORTED.clear()
    client = TestClient(app)
    return {"client": client, "Session": Session, "http": recorder, "storage": tmp_path / "storage", "tmp": tmp_path}


def configure_anthropic(client, **extra):
    body = {"active_provider": "anthropic", "anthropic": {"api_key": ANTHROPIC_KEY, **extra}}
    assert client.put("/api/assistant/settings", json=body).status_code == 200


def configure_openai(client, **extra):
    section = {"preset": "ollama", "model": "qwen3:8b", **extra}
    body = {"active_provider": "openai_compatible", "openai_compatible": section}
    assert client.put("/api/assistant/settings", json=body).status_code == 200


def anthropic_sse(blocks: list[dict], stop_reason: str = "end_turn", model: str = "claude-sonnet-5-5") -> httpx.Response:
    """Build an Anthropic streaming response from simplified blocks."""
    events = [{"type": "message_start", "message": {"id": "msg_1", "model": model, "usage": {"input_tokens": 50, "output_tokens": 1}}}]
    for index, block in enumerate(blocks):
        kind = block["type"]
        if kind == "text":
            events.append({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
            for piece in block["chunks"]:
                events.append({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": piece}})
        elif kind == "thinking":
            events.append({"type": "content_block_start", "index": index, "content_block": {"type": "thinking", "thinking": ""}})
            events.append({"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": block["thinking"]}})
            events.append({"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": block["signature"]}})
        elif kind == "tool_use":
            events.append({"type": "content_block_start", "index": index, "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}})
            raw = json.dumps(block["input"])
            for start in range(0, len(raw), 7):
                events.append({"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": raw[start : start + 7]}})
        events.append({"type": "content_block_stop", "index": index})
    events.append({"type": "message_delta", "delta": {"stop_reason": stop_reason}, "usage": {"output_tokens": 42}})
    events.append({"type": "message_stop"})
    payload = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    payload = "event: ping\ndata: {\"type\": \"ping\"}\n\n" + payload
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload.encode())


def openai_sse(chunks: list[dict]) -> httpx.Response:
    payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload.encode())


def oa_chunk(delta: dict, finish: str | None = None) -> dict:
    return {"id": "c1", "model": "qwen3:8b", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def parse_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for frame in text.strip().split("\n\n"):
        name, data = "", ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if name:
            events.append((name, json.loads(data)))
    return events


def chat(client, **payload) -> list[tuple[str, dict]]:
    response = client.post("/api/assistant/chat", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_events(response.text)


def seed_job(env, title="Review iPhone 17 Pro", job_id="job-1") -> str:
    with env["Session"]() as db:
        db.add(
            models.MediaJob(
                id=job_id,
                video_url="https://youtu.be/abc",
                source_title=title,
                source_language="en",
                target_language="vi",
                status=models.MediaJobStatus.ready,
                created_at=datetime(2026, 10, 1),
            )
        )
        db.commit()
    subtitles = env["storage"] / "subtitles"
    (subtitles / f"{job_id}.en.vi.render.display.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nXin chào các bạn.\n\n2\n00:00:02,000 --> 00:00:04,000\n<i>Hôm nay mình review iPhone.</i>\n",
        encoding="utf-8",
    )
    (subtitles / f"{job_id}.en.vi.render.source.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello everyone.\n", encoding="utf-8"
    )
    return job_id


# ------------------------------------------------------------------ settings


def test_settings_masks_and_encrypts_api_key(env):
    client = env["client"]
    configure_anthropic(client)

    data = client.get("/api/assistant/settings").json()
    section = data["anthropic"]
    assert "api_key" not in section
    assert section["api_key_set"] is True
    assert section["api_key_masked"].startswith("sk-ant") and section["api_key_masked"].endswith("WXYZ")
    assert ANTHROPIC_KEY not in json.dumps(data)
    assert section["configured"] is True and section["model"] == "claude-sonnet-5-5"

    on_disk = (env["tmp"] / "cfg" / "assistant-settings.json").read_text()
    assert ANTHROPIC_KEY not in on_disk and "enc:" in on_disk

    # Echoing the masked value back keeps the stored key.
    client.put("/api/assistant/settings", json={"anthropic": {"api_key": section["api_key_masked"], "model": "claude-opus-5-5"}})
    assert assistant_settings.load_settings()["anthropic"]["api_key"] == ANTHROPIC_KEY
    # Blank clears it.
    cleared = client.put("/api/assistant/settings", json={"anthropic": {"api_key": ""}}).json()
    assert cleared["anthropic"]["api_key_set"] is False and cleared["anthropic"]["configured"] is False


def test_settings_preset_sets_base_url_and_validates(env):
    client = env["client"]
    defaults = client.get("/api/assistant/settings").json()
    assert defaults["active_provider"] == "openai_compatible"
    assert defaults["openai_compatible"]["preset"] == "openrouter"
    assert defaults["openai_compatible"]["model"] == "qwen/qwen3.7-flash"
    data = client.put("/api/assistant/settings", json={"openai_compatible": {"preset": "lmstudio"}}).json()
    assert data["openai_compatible"]["base_url"] == "http://localhost:1234/v1"
    assert data["openai_compatible"]["configured"] is False  # no model yet
    assert client.put("/api/assistant/settings", json={"openai_compatible": {"tools_mode": "maybe"}}).status_code == 422
    data = client.put("/api/assistant/settings", json={"openai_compatible": {"preset": "openrouter"}}).json()
    assert data["openai_compatible"]["base_url"] == "https://openrouter.ai/api/v1"
    assert data["openai_compatible"]["model"] == "qwen/qwen3.7-flash"
    assert data["openai_compatible"]["configured"] is False  # OpenRouter needs a key
    providers_list = client.get("/api/assistant/providers").json()["providers"]
    assert {p["id"] for p in providers_list} == {"anthropic", "openai_compatible"}


def test_connection_and_models(env):
    client, http = env["client"], env["http"]
    configure_anthropic(client)
    http.queue(httpx.Response(200, json={"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"}))
    result = client.post("/api/assistant/settings/test", json={}).json()
    assert result["ok"] is True and "Claude Sonnet 5.5" in result["message"]
    assert http.requests[-1].url.path == "/v1/models/claude-sonnet-5-5"
    assert http.requests[-1].headers["x-api-key"] == ANTHROPIC_KEY

    http.queue(httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
    result = client.post("/api/assistant/settings/test", json={"anthropic": {"api_key": "sk-ant-wrong-key-000000"}}).json()
    assert result["ok"] is False and result["error_code"] == "auth"
    # The unsaved override was not persisted.
    assert assistant_settings.load_settings()["anthropic"]["api_key"] == ANTHROPIC_KEY

    configure_openai(client)
    http.queue(httpx.Response(200, json={"object": "list", "data": [{"id": "qwen3:8b"}, {"id": "llama3.2"}]}))
    models_out = client.get("/api/assistant/models").json()
    assert [m["id"] for m in models_out["models"]] == ["llama3.2", "qwen3:8b"]
    assert str(http.requests[-1].url) == "http://localhost:11434/v1/models"

    http.queue(httpx.Response(200, json={"data": [{"id": "llama3.2"}]}))
    result = client.post("/api/assistant/settings/test", json={"provider": "openai_compatible"}).json()
    assert result["ok"] is False and result["error_code"] == "model_missing"

    http.queue(httpx.ConnectError("refused"))
    result = client.post("/api/assistant/settings/test", json={"provider": "openai_compatible"}).json()
    assert result["ok"] is False and result["error_code"] == "network"


# ------------------------------------------------------------------ anthropic


def test_anthropic_streaming_chat_persists_conversation(env):
    client, http = env["client"], env["http"]
    configure_anthropic(client)
    http.queue(anthropic_sse([{"type": "text", "chunks": ["Xin ", "chào", "!"]}]))

    events = chat(client, message="Chào bạn", mode="chat")
    names = [name for name, _ in events]
    assert names[0] == "conversation" and names[-1] == "done"
    assert "".join(d["delta"] for n, d in events if n == "text") == "Xin chào!"
    done = events[-1][1]
    assert done["text"] == "Xin chào!" and done["stop_reason"] == "end_turn" and done["usage"]["output_tokens"] == 42

    request = http.requests[0]
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == ANTHROPIC_KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["anthropic-beta"] == providers.FALLBACK_BETA
    body = http.body(0)
    assert body["model"] == "claude-sonnet-5-5" and body["stream"] is True
    assert body["fallbacks"] == "default" and body["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"] == {"effort": "low"}
    assert {t["name"] for t in body["tools"]} >= {"list_dubbing_jobs", "get_job_transcript", "get_channels_overview"}
    assert "thinking" not in body and "temperature" not in body
    assert body["messages"][0]["content"].startswith("[Ngữ cảnh]") and body["messages"][0]["content"].endswith("Chào bạn")

    conv_id = events[0][1]["conversation_id"]
    listed = client.get("/api/assistant/conversations").json()
    assert listed["total"] == 1 and listed["items"][0]["message_count"] == 2
    detail = client.get(f"/api/assistant/conversations/{conv_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "Chào bạn"  # header is not shown in the UI
    assert detail["messages"][1]["content"] == "Xin chào!"
    assert detail["title"] == "Chào bạn"


def test_anthropic_tool_loop_replays_native_blocks(env):
    client, http = env["client"], env["http"]
    configure_anthropic(client)
    seed_job(env)
    http.queue(
        anthropic_sse(
            [
                {"type": "thinking", "thinking": "", "signature": "sig-abc"},
                {"type": "text", "chunks": ["Để mình xem dữ liệu."]},
                {"type": "tool_use", "id": "toolu_1", "name": "list_dubbing_jobs", "input": {"limit": 5}},
            ],
            stop_reason="tool_use",
        )
    )
    http.queue(anthropic_sse([{"type": "text", "chunks": ["Bạn có 1 video: Review iPhone 17 Pro."]}]))

    events = chat(client, message="Tôi có những video nào?", mode="strategy")
    names = [n for n, _ in events]
    assert names.count("tool_call") == 1 and names.count("tool_result") == 1
    tool_call = next(d for n, d in events if n == "tool_call")
    assert tool_call == {"id": "toolu_1", "name": "list_dubbing_jobs", "input": {"limit": 5}}
    tool_result = next(d for n, d in events if n == "tool_result")
    assert tool_result["ok"] is True and "1/1" in tool_result["summary"]
    assert events[-1][1]["text"].endswith("Review iPhone 17 Pro.")

    first, second = http.body(0), http.body(1)
    assert first["output_config"]["effort"] == "medium"  # strategy mode
    assert second["system"] == first["system"] and second["tools"] == first["tools"]
    assistant_turn = second["messages"][1]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    assert assistant_turn["content"][2] == {"type": "tool_use", "id": "toolu_1", "name": "list_dubbing_jobs", "input": {"limit": 5}}
    result_turn = second["messages"][2]
    assert result_turn["role"] == "user" and result_turn["content"][0]["tool_use_id"] == "toolu_1"
    assert "Review iPhone 17 Pro" in result_turn["content"][0]["content"]

    # Next user turn replays the earlier history unchanged (append-only).
    http.queue(anthropic_sse([{"type": "text", "chunks": ["OK"]}]))
    conv_id = events[0][1]["conversation_id"]
    chat(client, message="Cảm ơn", conversation_id=conv_id, mode="chat")
    third = http.body(2)
    assert third["system"] == first["system"]
    assert third["messages"][:3] == second["messages"][:3]
    assert third["messages"][3]["content"][0]["text"] == "Bạn có 1 video: Review iPhone 17 Pro."
    assert third["messages"][4]["content"].endswith("Cảm ơn")

    detail = client.get(f"/api/assistant/conversations/{conv_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "tool", "assistant", "user", "assistant"]
    assert detail["messages"][1]["tool_calls"][0]["name"] == "list_dubbing_jobs"


def test_anthropic_job_context_and_transcript_tool(env):
    client, http = env["client"], env["http"]
    configure_anthropic(client)
    job_id = seed_job(env)
    http.queue(
        anthropic_sse(
            [{"type": "tool_use", "id": "toolu_t", "name": "get_job_transcript", "input": {"job_id": job_id, "kind": "both"}}],
            stop_reason="tool_use",
        )
    )
    http.queue(anthropic_sse([{"type": "text", "chunks": ["Caption: ..."]}]))
    events = chat(client, message="Viết caption", mode="write", job_id=job_id, options={"platform": "tiktok", "tone": "funny"})
    assert events[-1][0] == "done"
    header = http.body(0)["messages"][0]["content"]
    assert "Nền tảng: TikTok" in header and "Review iPhone 17 Pro" in header and job_id in header
    tool_payload = json.loads(http.body(1)["messages"][2]["content"][0]["content"])
    assert tool_payload["transcript"]["translated"]["text"] == "Xin chào các bạn.\nHôm nay mình review iPhone."
    assert tool_payload["transcript"]["source"]["text"] == "Hello everyone."


def test_anthropic_errors_are_reported_as_sse_events(env):
    client, http = env["client"], env["http"]

    client.put("/api/assistant/settings", json={"active_provider": "anthropic"})
    events = chat(client, message="hi")
    assert events == [("error", {"code": "not_configured", "message": "Chưa nhập Anthropic API key."})]
    assert client.get("/api/assistant/conversations").json()["total"] == 0

    configure_anthropic(client)
    http.queue(httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
    events = chat(client, message="hi")
    assert events[0][0] == "conversation"
    assert events[-1][0] == "error" and events[-1][1]["code"] == "auth" and events[-1][1]["status"] == 401
    conv_id = events[0][1]["conversation_id"]
    messages = client.get(f"/api/assistant/conversations/{conv_id}").json()["messages"]
    assert messages[-1]["role"] == "assistant" and messages[-1]["error"] == "auth"

    http.queue(httpx.ConnectError("connection refused"))
    events = chat(client, message="hi again", conversation_id=conv_id)
    assert events[-1][1]["code"] == "network"

    # Mid-stream error event (overloaded) keeps the partial text.
    overloaded = (
        'event: message_start\ndata: {"type":"message_start","message":{"model":"claude-sonnet-5-5","usage":{}}}\n\n'
        'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Một phần"}}\n\n'
        'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n\n'
    )
    http.queue(httpx.Response(200, headers={"content-type": "text/event-stream"}, content=overloaded.encode()))
    events = chat(client, message="lần 3", conversation_id=conv_id)
    assert ("text", {"delta": "Một phần"}) in events
    assert events[-1][1]["code"] == "provider_unavailable"
    messages = client.get(f"/api/assistant/conversations/{conv_id}").json()["messages"]
    assert messages[-1]["content"] == "Một phần" and messages[-1]["error"] == "provider_unavailable"

    # The failed turns don't corrupt the next request: errors with no text are skipped.
    http.queue(anthropic_sse([{"type": "text", "chunks": ["ok"]}]))
    chat(client, message="lần 4", conversation_id=conv_id)
    roles = [m["role"] for m in http.body(len(http.requests) - 1)["messages"]]
    assert roles == ["user", "user", "user", "assistant", "user"]

    missing = chat(client, message="x", conversation_id="nope")
    assert missing == [("error", {"code": "not_found", "message": "Không tìm thấy cuộc trò chuyện."})]


def test_anthropic_refusal(env):
    client, http = env["client"], env["http"]
    configure_anthropic(client)
    http.queue(anthropic_sse([], stop_reason="refusal"))
    events = chat(client, message="...")
    assert events[-1][0] == "error" and events[-1][1]["code"] == "refusal"


def test_fallback_blocks_are_sanitized():
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "text", "text": "partial"},
        {"type": "tool_use", "id": "t", "name": "x", "input": {}},
        {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
        {"type": "thinking", "thinking": "", "signature": "s2"},
        {"type": "text", "text": "rest"},
    ]
    kept = providers.sanitize_fallback_blocks(blocks)
    assert [b["type"] for b in kept] == ["text", "fallback", "thinking", "text"]


def test_effort_and_fallback_only_for_supported_models():
    assert providers.supports_effort("claude-sonnet-5-5")
    assert providers.supports_effort("claude-opus-4-5")
    assert not providers.supports_effort("claude-haiku-4-5")
    assert not providers.supports_effort("claude-3-5-haiku-20241022")
    provider = providers.AnthropicProvider({"api_key": "k", "model": "claude-haiku-4-5", "base_url": "https://proxy.local"})
    body, betas = provider.build_body("sys", [{"role": "user", "content": "hi"}], None)
    assert "output_config" not in body and "fallbacks" not in body and betas == []


# ------------------------------------------------------------- openai-compatible


def test_openai_streaming_tool_calls(env):
    client, http = env["client"], env["http"]
    configure_openai(client)
    seed_job(env)
    http.queue(
        openai_sse(
            [
                oa_chunk({"role": "assistant", "content": ""}),
                oa_chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "list_dubbing_jobs", "arguments": ""}}]}),
                oa_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"sta'}}]}),
                oa_chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'tus": "ready"}'}}]}),
                oa_chunk({}, finish="tool_calls"),
            ]
        )
    )
    http.queue(openai_sse([oa_chunk({"content": "Bạn có "}), oa_chunk({"content": "1 video."}), oa_chunk({}, finish="stop")]))

    events = chat(client, message="Video của tôi?")
    tool_call = next(d for n, d in events if n == "tool_call")
    assert tool_call["name"] == "list_dubbing_jobs" and tool_call["input"] == {"status": "ready"}
    assert events[-1][0] == "done" and events[-1][1]["text"] == "Bạn có 1 video."

    assert str(http.requests[0].url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in http.requests[0].headers  # Ollama: no key
    first = http.body(0)
    assert first["model"] == "qwen3:8b" and first["stream"] is True and first["tool_choice"] == "auto"
    assert first["messages"][0]["role"] == "system"
    assert first["tools"][0]["type"] == "function"
    second = http.body(1)["messages"]
    assert second[2]["role"] == "assistant" and second[2]["tool_calls"][0]["function"]["name"] == "list_dubbing_jobs"
    assert json.loads(second[2]["tool_calls"][0]["function"]["arguments"]) == {"status": "ready"}
    assert second[3]["role"] == "tool" and second[3]["tool_call_id"] == "call_1"
    assert "Review iPhone 17 Pro" in second[3]["content"]


def test_openai_sends_bearer_key(env):
    client, http = env["client"], env["http"]
    configure_openai(client, preset="openai", api_key="sk-proj-abcdefghijklmnop", model="gpt-4o-mini")
    http.queue(openai_sse([oa_chunk({"content": "hi"}, finish="stop")]))
    chat(client, message="hi")
    assert http.requests[0].headers["authorization"] == "Bearer sk-proj-abcdefghijklmnop"
    assert str(http.requests[0].url) == "https://api.openai.com/v1/chat/completions"
    masked = client.get("/api/assistant/settings").json()["openai_compatible"]["api_key_masked"]
    assert "abcdefghij" not in masked


def test_chat_can_pick_a_model_for_one_request(env):
    client, http = env["client"], env["http"]
    configure_openai(client, preset="openai", api_key="sk-proj-abcdefghijklmnop", model="gpt-4o-mini")
    http.queue(openai_sse([oa_chunk({"content": "hi"}, finish="stop")]))
    http.queue(openai_sse([oa_chunk({"content": "hi"}, finish="stop")]))
    chat(client, message="Viết caption", mode="write", model="deepseek/deepseek-chat")
    assert http.body(0)["model"] == "deepseek/deepseek-chat"
    chat(client, message="hi")
    assert http.body(1)["model"] == "gpt-4o-mini"  # The saved model is untouched.


def test_openai_without_tool_support_falls_back_to_data_summary(env):
    client, http = env["client"], env["http"]
    configure_openai(client, model="gemma:2b")
    seed_job(env)
    http.queue(httpx.Response(400, json={"error": {"message": "registry.ollama.ai/library/gemma:2b does not support tools"}}))
    http.queue(openai_sse([oa_chunk({"content": "Bạn có video Review iPhone."}, finish="stop")]))

    events = chat(client, message="Tôi có video gì?")
    notice = next(d for n, d in events if n == "notice")
    assert notice["code"] == "tools_unsupported"
    assert events[-1][0] == "done" and events[-1][1]["text"] == "Bạn có video Review iPhone."
    retry = http.body(1)
    assert "tools" not in retry
    assert "Review iPhone 17 Pro" in retry["messages"][0]["content"]  # summary in system prompt

    # Remembered: the next turn goes straight to the no-tools path.
    http.queue(openai_sse([oa_chunk({"content": "ok"}, finish="stop")]))
    chat(client, message="tiếp", conversation_id=events[0][1]["conversation_id"])
    assert "tools" not in http.body(2)
    assert len(http.requests) == 3


def test_openai_not_configured_and_network_down(env):
    client, http = env["client"], env["http"]
    client.put("/api/assistant/settings", json={"active_provider": "openai_compatible"})
    events = chat(client, message="hi")
    assert events[0][0] == "error" and events[0][1]["code"] == "not_configured"

    configure_openai(client)
    http.queue(httpx.ConnectError("refused"))
    events = chat(client, message="hi")
    assert events[-1][1]["code"] == "network" and "localhost:11434" in events[-1][1]["message"]


# ------------------------------------------------------------- conversations


def test_conversation_crud(env):
    client = env["client"]
    created = client.post("/api/assistant/conversations", json={"title": "Kế hoạch tháng 11", "mode": "strategy"})
    assert created.status_code == 201
    conv = created.json()
    assert conv["title"] == "Kế hoạch tháng 11" and conv["messages"] == []

    renamed = client.patch(f"/api/assistant/conversations/{conv['id']}", json={"title": "Kế hoạch Q4"}).json()
    assert renamed["title"] == "Kế hoạch Q4"
    assert client.get("/api/assistant/conversations").json()["items"][0]["title"] == "Kế hoạch Q4"
    assert client.patch(f"/api/assistant/conversations/{conv['id']}", json={"title": ""}).status_code == 422

    assert client.delete(f"/api/assistant/conversations/{conv['id']}").status_code == 204
    assert client.get(f"/api/assistant/conversations/{conv['id']}").status_code == 404
    assert client.delete(f"/api/assistant/conversations/{conv['id']}").status_code == 404


def test_delete_conversation_removes_messages(env):
    client, http = env["client"], env["http"]
    configure_anthropic(client)
    http.queue(anthropic_sse([{"type": "text", "chunks": ["hi"]}]))
    conv_id = chat(client, message="hello")[0][1]["conversation_id"]
    client.delete(f"/api/assistant/conversations/{conv_id}")
    with env["Session"]() as db:
        assert db.query(models.AssistantMessage).count() == 0


def test_build_history_repairs_interrupted_tool_round():
    rows = [
        models.AssistantMessage(role="user", content="q", llm_content="[h] q", seq=1),
        models.AssistantMessage(
            role="assistant", content="", seq=2, provider="anthropic",
            tool_calls_json=json.dumps([{"id": "t1", "name": "list_projects", "input": {}}]),
        ),
        models.AssistantMessage(role="assistant", content="", seq=3, error="cancelled"),
        models.AssistantMessage(role="user", content="again", seq=4),
    ]
    history = assistant_engine.build_history(rows)
    assert [h["role"] for h in history] == ["user", "assistant", "tool", "user"]
    assert history[0]["content"] == "[h] q"
    assert history[2]["tool_call_id"] == "t1" and history[2]["is_error"] is True


def test_quick_actions(env):
    data = env["client"].get("/api/assistant/quick-actions").json()
    assert {m["id"] for m in data["modes"]} == {"chat", "write", "strategy"}
    assert any(a["mode"] == "strategy" for a in data["actions"])


# ------------------------------------------------------------------- tools


def test_channel_tools_degrade_and_read_data(env):
    with env["Session"]() as db:
        overview = tools.run_tool(db, "get_channels_overview", {})[0]
        assert overview["available"] is False
        channel = models.Channel(platform="youtube", name="Kênh Review", status="connected")
        db.add(channel)
        db.flush()
        db.add_all(
            [
                models.ChannelSnapshot(channel_id=channel.id, date=(datetime.utcnow()).strftime("%Y-%m-%d"), followers=1500, views=90000),
                models.ChannelSnapshot(channel_id=channel.id, date="2000-01-01", followers=10, views=100),
                models.ChannelVideo(channel_id=channel.id, external_id="v1", title="Video A", views=500),
                models.ChannelVideo(channel_id=channel.id, external_id="v2", title="Video B", views=9000),
            ]
        )
        db.commit()
        overview = tools.run_tool(db, "get_channels_overview", {})[0]
        assert overview["available"] is True
        assert overview["channels"][0]["latest"]["followers"] == 1500
        top = tools.run_tool(db, "get_top_videos", {"limit": 5})[0]
        assert [v["title"] for v in top["videos"]] == ["Video B", "Video A"]
        result, is_error = tools.run_tool(db, "nope", {})
        assert is_error and "nope" in result["error"]
        result, is_error = tools.run_tool(db, "LIST_PROJECTS", {})
        assert not is_error and result == {"projects": []}


def test_channel_tools_when_tables_missing(env, monkeypatch):
    monkeypatch.delattr(models, "Channel")
    with env["Session"]() as db:
        assert tools.run_tool(db, "get_top_videos", {})[0]["available"] is False
        assert "Chưa có kênh" in tools.data_summary(db)


def test_srt_to_text():
    raw = "﻿1\n00:00:01,000 --> 00:00:02,000\n<b>Một</b>\n\n2\n00:00:02,000 --> 00:00:03,000\n{\\an8}Hai\nHai\n"
    assert tools.srt_to_text(raw) == "Một\nHai"


# ------------------------------------------------------- structured generation


def test_generate_job_content_parses_json(env):
    client, http = env["client"], env["http"]
    configure_anthropic(client)
    job_id = seed_job(env)
    payload = {
        "title": "iPhone 17 Pro có đáng mua?",
        "alt_titles": ["Review iPhone 17 Pro", "Đập hộp iPhone 17 Pro"],
        "description": "Mô tả...",
        "hashtags": ["iphone17", "#review", "#iphone17"],
        "tags": ["iphone", "#apple"],
        "tiktok_caption": "iPhone mới nè #iphone",
        "facebook_post": "Bạn nghĩ sao?",
        "summary": "Review iPhone.",
    }
    text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    http.queue(anthropic_sse([{"type": "text", "chunks": [text[:20], text[20:]]}]))

    response = client.post(f"/api/assistant/jobs/{job_id}/generate-content", json={"platform": "youtube", "tone": "dramatic"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["transcript_used"] is True and data["provider"] == "anthropic"
    content = data["content"]
    assert content["title"] == "iPhone 17 Pro có đáng mua?"
    assert content["hashtags"] == ["#iphone17", "#review"]
    assert content["tags"] == ["iphone", "apple"]

    body = http.body(0)
    assert "tools" not in body
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "Hôm nay mình review iPhone." in body["messages"][0]["content"]
    assert client.get("/api/assistant/conversations").json()["total"] == 0  # not a chat


def test_generate_job_content_errors(env):
    client, http = env["client"], env["http"]
    job_id = seed_job(env)
    not_configured = client.post(f"/api/assistant/jobs/{job_id}/generate-content", json={})
    assert not_configured.status_code == 400 and not_configured.json()["detail"]["code"] == "not_configured"

    configure_openai(client)
    assert client.post("/api/assistant/jobs/missing/generate-content", json={}).status_code == 404

    http.queue(openai_sse([oa_chunk({"content": "Xin lỗi, tôi không chắc."}, finish="stop")]))
    http.queue(openai_sse([oa_chunk({"content": "Vẫn không phải JSON"}, finish="stop")]))
    bad = client.post(f"/api/assistant/jobs/{job_id}/generate-content", json={})
    assert bad.status_code == 502 and bad.json()["detail"]["code"] == "invalid_output"

    http.queue(openai_sse([oa_chunk({"content": "Đây: {\"title\": \"T\", \"description\": \"D\", \"hashtags\": \"#a #b\"}"}, finish="stop")]))
    ok = client.post(f"/api/assistant/jobs/{job_id}/generate-content", json={})
    assert ok.status_code == 200 and ok.json()["content"]["hashtags"] == ["#a", "#b"]

    http.queue(httpx.Response(429, json={"error": {"message": "slow down"}}))
    limited = client.post(f"/api/assistant/jobs/{job_id}/generate-content", json={})
    assert limited.status_code == 502 and limited.json()["detail"]["code"] == "rate_limited"


def test_parse_json_object():
    assert assistant_engine.parse_json_object('{"a": 1}') == {"a": 1}
    assert assistant_engine.parse_json_object("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert assistant_engine.parse_json_object("text {\"a\": {\"b\": 2}} tail") == {"a": {"b": 2}}
    assert assistant_engine.parse_json_object("[1, 2]") is None
    assert assistant_engine.parse_json_object("nope") is None


def test_content_is_written_with_the_content_model_on_openrouter(env):
    client, http = env["client"], env["http"]
    configure_openai(client, preset="openrouter", api_key="sk-or-abcdefghijklmnop", model="qwen/qwen3.7-flash")
    for _ in range(3):
        http.queue(openai_sse([oa_chunk({"content": "ok"}, finish="stop")]))
    chat(client, message="Viết caption", mode="write")
    chat(client, message="Kênh của tôi thế nào?")
    chat(client, message="Viết caption", mode="write", model="deepseek/deepseek-chat")
    assert [http.body(i)["model"] for i in range(3)] == [
        assistant_settings.DEFAULT_CONTENT_MODEL,
        "qwen/qwen3.7-flash",  # Q&A keeps the main model.
        "deepseek/deepseek-chat",  # An explicit pick wins.
    ]

    # Other OpenAI-compatible servers do not serve OpenRouter ids: no content model there.
    configure_openai(client)
    http.queue(openai_sse([oa_chunk({"content": "ok"}, finish="stop")]))
    chat(client, message="Viết caption", mode="write")
    assert http.body(3)["model"] == "qwen3:8b"


def test_post_drafts_for_a_job(env):
    client, http = env["client"], env["http"]
    job_id = seed_job(env)
    configure_openai(client, preset="openrouter", api_key="sk-or-abcdefghijklmnop")
    drafts = {
        "drafts": [
            {"title": "A", "caption": "Bài A", "hashtags": ["#iphone", "review"]},
            {"title": "B", "caption": "Bài B", "hashtags": "#apple #longtieng"},
            {"title": "C", "caption": "thừa", "hashtags": []},
        ]
    }
    http.queue(openai_sse([oa_chunk({"content": json.dumps(drafts, ensure_ascii=False)}, finish="stop")]))
    response = client.post(f"/api/assistant/jobs/{job_id}/post-drafts", json={"platform": "facebook", "count": 2})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["drafts"] == [
        {"title": "A", "caption": "Bài A", "hashtags": ["#iphone", "#review"]},
        {"title": "B", "caption": "Bài B", "hashtags": ["#apple", "#longtieng"]},
    ]
    sent = http.body(0)
    assert sent["model"] == assistant_settings.DEFAULT_CONTENT_MODEL
    prompt = sent["messages"][-1]["content"]
    assert "Nền tảng: Facebook" in prompt and "Số phương án: 2" in prompt and "Hôm nay mình review iPhone." in prompt

    assert client.post("/api/assistant/jobs/missing/post-drafts", json={}).status_code == 404
