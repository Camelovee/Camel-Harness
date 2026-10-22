from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic

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


def create_anthropic_adapter() -> AnthropicLlmAdapter:
    """创建 Anthropic 官方 SDK 适配器；凭据随请求进入，适配器无状态。"""

    return AnthropicLlmAdapter()


def _default_client(api_key: str, base_url: str | None) -> AsyncAnthropic:
    return AsyncAnthropic(api_key=api_key, base_url=base_url, max_retries=0)


class AnthropicLlmAdapter(LlmAdapter):
    """使用 Anthropic 官方异步 SDK 的流式 Provider 适配器。"""

    def __init__(
        self,
        client_factory: Callable[[str, str | None], AsyncAnthropic] = _default_client,
    ) -> None:
        self._client_factory = client_factory

    @property
    def provider_id(self) -> str:
        return "anthropic"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        llm_model = llm_request.model
        if not llm_model.api_key:
            yield StreamFailed("Anthropic 请求缺少 API Key", retryable=False)
            return
        sdk_client = self._client_factory(llm_model.api_key, llm_model.base_url)
        try:
            async for llm_event in self._stream_with_client(sdk_client, llm_request):
                yield llm_event
        finally:
            await sdk_client.close()

    async def _stream_with_client(
        self, sdk_client: AsyncAnthropic, llm_request: LlmRequest
    ) -> AsyncIterator[LlmStreamEvent]:
        messages = self._convert_messages(llm_request.messages)
        tool_definitions = [
            {
                "name": tool_definition.name,
                "description": tool_definition.description,
                "input_schema": tool_definition.input_schema,
            }
            for tool_definition in llm_request.tools
        ]
        request_parameters: dict[str, Any] = {
            "model": llm_request.model.model,
            "max_tokens": llm_request.max_output_tokens,
            "messages": messages,
        }
        if llm_request.system_prompt:
            request_parameters["system"] = llm_request.system_prompt
        if tool_definitions:
            request_parameters["tools"] = tool_definitions
        if llm_request.temperature is not None:
            request_parameters["temperature"] = llm_request.temperature
        if llm_request.model.thinking_mode is LlmThinkingMode.ANTHROPIC_ADAPTIVE:
            request_parameters["thinking"] = {"type": "adaptive", "display": "summarized"}
            request_parameters["output_config"] = {"effort": llm_request.effort.value}

        tool_blocks_by_index: dict[int, dict[str, str]] = {}
        completion: dict[str, LlmStopReason] = {}
        usage_state: dict[str, int] = {}
        try:
            async with sdk_client.messages.stream(**request_parameters) as sdk_stream:
                async for sdk_event in sdk_stream:
                    for mapped_event in self._map_event(
                        sdk_event, tool_blocks_by_index, completion, usage_state
                    ):
                        yield mapped_event
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retryable = isinstance(exc, APIConnectionError) or (
                isinstance(exc, APIStatusError)
                and (exc.status_code == 429 or exc.status_code >= 500)
            )
            code = self._error_code(exc)
            yield StreamFailed(error=str(exc), retryable=retryable and code is None, code=code)

    @staticmethod
    def _error_code(error: Exception) -> LlmErrorCode | None:
        """只识别请求错误正文，避免异常中的请求回显误触发压缩。"""

        # SDK 对 SSE error 使用原 HTTP 200 构造 APIStatusError，正文仍需严格识别。
        if not isinstance(error, APIStatusError) or error.status_code not in {200, 400}:
            return None
        body = error.body
        if not isinstance(body, dict):
            return None
        detail = body.get("error", body)
        if not isinstance(detail, dict):
            return None
        if detail.get("code") == "context_length_exceeded":
            return LlmErrorCode.CONTEXT_OVERFLOW
        message = detail.get("message", "")
        if (
            detail.get("type") == "invalid_request_error"
            and isinstance(message, str)
            and message.lower()
            .replace("`", "")
            .startswith(("prompt is too long", "input length and max_tokens exceed context limit"))
        ):
            return LlmErrorCode.CONTEXT_OVERFLOW
        return None

    @staticmethod
    def _convert_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
        converted_messages: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tool_results() -> None:
            if pending_tool_results:
                converted_messages.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results.clear()

        for message in messages:
            if isinstance(message, ToolResultMessage):
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message.call_id,
                        "content": message.content,
                        "is_error": message.is_error,
                    }
                )
                continue

            flush_tool_results()
            if isinstance(message, UserMessage):
                converted_messages.append(
                    {"role": "user", "content": [{"type": "text", "text": message.content}]}
                )
                continue

            assert isinstance(message, AssistantMessage)
            content: list[dict[str, Any]] = []
            for block in message.content:
                # ReasoningBlock 只用于可观测性和 UI 回放，不能冒充普通对话文本。
                if isinstance(block, TextBlock):
                    content.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": block.call_id,
                            "name": block.name,
                            "input": block.arguments,
                        }
                    )
            if content:
                converted_messages.append({"role": "assistant", "content": content})
        flush_tool_results()
        return converted_messages

    @staticmethod
    def _map_event(
        sdk_event: Any,
        tool_blocks_by_index: dict[int, dict[str, str]],
        completion: dict[str, LlmStopReason],
        usage_state: dict[str, int] | None = None,
    ) -> list[LlmStreamEvent]:
        event_type = getattr(sdk_event, "type", "")
        if event_type == "message_start":
            message = getattr(sdk_event, "message", None)
            usage = getattr(message, "usage", None)
            model = getattr(message, "model", None)
            llm_events: list[LlmStreamEvent] = [StreamStarted(model=model)]
            if usage is not None:
                usage_data = AnthropicLlmAdapter._map_usage(usage, usage_state)
                llm_events.append(UsageUpdated(LlmUsage(**usage_data)))
            return llm_events
        if event_type == "content_block_start":
            block = sdk_event.content_block
            if getattr(block, "type", "") == "tool_use":
                tool_blocks_by_index[sdk_event.index] = {
                    "call_id": block.id,
                    "name": block.name,
                    "arguments": "",
                }
                return [ToolCallStarted(call_id=block.id, name=block.name)]
            return []
        if event_type == "content_block_delta":
            delta = sdk_event.delta
            if getattr(delta, "type", "") == "text_delta":
                return [TextDelta(text=delta.text)]
            if getattr(delta, "type", "") == "thinking_delta":
                return [ReasoningDelta(text=delta.thinking)]
            if getattr(delta, "type", "") == "input_json_delta":
                block = tool_blocks_by_index[sdk_event.index]
                block["arguments"] += delta.partial_json
                return [ToolCallArgumentsDelta(call_id=block["call_id"], delta=delta.partial_json)]
        if event_type == "content_block_stop" and sdk_event.index in tool_blocks_by_index:
            block = tool_blocks_by_index.pop(sdk_event.index)
            arguments_json = block["arguments"] or "{}"
            try:
                arguments = json.loads(arguments_json)
            except json.JSONDecodeError:
                arguments = {"_invalid_json": arguments_json}
            return [ToolCallCompleted(ToolUseBlock(block["call_id"], block["name"], arguments))]
        if event_type == "message_delta":
            usage = getattr(sdk_event, "usage", None)
            usage_data = AnthropicLlmAdapter._map_usage(usage, usage_state) if usage else {}
            reason = getattr(getattr(sdk_event, "delta", None), "stop_reason", None)
            if reason:
                completion["stop_reason"] = AnthropicLlmAdapter._map_stop_reason(reason)
            return [UsageUpdated(LlmUsage(**usage_data))]
        if event_type == "message_stop":
            reason = completion.get("stop_reason")
            if reason is None:
                raise RuntimeError("Anthropic 响应缺少 stop_reason")
            return [MessageCompleted(reason, model=None)]
        return []

    @staticmethod
    def _map_usage(usage: Any, state: dict[str, int] | None) -> dict[str, Any]:
        """合并 SDK 的累计原生用量，再统一缓存口径；缺失字段不覆盖已知统计。"""
        snapshot = state if state is not None else {}
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = getattr(usage, field, None)
            if value is not None:
                snapshot[field] = value
        thinking = getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", None)
        if thinking is not None:
            snapshot["reasoning_tokens"] = thinking
        usage_data: dict[str, Any] = {}
        for field, key in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_read_input_tokens", "cache_read_tokens"),
            ("cache_creation_input_tokens", "cache_write_tokens"),
            ("reasoning_tokens", "reasoning_tokens"),
        ):
            if field in snapshot:
                usage_data[key] = snapshot[field]
        # Anthropic 原生输入不含缓存，必须在原生快照上求和，不能反复累加规范输入。
        if "input_tokens" in usage_data:
            usage_data["input_tokens"] += usage_data.get("cache_read_tokens", 0) + usage_data.get(
                "cache_write_tokens", 0
            )
        return usage_data

    @staticmethod
    def _map_stop_reason(reason: str) -> LlmStopReason:
        """把 Anthropic 结束原因收敛为厂商无关语义。"""

        if reason in {"end_turn", "stop_sequence", "pause_turn"}:
            return LlmStopReason.STOP
        if reason == "max_tokens":
            return LlmStopReason.LENGTH
        if reason == "tool_use":
            return LlmStopReason.TOOL_USE
        if reason in {"refusal", "sensitive"}:
            raise RuntimeError(f"Anthropic 拒绝生成：{reason}")
        raise RuntimeError(f"Anthropic 返回未知 stop_reason：{reason}")
