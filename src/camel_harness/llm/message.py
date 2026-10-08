from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True, slots=True)
class TextBlock:
    """模型回答中的可见文本。"""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningBlock:
    """模型推理摘要；不得假设所有 Provider 都会返回。"""

    text: str


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    """模型请求执行工具的规范内容块。"""

    call_id: str
    name: str
    arguments: dict[str, Any]


AssistantContent: TypeAlias = TextBlock | ReasoningBlock | ToolUseBlock


class LlmStopReason(StrEnum):
    """单次模型响应的规范结束原因，不表示 Agent Run 的终态。"""

    STOP = "stop"
    LENGTH = "length"
    TOOL_USE = "tool_use"


@dataclass(frozen=True, slots=True)
class UserMessage:
    """用户提供给模型的文本消息。"""

    content: str
    role: Literal["user"] = field(default="user", init=False)


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """单次响应的 Token 统计；缓存属于输入、推理属于输出，不重复计入总量。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{item.name} 必须是非负整数或未知值")

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    def merge(self, snapshot: LlmUsage) -> LlmUsage:
        """合并累计快照，不累加重复值，也不以未知覆盖已知字段。"""
        values: dict[str, int | None] = {}
        for item in fields(self):
            previous = getattr(self, item.name)
            current = getattr(snapshot, item.name)
            values[item.name] = (
                previous
                if current is None
                else current
                if previous is None
                else max(previous, current)
            )
        return LlmUsage(**values)


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """模型返回的文本、推理摘要和工具调用。"""

    content: tuple[AssistantContent, ...]
    stop_reason: LlmStopReason
    provider: str | None = None
    model: str | None = None
    response_model: str | None = None
    usage: LlmUsage | None = None
    role: Literal["assistant"] = field(default="assistant", init=False)


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """一次工具调用的结果消息。"""

    call_id: str
    name: str
    content: str
    is_error: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    role: Literal["toolResult"] = field(default="toolResult", init=False)


Message: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage
