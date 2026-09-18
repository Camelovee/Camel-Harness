from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

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


def create_openai_adapter(api_key: str, base_url: str | None = None) -> OpenAiLlmAdapter:
    """在 LLM 边界内创建 OpenAI 官方 SDK 适配器。"""

    return OpenAiLlmAdapter(AsyncOpenAI(api_key=api_key, base_url=base_url))


class OpenAiLlmAdapter(LlmAdapter):
    """使用 OpenAI 官方异步 SDK Responses API 的流式适配器。"""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    @property
    def provider_id(self) -> str:
        return "openai"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
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
            "model": request.model,
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
            sdk_stream = await self._client.responses.create(**kwargs)
            async for event in sdk_stream:
                for event_type, data in self._map_event(event, calls, completion_state):
                    yield LlmStreamEvent(event_type, data)
        except Exception as exc:
            yield LlmStreamEvent(LlmStreamEventType.STREAM_FAILED, {"error": str(exc)})
            raise

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
    ) -> list[tuple[LlmStreamEventType, dict[str, Any]]]:
        event_type = getattr(event, "type", "")
        if event_type == "response.created":
            return [(LlmStreamEventType.STREAM_STARTED, {})]
        if event_type == "response.output_text.delta":
            return [(LlmStreamEventType.TEXT_DELTA, {"text": event.delta})]
        if event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            return [(LlmStreamEventType.REASONING_DELTA, {"text": event.delta})]
        if event_type == "response.output_item.added":
            item = event.item
            if getattr(item, "type", "") == "function_call":
                completion_state["has_tool_call"] = True
                calls[item.id] = {"call_id": item.call_id, "name": item.name}
                return [
                    (
                        LlmStreamEventType.TOOL_CALL_STARTED,
                        {"call_id": item.call_id, "name": item.name, "item_id": item.id},
                    )
                ]
        if event_type == "response.function_call_arguments.delta":
            call = calls.get(event.item_id, {"call_id": event.item_id})
            return [
                (
                    LlmStreamEventType.TOOL_CALL_ARGUMENTS_DELTA,
                    {"call_id": call["call_id"], "delta": event.delta},
                )
            ]
        if event_type == "response.function_call_arguments.done":
            call = calls.pop(event.item_id, {"call_id": event.item_id, "name": event.name})
            try:
                arguments = json.loads(event.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_invalid_json": event.arguments}
            return [
                (
                    LlmStreamEventType.TOOL_CALL_COMPLETED,
                    {"call_id": call["call_id"], "name": call["name"], "arguments": arguments},
                )
            ]
        if event_type in {"response.completed", "response.incomplete"}:
            usage = getattr(event.response, "usage", None)
            result: list[tuple[LlmStreamEventType, dict[str, Any]]] = []
            if usage is not None:
                result.append(
                    (
                        LlmStreamEventType.USAGE_UPDATED,
                        {
                            "input_tokens": getattr(usage, "input_tokens", 0),
                            "output_tokens": getattr(usage, "output_tokens", 0),
                        },
                    )
                )
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
            result.append(
                (LlmStreamEventType.MESSAGE_COMPLETED, {"stop_reason": stop_reason.value})
            )
            return result
        return []
