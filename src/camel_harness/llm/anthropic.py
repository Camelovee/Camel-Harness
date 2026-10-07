from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

from .message import (
    AssistantMessage,
    LlmStopReason,
    Message,
    TextBlock,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)
from .protocol import LlmAdapter, LlmRequest, LlmStreamEvent, LlmStreamEventType


def create_anthropic_adapter(api_key: str, base_url: str | None = None) -> AnthropicLlmAdapter:
    """在 LLM 边界内创建 Anthropic 官方 SDK 适配器。"""

    return AnthropicLlmAdapter(AsyncAnthropic(api_key=api_key, base_url=base_url))


class AnthropicLlmAdapter(LlmAdapter):
    """使用 Anthropic 官方异步 SDK 的流式 Provider 适配器。"""

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    @property
    def provider_id(self) -> str:
        return "anthropic"

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
        try:
            async with self._client.messages.stream(**kwargs) as sdk_stream:
                async for event in sdk_stream:
                    for event_type, data in self._map_event(event, tool_blocks, completion):
                        yield LlmStreamEvent(event_type, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield LlmStreamEvent(LlmStreamEventType.STREAM_FAILED, {"error": str(exc)})
            raise

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
    ) -> list[tuple[LlmStreamEventType, dict[str, Any]]]:
        event_type = getattr(event, "type", "")
        if event_type == "message_start":
            usage = getattr(getattr(event, "message", None), "usage", None)
            result: list[tuple[LlmStreamEventType, dict[str, Any]]] = [
                (LlmStreamEventType.STREAM_STARTED, {})
            ]
            if usage is not None:
                result.append(
                    (LlmStreamEventType.USAGE_UPDATED, {"input_tokens": usage.input_tokens})
                )
            return result
        if event_type == "content_block_start":
            block = event.content_block
            if getattr(block, "type", "") == "tool_use":
                tool_blocks[event.index] = {
                    "call_id": block.id,
                    "name": block.name,
                    "arguments": "",
                }
                return [
                    (
                        LlmStreamEventType.TOOL_CALL_STARTED,
                        {"call_id": block.id, "name": block.name, "index": event.index},
                    )
                ]
            return []
        if event_type == "content_block_delta":
            delta = event.delta
            if getattr(delta, "type", "") == "text_delta":
                return [(LlmStreamEventType.TEXT_DELTA, {"text": delta.text})]
            if getattr(delta, "type", "") == "thinking_delta":
                return [(LlmStreamEventType.REASONING_DELTA, {"text": delta.thinking})]
            if getattr(delta, "type", "") == "input_json_delta":
                block = tool_blocks[event.index]
                block["arguments"] += delta.partial_json
                return [
                    (
                        LlmStreamEventType.TOOL_CALL_ARGUMENTS_DELTA,
                        {"call_id": block["call_id"], "delta": delta.partial_json},
                    )
                ]
        if event_type == "content_block_stop" and event.index in tool_blocks:
            block = tool_blocks.pop(event.index)
            raw = block["arguments"] or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                arguments = {"_invalid_json": raw}
            return [
                (
                    LlmStreamEventType.TOOL_CALL_COMPLETED,
                    {"call_id": block["call_id"], "name": block["name"], "arguments": arguments},
                )
            ]
        if event_type == "message_delta":
            usage = getattr(event, "usage", None)
            data = {"output_tokens": getattr(usage, "output_tokens", 0)} if usage else {}
            reason = getattr(getattr(event, "delta", None), "stop_reason", None)
            if reason:
                completion["stop_reason"] = AnthropicLlmAdapter._map_stop_reason(reason)
            return [(LlmStreamEventType.USAGE_UPDATED, data)]
        if event_type == "message_stop":
            reason = completion.get("stop_reason")
            if reason is None:
                raise RuntimeError("Anthropic 响应缺少 stop_reason")
            return [(LlmStreamEventType.MESSAGE_COMPLETED, {"stop_reason": reason.value})]
        return []

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
