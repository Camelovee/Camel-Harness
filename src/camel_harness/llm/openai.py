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
    LlmThinkingMode,
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
    # 重试统一由 LlmClient 负责，SDK 自身关闭重试避免双层放大。
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

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        llm_model = llm_request.model
        if not llm_model.api_key:
            yield StreamFailed("OpenAI 请求缺少 API Key", retryable=False)
            return
        # 每次调用新建并关闭 client：适配器无状态，凭据随请求进入，不做连接复用。
        sdk_client = self._client_factory(llm_model.api_key, llm_model.base_url)
        try:
            async for llm_event in self._stream_with_client(sdk_client, llm_request):
                yield llm_event
        finally:
            await sdk_client.close()

    async def _stream_with_client(
        self, sdk_client: AsyncOpenAI, llm_request: LlmRequest
    ) -> AsyncIterator[LlmStreamEvent]:
        input_items = self._convert_messages(llm_request.messages)
        tool_definitions = [
            {
                "type": "function",
                "name": tool_definition.name,
                "description": tool_definition.description,
                "parameters": tool_definition.input_schema,
                # 工具 schema 不满足严格模式要求，关闭 strict 避免请求被拒。
                "strict": False,
            }
            for tool_definition in llm_request.tools
        ]
        request_parameters: dict[str, Any] = {
            "model": llm_request.model.model,
            "input": input_items,
            "max_output_tokens": llm_request.max_output_tokens,
            "stream": True,
            # 不在服务端留存响应，避免会话内容落到第三方存储。
            "store": False,
        }
        if llm_request.system_prompt:
            request_parameters["instructions"] = llm_request.system_prompt
        if tool_definitions:
            request_parameters["tools"] = tool_definitions
        if llm_request.temperature is not None:
            request_parameters["temperature"] = llm_request.temperature
        if llm_request.model.thinking_mode is LlmThinkingMode.OPENAI_REASONING:
            # OpenAI 没有 max 档，MAX 映射为 xhigh。
            effort = {
                "low": "low",
                "high": "high",
                "max": "xhigh",
            }[llm_request.effort.value]
            request_parameters["reasoning"] = {"effort": effort, "summary": "auto"}

        # item_id 与 call_id 不同，参数增量到达时需要这层映射。
        tool_calls_by_item_id: dict[str, dict[str, str]] = {}
        completion_state = {"has_tool_call": False}
        try:
            sdk_stream = await sdk_client.responses.create(**request_parameters)
            try:
                async for sdk_event in sdk_stream:
                    for mapped_event in self._map_event(
                        sdk_event, tool_calls_by_item_id, completion_state
                    ):
                        yield mapped_event
            finally:
                # SDK 不同版本分别提供 close/aclose，两者都尝试。
                close = getattr(sdk_stream, "close", None) or getattr(sdk_stream, "aclose", None)
                if close is not None:
                    await close()
        except Exception as exc:
            retryable = isinstance(exc, APIConnectionError) or (
                isinstance(exc, APIStatusError)
                and (exc.status_code == 429 or exc.status_code >= 500)
            )
            code = None
            # 只有 400 才提取错误码；其它状态不据此判断可恢复性。
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
        sdk_event: Any,
        tool_calls_by_item_id: dict[str, dict[str, str]],
        completion_state: dict[str, bool],
    ) -> list[LlmStreamEvent]:
        event_type = getattr(sdk_event, "type", "")
        if event_type == "response.created":
            model = getattr(getattr(sdk_event, "response", None), "model", None)
            return [StreamStarted(model=model)]
        if event_type == "response.output_text.delta":
            return [TextDelta(text=sdk_event.delta)]
        if event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            return [ReasoningDelta(text=sdk_event.delta)]
        if event_type == "response.output_item.added":
            item = sdk_event.item
            if getattr(item, "type", "") == "function_call":
                completion_state["has_tool_call"] = True
                tool_calls_by_item_id[item.id] = {"call_id": item.call_id, "name": item.name}
                return [ToolCallStarted(call_id=item.call_id, name=item.name)]
        if event_type == "response.function_call_arguments.delta":
            # delta 可能早于 item.added，先按 item_id 兜底，不丢弃参数片段。
            tool_call_data = tool_calls_by_item_id.get(
                sdk_event.item_id, {"call_id": sdk_event.item_id}
            )
            return [
                ToolCallArgumentsDelta(call_id=tool_call_data["call_id"], delta=sdk_event.delta)
            ]
        if event_type == "response.function_call_arguments.done":
            tool_call_data = tool_calls_by_item_id.pop(
                sdk_event.item_id, {"call_id": sdk_event.item_id, "name": sdk_event.name}
            )
            try:
                arguments = json.loads(sdk_event.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_invalid_json": sdk_event.arguments}
            return [
                ToolCallCompleted(
                    ToolUseBlock(tool_call_data["call_id"], tool_call_data["name"], arguments)
                )
            ]
        if event_type in {"response.completed", "response.incomplete", "response.failed"}:
            usage = getattr(sdk_event.response, "usage", None)
            llm_events: list[LlmStreamEvent] = []
            if usage is not None:
                usage_data = {
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
                        usage_data[key] = value
                llm_events.append(UsageUpdated(LlmUsage(**usage_data)))
            if event_type == "response.failed":
                # 先发 UsageUpdated 再发失败：失败响应也可能已经消耗用量。
                error = getattr(sdk_event.response, "error", None)
                llm_events.append(
                    OpenAiLlmAdapter._failure(
                        getattr(error, "message", None) or "OpenAI 响应失败",
                        getattr(error, "code", None),
                    )
                )
                return llm_events
            if event_type == "response.incomplete":
                details = getattr(sdk_event.response, "incomplete_details", None)
                detail_reason = getattr(details, "reason", None)
                if detail_reason not in {"max_output_tokens", "max_tokens"}:
                    raise RuntimeError(f"OpenAI 响应未完成：{detail_reason or 'unknown'}")
                stop_reason = LlmStopReason.LENGTH
            elif completion_state.get("has_tool_call", False):
                stop_reason = LlmStopReason.TOOL_USE
            else:
                stop_reason = LlmStopReason.STOP
            model = getattr(sdk_event.response, "model", None)
            llm_events.append(MessageCompleted(stop_reason, model=model))
            return llm_events
        if event_type == "error":
            return [
                OpenAiLlmAdapter._failure(
                    getattr(sdk_event, "message", None) or "OpenAI 流错误",
                    getattr(sdk_event, "code", None),
                )
            ]
        return []
