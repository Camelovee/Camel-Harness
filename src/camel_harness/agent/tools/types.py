from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from camel_harness.llm import LlmModel, ToolDefinition, ToolResultMessage


class ToolRisk(StrEnum):
    """工具风险等级，供能力授权策略决策。"""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    EXTERNAL = "external"


class PermissionMode(StrEnum):
    """一次 Agent 运行采用的工具权限模式。"""

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    YOLO = "yolo"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型提出的一次规范工具调用。"""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具返回给 Agent 的规范结果。"""

    call_id: str
    name: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> ToolResultMessage:
        """投影模型可见的结果并复制附加信息，保存时机仍由 Agent 决定。"""

        return ToolResultMessage(
            call_id=self.call_id,
            name=self.name,
            content=self.content,
            is_error=self.is_error,
            details=dict(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class ToolContext:
    """一次工具执行所需的稳定运行上下文；model 为所属 Run 的模型选择。"""

    session_id: str
    workspace: Path
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    model: LlmModel | None = None


class Tool(ABC):
    """供内置工具、插件和 MCP 共同实现的轻量工具契约。

    具体工具像 pi-agent 一样直接声明名称、描述和参数 schema；运行时仅在
    构造模型请求时投影为 ``ToolDefinition``，避免嵌套定义对象。
    """

    namespace = "builtin"
    name = ""
    description = ""
    parameters: dict[str, Any] = {}
    capabilities: frozenset[str] = frozenset()
    risk = ToolRisk.READ
    timeout_seconds = 30.0

    @property
    def qualified_name(self) -> str:
        """返回模型调用和注册表查找使用的稳定名称。"""

        return self.name if self.namespace == "builtin" else f"{self.namespace}__{self.name}"

    def to_definition(self) -> ToolDefinition:
        """投影为厂商无关的 LLM 工具定义。"""

        return ToolDefinition(
            name=self.qualified_name,
            description=self.description,
            input_schema=self.parameters,
        )

    @abstractmethod
    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        """执行一次已经通过校验和授权的工具调用。"""

        raise NotImplementedError
