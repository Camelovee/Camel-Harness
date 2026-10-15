from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from .message import (
    AssistantMessage,
    LlmStopReason,
    LlmUsage,
    Message,
    TextBlock,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)
from .protocol import (
    LlmAdapter,
    LlmErrorCode,
    LlmRequest,
    LlmStreamEvent,
    MessageCompleted,
    ReasoningDelta,
    StreamFailed,
    StreamStarted,
    TextDelta,
    ToolCallArgumentsDelta,
    ToolCallCompleted,
    ToolCallStarted,
    UsageUpdated,
)


def create_openai_adapter() -> OpenAiLlmAdapter:
    """创建 OpenAI 官方 SDK 适配器；凭据随请求进入，适配器无状态。"""

    return OpenAiLlmAdapter()


def _default_client(api_key: str, base_url: str | None) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)


class OpenAiLlmAdapter(LlmAdapter):
    """使用 OpenAI 官方异步 SDK Responses API 的流式适配器。"""

    def __init__(
        self,
        client_factory: Callable[[str, str | None], AsyncOpenAI] = _default_client,
    ) -> None:
        self._client_factory = client_factory

    @property
    def provider_id(self) -> str:
        return "openai"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        target = request.model
        if not target.api_key:
            yield StreamFailed("OpenAI 请求缺少 API Key", retryable=False)
            return
        client = self._client_factory(target.api_key, target.base_url)
        try:
            async for event in self._stream_with_client(client, request):
                yield event
        finally:
            await client.close()

    async def _stream_with_client(
        self, client: AsyncOpenAI, request: LlmRequest
    ) -> AsyncIterator[LlmStreamEvent]:
        input_items = self._convert_messages(request.messages)
        tools = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
                "strict": False,
            }
            for tool in request.tools
        ]
        kwargs: dict[str, Any] = {
            "model": request.model.model,
            "input": input_items,
            "max_output_tokens": request.max_output_tokens,
            "stream": True,
            "store": False,
        }
        if request.system_prompt:
            kwargs["instructions"] = request.system_prompt
        if tools:
            kwargs["tools"] = tools
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.thinking:
            kwargs["reasoning"] = {"effort": "medium", "summary": "auto"}

        calls: dict[str, dict[str, str]] = {}
        completion_state = {"has_tool_call": False}
        try:
            sdk_stream = await client.responses.create(**kwargs)
            try:
                async for event in sdk_stream:
                    for mapped_event in self._map_event(event, calls, completion_state):
                        yield mapped_event
            finally:
                close = getattr(sdk_stream, "close", None) or getattr(sdk_stream, "aclose", None)
                if close is not None:
                    await close()
        except Exception as exc:
            retryable = isinstance(exc, APIConnectionError) or (
                isinstance(exc, APIStatusError)
                and (exc.status_code == 429 or exc.status_code >= 500)
            )
            code = None
            if isinstance(exc, APIStatusError) and exc.status_code == 400:
                code = exc.code
                if isinstance(exc.body, dict):
                    detail = exc.body.get("error", exc.body)
                    if isinstance(detail, dict):
                        code = code or detail.get("code")
            yield self._failure(str(exc), code, retryable=retryable)

    @staticmethod
    def _failure(message: str, code: str | None, *, retryable: bool = False) -> StreamFailed:
        normalized = LlmErrorCode.CONTEXT_OVERFLOW if code == "context_length_exceeded" else None
        return StreamFailed(message, retryable=retryable and normalized is None, code=normalized)

    @staticmethod
    def _convert_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, UserMessage):
                items.append({"role": "user", "content": message.content})
                continue
            if isinstance(message, ToolResultMessage):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.call_id,
                        "output": message.content,
                    }
                )
                continue
            assert isinstance(message, AssistantMessage)
            text_parts: list[str] = []
            for block in message.content:
                # Provider reasoning 可持久化供 UI 回放，但不重新注入下一次请求。
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": block.call_id,
                            "name": block.name,
                            "arguments": json.dumps(block.arguments, ensure_ascii=False),
                        }
                    )
            if text_parts:
                items.append({"role": "assistant", "content": "\n".join(text_parts)})
        return items

    @staticmethod
    def _map_event(
        event: Any,
        calls: dict[str, dict[str, str]],
        completion_state: dict[str, bool],
    ) -> list[LlmStreamEvent]:
        event_type = getattr(event, "type", "")
        if event_type == "response.created":
            model = getattr(getattr(event, "response", None), "model", None)
            return [StreamStarted(model=model)]
        if event_type == "response.output_text.delta":
            return [TextDelta(text=event.delta)]
        if event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            return [ReasoningDelta(text=event.delta)]
        if event_type == "response.output_item.added":
            item = event.item
            if getattr(item, "type", "") == "function_call":
                completion_state["has_tool_call"] = True
                calls[item.id] = {"call_id": item.call_id, "name": item.name}
                return [ToolCallStarted(call_id=item.call_id, name=item.name)]
        if event_type == "response.function_call_arguments.delta":
            call = calls.get(event.item_id, {"call_id": event.item_id})
            return [ToolCallArgumentsDelta(call_id=call["call_id"], delta=event.delta)]
        if event_type == "response.function_call_arguments.done":
            call = calls.pop(event.item_id, {"call_id": event.item_id, "name": event.name})
            try:
                arguments = json.loads(event.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_invalid_json": event.arguments}
            return [ToolCallCompleted(ToolUseBlock(call["call_id"], call["name"], arguments))]
        if event_type in {"response.completed", "response.incomplete", "response.failed"}:
            usage = getattr(event.response, "usage", None)
            result: list[LlmStreamEvent] = []
            if usage is not None:
                data = {
                    key: value
                    for key in ("input_tokens", "output_tokens")
                    if (value := getattr(usage, key, None)) is not None
                }
                # 缓存与推理是总输入/输出的子集，仅记录明细，禁止重复累计。
                for details, field, key in (
                    (
                        getattr(usage, "input_tokens_details", None),
                        "cached_tokens",
                        "cache_read_tokens",
                    ),
                    (
                        getattr(usage, "input_tokens_details", None),
                        "cache_write_tokens",
                        "cache_write_tokens",
                    ),
                    (
                        getattr(usage, "output_tokens_details", None),
                        "reasoning_tokens",
                        "reasoning_tokens",
                    ),
                ):
                    value = getattr(details, field, None)
                    if value is not None:
                        data[key] = value
                result.append(UsageUpdated(LlmUsage(**data)))
            if event_type == "response.failed":
                error = getattr(event.response, "error", None)
                result.append(
                    OpenAiLlmAdapter._failure(
                        getattr(error, "message", None) or "OpenAI 响应失败",
                        getattr(error, "code", None),
                    )
                )
                return result
            if event_type == "response.incomplete":
                details = getattr(event.response, "incomplete_details", None)
                detail_reason = getattr(details, "reason", None)
                if detail_reason not in {"max_output_tokens", "max_tokens"}:
                    raise RuntimeError(f"OpenAI 响应未完成：{detail_reason or 'unknown'}")
                stop_reason = LlmStopReason.LENGTH
            elif completion_state.get("has_tool_call", False):
                stop_reason = LlmStopReason.TOOL_USE
            else:
                stop_reason = LlmStopReason.STOP
            model = getattr(event.response, "model", None)
            result.append(MessageCompleted(stop_reason, model=model))
            return result
        if event_type == "error":
            return [
                OpenAiLlmAdapter._failure(
                    getattr(event, "message", None) or "OpenAI 流错误",
                    getattr(event, "code", None),
                )
            ]
        return []
