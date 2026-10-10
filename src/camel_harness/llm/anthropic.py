from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
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


def create_anthropic_adapter(api_key: str, base_url: str | None = None) -> AnthropicLlmAdapter:
    """在 LLM 边界内创建 Anthropic 官方 SDK 适配器。"""

    return AnthropicLlmAdapter(AsyncAnthropic(api_key=api_key, base_url=base_url, max_retries=0))


class AnthropicLlmAdapter(LlmAdapter):
    """使用 Anthropic 官方异步 SDK 的流式 Provider 适配器。"""

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    @property
    def provider_id(self) -> str:
        return "anthropic"

    async def close(self) -> None:
        await self._client.close()

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        messages = self._convert_messages(request.messages)
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in request.tools
        ]
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "messages": messages,
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        if tools:
            kwargs["tools"] = tools
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.thinking:
            if request.max_output_tokens <= 1024:
                raise ValueError("启用思考模式时 max_output_tokens 必须大于 1024")
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": min(
                    request.max_output_tokens - 1,
                    max(1024, request.max_output_tokens // 2),
                ),
            }

        tool_blocks: dict[int, dict[str, str]] = {}
        completion: dict[str, LlmStopReason] = {}
        usage_state: dict[str, int] = {}
        try:
            async with self._client.messages.stream(**kwargs) as sdk_stream:
                async for event in sdk_stream:
                    for mapped_event in self._map_event(
                        event, tool_blocks, completion, usage_state
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
        result: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tool_results() -> None:
            if pending_tool_results:
                result.append({"role": "user", "content": list(pending_tool_results)})
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
                result.append(
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
                result.append({"role": "assistant", "content": content})
        flush_tool_results()
        return result

    @staticmethod
    def _map_event(
        event: Any,
        tool_blocks: dict[int, dict[str, str]],
        completion: dict[str, LlmStopReason],
        usage_state: dict[str, int] | None = None,
    ) -> list[LlmStreamEvent]:
        event_type = getattr(event, "type", "")
        if event_type == "message_start":
            message = getattr(event, "message", None)
            usage = getattr(message, "usage", None)
            model = getattr(message, "model", None)
            result: list[LlmStreamEvent] = [StreamStarted(model=model)]
            if usage is not None:
                data = AnthropicLlmAdapter._map_usage(usage, usage_state)
                result.append(UsageUpdated(LlmUsage(**data)))
            return result
        if event_type == "content_block_start":
            block = event.content_block
            if getattr(block, "type", "") == "tool_use":
                tool_blocks[event.index] = {
                    "call_id": block.id,
                    "name": block.name,
                    "arguments": "",
                }
                return [ToolCallStarted(call_id=block.id, name=block.name)]
            return []
        if event_type == "content_block_delta":
            delta = event.delta
            if getattr(delta, "type", "") == "text_delta":
                return [TextDelta(text=delta.text)]
            if getattr(delta, "type", "") == "thinking_delta":
                return [ReasoningDelta(text=delta.thinking)]
            if getattr(delta, "type", "") == "input_json_delta":
                block = tool_blocks[event.index]
                block["arguments"] += delta.partial_json
                return [ToolCallArgumentsDelta(call_id=block["call_id"], delta=delta.partial_json)]
        if event_type == "content_block_stop" and event.index in tool_blocks:
            block = tool_blocks.pop(event.index)
            raw = block["arguments"] or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                arguments = {"_invalid_json": raw}
            return [ToolCallCompleted(ToolUseBlock(block["call_id"], block["name"], arguments))]
        if event_type == "message_delta":
            usage = getattr(event, "usage", None)
            data = AnthropicLlmAdapter._map_usage(usage, usage_state) if usage else {}
            reason = getattr(getattr(event, "delta", None), "stop_reason", None)
            if reason:
                completion["stop_reason"] = AnthropicLlmAdapter._map_stop_reason(reason)
            return [UsageUpdated(LlmUsage(**data))]
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
        data: dict[str, Any] = {}
        for field, key in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_read_input_tokens", "cache_read_tokens"),
            ("cache_creation_input_tokens", "cache_write_tokens"),
            ("reasoning_tokens", "reasoning_tokens"),
        ):
            if field in snapshot:
                data[key] = snapshot[field]
        # Anthropic 原生输入不含缓存，必须在原生快照上求和，不能反复累加规范输入。
        if "input_tokens" in data:
            data["input_tokens"] += data.get("cache_read_tokens", 0) + data.get(
                "cache_write_tokens", 0
            )
        return data

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
