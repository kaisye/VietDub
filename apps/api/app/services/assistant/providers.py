"""LLM provider adapters over plain httpx (no SDKs, keeps the PyInstaller bundle small).

Both adapters consume a provider-neutral history and yield normalized events:

* ``TextDelta(text)`` — visible answer text as it streams
* ``TurnResult(...)`` — emitted once at the end of a model turn: full text,
  tool calls, stop reason, usage and the provider-native assistant content
  (Anthropic blocks incl. thinking signatures) for verbatim replay later.

Neutral history items::

    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id","name","input"}], "native": list|None}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from .settings import ANTHROPIC, DEFAULT_ANTHROPIC_BASE_URL, OPENAI_COMPATIBLE

ANTHROPIC_VERSION = "2023-06-01"
# Server-side refusal fallback ("default" routing). Claude API only.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5"}

# Tests swap this for ``httpx.MockTransport``.
HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


# ----------------------------------------------------------------------- types


class ProviderError(Exception):
    """Normalized provider failure. ``code`` is stable for the UI."""

    def __init__(self, code: str, message: str, status: int | None = None, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.status:
            data["status"] = self.status
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class TextDelta:
    text: str


@dataclass
class TurnResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    native: list[dict[str, Any]] | None = None
    model: str = ""
    refusal: dict[str, Any] | None = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, transport=HTTP_TRANSPORT)


def _error_message_from_body(body: str) -> str:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body.strip()[:500]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:500]
        if isinstance(err, str):
            return err[:500]
        if data.get("message"):
            return str(data["message"])[:500]
        if data.get("detail"):
            return str(data["detail"])[:500]
    return body.strip()[:500]


def http_error(status: int, body: str) -> ProviderError:
    message = _error_message_from_body(body) or f"HTTP {status}"
    if status in (401, 403):
        return ProviderError("auth", f"API key không hợp lệ hoặc không có quyền ({message}).", status)
    if status == 404:
        return ProviderError("not_found", f"Không tìm thấy model hoặc endpoint ({message}).", status)
    if status == 429:
        return ProviderError("rate_limited", f"Nhà cung cấp đang giới hạn tốc độ, thử lại sau ({message}).", status)
    if status in (400, 413, 422):
        return ProviderError("bad_request", message, status)
    if status == 529 or status >= 500:
        return ProviderError("provider_unavailable", f"Nhà cung cấp đang quá tải hoặc lỗi ({message}).", status)
    return ProviderError("provider_error", message, status)


def network_error(exc: Exception, base_url: str) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("timeout", f"Hết thời gian chờ khi kết nối {base_url}.")
    return ProviderError("network", f"Không kết nối được tới {base_url}. Kiểm tra mạng hoặc máy chủ đã chạy chưa.")


async def iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """Yield ``(event, data)`` pairs from a text/event-stream response."""
    event = ""
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = "", []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:
        yield event, "\n".join(data_lines)


def _model_version(model: str) -> tuple[str, float] | None:
    match = re.match(r"^claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2}))?(?:$|-)", model)
    if not match:
        return None
    major = int(match.group(2))
    minor = int(match.group(3)) if match.group(3) and len(match.group(3)) <= 2 else 0
    return match.group(1), major + minor / 10


def supports_effort(model: str) -> bool:
    parsed = _model_version(model)
    if not parsed:
        return False
    family, version = parsed
    if family == "opus":
        return version >= 4.5
    return version >= 4.6 or family in ("fable", "mythos")


def supports_structured_output(model: str) -> bool:
    parsed = _model_version(model)
    return bool(parsed and parsed[1] >= 4.5)


# ------------------------------------------------------------------- Anthropic


def sanitize_fallback_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """After a mid-output server-side fallback, drop model-internal blocks that
    precede the last ``fallback`` marker (thinking / tool_use / unknown)."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if last < 0:
        return blocks
    keep_types = {"text", "fallback"}
    return [b for i, b in enumerate(blocks) if i > last or b.get("type") in keep_types]


def to_anthropic_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush_results() -> None:
        nonlocal pending_results
        if pending_results:
            messages.append({"role": "user", "content": pending_results})
            pending_results = []

    for item in history:
        role = item["role"]
        if role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": item["tool_call_id"],
                    "content": item.get("content") or "",
                    **({"is_error": True} if item.get("is_error") else {}),
                }
            )
            continue
        flush_results()
        if role == "user":
            messages.append({"role": "user", "content": item.get("content") or ""})
        elif role == "assistant":
            native = item.get("native")
            if native:
                messages.append({"role": "assistant", "content": native})
                continue
            blocks: list[dict[str, Any]] = []
            if item.get("content"):
                blocks.append({"type": "text", "text": item["content"]})
            for call in item.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": call.get("input") or {}})
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
    flush_results()
    return messages


class AnthropicProvider:
    name = ANTHROPIC

    def __init__(self, section: dict[str, Any], max_tokens: int = 16000):
        self.api_key = section.get("api_key") or ""
        self.model = section.get("model") or ""
        self.base_url = (section.get("base_url") or DEFAULT_ANTHROPIC_BASE_URL).rstrip("/")
        self.effort_setting = section.get("effort") or "auto"
        self.refusal_fallback = bool(section.get("refusal_fallback", True))
        self.max_tokens = max_tokens

    @property
    def official(self) -> bool:
        return self.base_url == DEFAULT_ANTHROPIC_BASE_URL

    def _headers(self, betas: list[str] | None = None) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if betas:
            headers["anthropic-beta"] = ",".join(betas)
        return headers

    def build_body(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec] | None,
        *,
        effort: str = "low",
        json_schema: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "stream": True,
            "system": [{"type": "text", "text": system}],
            "messages": to_anthropic_messages(history),
            # Automatic prompt caching of the stable prefix (tools -> system -> history).
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        output_config: dict[str, Any] = {}
        chosen = effort if self.effort_setting == "auto" else self.effort_setting
        if supports_effort(self.model) and chosen:
            output_config["effort"] = chosen
        if json_schema and supports_structured_output(self.model):
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            body["output_config"] = output_config
        betas: list[str] = []
        if self.refusal_fallback and self.official and self.model in FALLBACK_MODELS:
            body["fallbacks"] = "default"
            betas.append(FALLBACK_BETA)
        return body, betas

    async def stream_turn(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        *,
        effort: str = "low",
        json_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[TextDelta | TurnResult]:
        if not self.api_key:
            raise ProviderError("not_configured", "Chưa nhập Anthropic API key.")
        body, betas = self.build_body(system, history, tools, effort=effort, json_schema=json_schema)
        url = f"{self.base_url}/v1/messages"
        try:
            async with _client() as client:
                async with client.stream("POST", url, headers=self._headers(betas), json=body) as response:
                    if response.status_code >= 400:
                        raise http_error(response.status_code, (await response.aread()).decode("utf-8", "replace"))
                    async for event in self._parse(response):
                        yield event
        except httpx.HTTPError as exc:
            raise network_error(exc, self.base_url) from exc

    async def _parse(self, response: httpx.Response) -> AsyncIterator[TextDelta | TurnResult]:
        blocks: dict[int, dict[str, Any]] = {}
        partial_json: dict[int, str] = {}
        result = TurnResult(model=self.model)
        usage: dict[str, Any] = {}
        finished = False
        async for event, data in iter_sse(response):
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            kind = payload.get("type") or event
            if kind == "message_start":
                message = payload.get("message") or {}
                result.model = message.get("model") or result.model
                usage.update(message.get("usage") or {})
            elif kind == "content_block_start":
                index = int(payload.get("index", len(blocks)))
                block = dict(payload.get("content_block") or {})
                if block.get("type") == "tool_use":
                    block["input"] = {}
                    partial_json[index] = ""
                elif block.get("type") == "text":
                    block["text"] = block.get("text") or ""
                    if block["text"]:
                        yield TextDelta(block["text"])
                elif block.get("type") == "thinking":
                    block.setdefault("thinking", "")
                    block.setdefault("signature", "")
                blocks[index] = block
            elif kind == "content_block_delta":
                index = int(payload.get("index", 0))
                delta = payload.get("delta") or {}
                block = blocks.setdefault(index, {"type": "text", "text": ""})
                dtype = delta.get("type")
                if dtype == "text_delta":
                    text = delta.get("text") or ""
                    block["text"] = (block.get("text") or "") + text
                    if text:
                        yield TextDelta(text)
                elif dtype == "input_json_delta":
                    partial_json[index] = partial_json.get(index, "") + (delta.get("partial_json") or "")
                elif dtype == "thinking_delta":
                    block["thinking"] = (block.get("thinking") or "") + (delta.get("thinking") or "")
                elif dtype == "signature_delta":
                    block["signature"] = (block.get("signature") or "") + (delta.get("signature") or "")
                elif dtype == "citations_delta":
                    block.setdefault("citations", []).append(delta.get("citation"))
            elif kind == "content_block_stop":
                index = int(payload.get("index", 0))
                block = blocks.get(index)
                if block is not None and block.get("type") == "tool_use":
                    raw = partial_json.get(index, "")
                    try:
                        block["input"] = json.loads(raw) if raw.strip() else {}
                    except json.JSONDecodeError:
                        block["input"] = {"__invalid_json__": raw}
            elif kind == "message_delta":
                delta = payload.get("delta") or {}
                if delta.get("stop_reason"):
                    result.stop_reason = delta["stop_reason"]
                if delta.get("stop_details"):
                    result.refusal = delta["stop_details"]
                usage.update(payload.get("usage") or {})
            elif kind == "message_stop":
                finished = True
            elif kind == "error":
                err = payload.get("error") or {}
                etype = err.get("type") or "provider_error"
                code = "provider_unavailable" if etype in ("overloaded_error", "api_error") else "provider_error"
                raise ProviderError(code, err.get("message") or "Lỗi từ Anthropic.", detail={"type": etype})
        if not finished and not result.stop_reason:
            raise ProviderError("network", "Kết nối bị ngắt giữa chừng khi đang nhận phản hồi.")
        ordered = [blocks[i] for i in sorted(blocks)]
        ordered = sanitize_fallback_blocks(ordered)
        result.native = ordered
        result.text = "".join(b.get("text") or "" for b in ordered if b.get("type") == "text")
        result.tool_calls = [
            {"id": b["id"], "name": b["name"], "input": b.get("input") or {}}
            for b in ordered
            if b.get("type") == "tool_use"
        ]
        result.usage = {k: v for k, v in usage.items() if isinstance(v, (int, float, str))}
        yield result

    async def list_models(self) -> list[dict[str, Any]]:
        if not self.api_key:
            raise ProviderError("not_configured", "Chưa nhập Anthropic API key.")
        try:
            async with _client() as client:
                response = await client.get(f"{self.base_url}/v1/models", params={"limit": 100}, headers=self._headers())
        except httpx.HTTPError as exc:
            raise network_error(exc, self.base_url) from exc
        if response.status_code >= 400:
            raise http_error(response.status_code, response.text)
        data = response.json().get("data") or []
        return [{"id": m.get("id"), "name": m.get("display_name") or m.get("id")} for m in data if m.get("id")]

    async def test(self) -> dict[str, Any]:
        """Validate key + model without spending tokens (GET /v1/models/{id})."""
        if not self.api_key:
            raise ProviderError("not_configured", "Chưa nhập Anthropic API key.")
        started = time.monotonic()
        try:
            async with _client() as client:
                response = await client.get(f"{self.base_url}/v1/models/{self.model}", headers=self._headers())
        except httpx.HTTPError as exc:
            raise network_error(exc, self.base_url) from exc
        if response.status_code >= 400:
            raise http_error(response.status_code, response.text)
        latency = int((time.monotonic() - started) * 1000)
        name = (response.json() or {}).get("display_name") or self.model
        return {"ok": True, "latency_ms": latency, "model": self.model, "message": f"Kết nối thành công tới {name}."}


# ----------------------------------------------------------- OpenAI-compatible


def to_openai_messages(system: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for item in history:
        role = item["role"]
        if role == "user":
            messages.append({"role": "user", "content": item.get("content") or ""})
        elif role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": item.get("content") or ""}
            calls = item.get("tool_calls") or []
            if calls:
                msg["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c.get("input") or {}, ensure_ascii=False)},
                    }
                    for c in calls
                ]
            messages.append(msg)
        elif role == "tool":
            messages.append({"role": "tool", "tool_call_id": item["tool_call_id"], "content": item.get("content") or ""})
    return messages


def strip_tool_turns(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """History for a model without tool support: keep only user/assistant text."""
    out = []
    for item in history:
        if item["role"] == "tool":
            continue
        if item["role"] == "assistant":
            if not item.get("content"):
                continue
            out.append({"role": "assistant", "content": item["content"]})
        else:
            out.append(item)
    return out


_TOOLS_UNSUPPORTED = re.compile(r"(does not support tools|tools? (are |is )?not supported|tool.?call|function.?call|unsupported.*tool|\btools\b)", re.I)


def looks_like_tools_unsupported(error: ProviderError) -> bool:
    return error.code == "bad_request" and bool(_TOOLS_UNSUPPORTED.search(error.message or ""))


class OpenAICompatibleProvider:
    name = OPENAI_COMPATIBLE

    def __init__(self, section: dict[str, Any], max_tokens: int | None = None):
        self.base_url = (section.get("base_url") or "").rstrip("/")
        self.api_key = section.get("api_key") or ""
        self.model = section.get("model") or ""
        self.tools_mode = section.get("tools_mode") or "auto"
        self.max_tokens = max_tokens

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in self.base_url:
            # Optional OpenRouter app attribution.
            headers["x-title"] = "VietDub"
            headers["http-referer"] = "https://github.com/kaisye/VietDub"
        return headers

    def _require(self) -> None:
        if not self.base_url:
            raise ProviderError("not_configured", "Chưa nhập Base URL cho máy chủ OpenAI-compatible.")
        if not self.model:
            raise ProviderError("not_configured", "Chưa chọn model.")

    async def stream_turn(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        *,
        effort: str = "low",
        json_schema: dict[str, Any] | None = None,
    ) -> AsyncIterator[TextDelta | TurnResult]:
        self._require()
        body: dict[str, Any] = {
            "model": self.model,
            "stream": True,
            "messages": to_openai_messages(system, history),
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
            body["tool_choice"] = "auto"
        if json_schema and "api.openai.com" in self.base_url:
            body["response_format"] = {"type": "json_object"}
        url = f"{self.base_url}/chat/completions"
        try:
            async with _client() as client:
                async with client.stream("POST", url, headers=self._headers(), json=body) as response:
                    if response.status_code >= 400:
                        raise http_error(response.status_code, (await response.aread()).decode("utf-8", "replace"))
                    async for event in self._parse(response):
                        yield event
        except httpx.HTTPError as exc:
            raise network_error(exc, self.base_url) from exc

    async def _parse(self, response: httpx.Response) -> AsyncIterator[TextDelta | TurnResult]:
        result = TurnResult(model=self.model)
        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        got_any = False
        async for _event, data in iter_sse(response):
            if data.strip() == "[DONE]":
                got_any = True
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            got_any = True
            if isinstance(payload.get("error"), (dict, str)):
                err = payload["error"]
                message = err.get("message") if isinstance(err, dict) else err
                raise ProviderError("provider_error", str(message or "Lỗi từ máy chủ."))
            if payload.get("model"):
                result.model = payload["model"]
            if payload.get("usage"):
                result.usage = {k: v for k, v in payload["usage"].items() if isinstance(v, (int, float))}
            for choice in payload.get("choices") or []:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield TextDelta(content)
                for call in delta.get("tool_calls") or []:
                    index = int(call.get("index", len(calls)))
                    slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if call.get("id"):
                        slot["id"] = call["id"]
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        args = fn["arguments"]
                        slot["arguments"] += args if isinstance(args, str) else json.dumps(args)
                if choice.get("finish_reason"):
                    result.stop_reason = choice["finish_reason"]
        if not got_any:
            raise ProviderError("network", "Máy chủ không trả về dữ liệu.")
        result.text = "".join(text_parts)
        for index in sorted(calls):
            slot = calls[index]
            if not slot["name"]:
                continue
            try:
                parsed = json.loads(slot["arguments"]) if slot["arguments"].strip() else {}
            except json.JSONDecodeError:
                parsed = {"__invalid_json__": slot["arguments"]}
            result.tool_calls.append(
                {"id": slot["id"] or f"call_{index}_{int(time.time() * 1000)}", "name": slot["name"], "input": parsed if isinstance(parsed, dict) else {}}
            )
        if result.tool_calls and result.stop_reason in ("", "stop"):
            result.stop_reason = "tool_calls"
        yield result

    async def list_models(self) -> list[dict[str, Any]]:
        if not self.base_url:
            raise ProviderError("not_configured", "Chưa nhập Base URL cho máy chủ OpenAI-compatible.")
        try:
            async with _client() as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise network_error(exc, self.base_url) from exc
        if response.status_code >= 400:
            raise http_error(response.status_code, response.text)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("provider_error", "Máy chủ trả về dữ liệu không hợp lệ cho /models.") from exc
        items = data.get("data") if isinstance(data, dict) else data
        models = []
        for m in items or []:
            if isinstance(m, dict) and m.get("id"):
                models.append({"id": m["id"], "name": m.get("name") or m["id"]})
        return sorted(models, key=lambda m: m["id"])

    async def test(self) -> dict[str, Any]:
        self._require()
        started = time.monotonic()
        try:
            models = await self.list_models()
        except ProviderError as error:
            if error.code != "not_found":
                raise
            models = []
        if models and self.model not in {m["id"] for m in models}:
            return {
                "ok": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "model": self.model,
                "error_code": "model_missing",
                "message": f"Kết nối được nhưng không thấy model '{self.model}' trên máy chủ.",
                "models": [m["id"] for m in models][:50],
            }
        if not models:
            # /models unsupported: probe with a 1-token completion.
            async for event in self.stream_turn("ping", [{"role": "user", "content": "ping"}]):
                if isinstance(event, TurnResult):
                    break
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "model": self.model,
            "message": "Kết nối thành công.",
        }


def build_provider(settings: dict[str, Any], provider: str | None = None):
    name = provider or settings.get("active_provider") or ANTHROPIC
    if name == ANTHROPIC:
        return AnthropicProvider(settings[ANTHROPIC], max_tokens=int(settings.get("max_tokens") or 16000))
    if name == OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(settings[OPENAI_COMPATIBLE])
    raise ProviderError("not_configured", f"Nhà cung cấp không hợp lệ: {name}")
