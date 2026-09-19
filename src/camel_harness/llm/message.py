from __future__ import annotations

from dataclasses import dataclass, field
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
class AssistantMessage:
    """模型返回的文本、推理摘要和工具调用。"""

    content: tuple[AssistantContent, ...]
    stop_reason: LlmStopReason
    role: Literal["assistant"] = field(default="assistant", init=False)


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    """一次工具调用的结果消息。"""

    call_id: str
    name: str
    content: str
    is_error: bool = False
    role: Literal["toolResult"] = field(default="toolResult", init=False)


Message: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage
