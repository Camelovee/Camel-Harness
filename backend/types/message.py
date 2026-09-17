from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeAlias


class MessageRole(StrEnum):
    """跨模型与会话边界稳定的规范消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class TextBlock:
    """用户或模型可见的文本内容。"""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningBlock:
    """模型推理摘要；不得假设所有 Provider 都会返回。"""

    text: str


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    """模型请求执行工具的规范消息块。"""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    """工具执行结果在消息中的规范表示。"""

    call_id: str
    name: str
    content: str
    is_error: bool = False


ContentBlock: TypeAlias = TextBlock | ReasoningBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True, slots=True)
class Message:
    """跨 LLM、Agent 与 Session 使用的不可变规范消息。"""

    role: MessageRole
    content: tuple[ContentBlock, ...]

    @classmethod
    def text(cls, role: MessageRole, text: str) -> Message:
        """创建只包含一个文本块的规范消息。"""

        return cls(role=role, content=(TextBlock(text),))
