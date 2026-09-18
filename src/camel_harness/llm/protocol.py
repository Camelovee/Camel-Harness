from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .message import Message


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """发送给模型的厂商无关工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """不包含 Agent 运行坐标的单次模型请求。"""

    model: str
    messages: tuple[Message, ...]
    system_prompt: str | None = None
    tools: tuple[ToolDefinition, ...] = ()
    max_output_tokens: int = 4096
    temperature: float | None = None
    thinking: bool = False


class LlmStreamEventType(StrEnum):
    """所有 Provider 必须映射到的规范流式事件。"""

    STREAM_STARTED = "stream_started"
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_ARGUMENTS_DELTA = "tool_call_arguments_delta"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    USAGE_UPDATED = "usage_updated"
    MESSAGE_COMPLETED = "message_completed"
    STREAM_FAILED = "stream_failed"
    STREAM_CANCELLED = "stream_cancelled"


@dataclass(frozen=True, slots=True)
class LlmStreamEvent:
    """Provider 产生的规范模型增量，不携带 Agent 运行坐标。"""

    type: LlmStreamEventType
    data: dict[str, Any] = field(default_factory=dict)


class LlmAdapter(ABC):
    """厂商无关的 LLM 流式协议。"""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """返回当前适配器服务的 Provider 标识。"""

        raise NotImplementedError

    @abstractmethod
    def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """把规范请求转换为厂商调用，并返回规范流式事件。"""

        raise NotImplementedError
