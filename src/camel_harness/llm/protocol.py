from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, TypeAlias

from .message import LlmStopReason, LlmUsage, Message, ToolUseBlock


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


@dataclass(frozen=True, slots=True)
class StreamStarted:
    """模型流已开始，可包含厂商报告的实际模型。"""

    model: str | None = None
    type: Literal["stream_started"] = field(default="stream_started", init=False)


@dataclass(frozen=True, slots=True)
class TextDelta:
    """可见回答的文本增量。"""

    text: str
    type: Literal["text_delta"] = field(default="text_delta", init=False)


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """仅用于观测的推理文本增量。"""

    text: str
    type: Literal["reasoning_delta"] = field(default="reasoning_delta", init=False)


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """工具调用开始；厂商索引留在适配器内部。"""

    call_id: str
    name: str
    type: Literal["tool_call_started"] = field(default="tool_call_started", init=False)


@dataclass(frozen=True, slots=True)
class ToolCallArgumentsDelta:
    """工具参数的 JSON 文本片段。"""

    call_id: str
    delta: str
    type: Literal["tool_call_arguments_delta"] = field(
        default="tool_call_arguments_delta", init=False
    )


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    """完整工具调用只复用 LLM 内容块，不反向依赖 Agent 工具类型。"""

    tool_call: ToolUseBlock
    type: Literal["tool_call_completed"] = field(default="tool_call_completed", init=False)


@dataclass(frozen=True, slots=True)
class UsageUpdated:
    """单次响应的累计用量快照，未报告字段仍为未知。"""

    usage: LlmUsage
    type: Literal["usage_updated"] = field(default="usage_updated", init=False)


@dataclass(frozen=True, slots=True)
class MessageCompleted:
    """完整响应的结束原因；本事件不提前代表会话消息已保存。"""

    stop_reason: LlmStopReason
    model: str | None = None
    type: Literal["message_completed"] = field(default="message_completed", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.stop_reason, LlmStopReason):
            raise ValueError("响应结束原因必须使用 LlmStopReason")


@dataclass(frozen=True, slots=True)
class StreamFailed:
    """模型流错误；运行终态仍由 Agent 统一表达。"""

    error: str
    retryable: bool = False
    type: Literal["stream_failed"] = field(default="stream_failed", init=False)


LlmStreamEvent: TypeAlias = (
    StreamStarted
    | TextDelta
    | ReasoningDelta
    | ToolCallStarted
    | ToolCallArgumentsDelta
    | ToolCallCompleted
    | UsageUpdated
    | MessageCompleted
    | StreamFailed
)


class LlmAdapter(ABC):
    """厂商无关的 LLM 流式协议。"""

    async def close(self) -> None:
        """释放适配器拥有的连接；无外部资源的测试实现无需覆盖。"""
        return None

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """返回当前适配器服务的 Provider 标识。"""

        raise NotImplementedError

    @abstractmethod
    def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """把规范请求转换为厂商调用，并返回规范流式事件。"""

        raise NotImplementedError
