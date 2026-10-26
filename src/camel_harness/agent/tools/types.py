from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from camel_harness.llm import LlmModel, ToolDefinition, ToolResultMessage


class ToolRisk(StrEnum):
    """工具风险等级，供能力授权策略决策。"""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    EXTERNAL = "external"


class ToolExecutionStatus(StrEnum):
    """描述已知执行结果；失败不代表没有部分副作用。"""

    NOT_EXECUTED = "not_executed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


UNKNOWN_EXECUTION_NOTICE = (
    "执行结果未知：操作可能已经产生副作用。请先通过只读方式核验实际状态，"
    "再决定是否重试，不要直接重复原操作。\n"
)


class ToolNotExecutedError(ValueError):
    """明确在实际操作前拒绝，可安全报告未执行。"""


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

    def with_execution_status(self, status: ToolExecutionStatus) -> ToolResult:
        """同时提供结构化状态和模型可见的未知结果说明。"""

        content = self.content
        if status is ToolExecutionStatus.UNKNOWN and not content.startswith(
            UNKNOWN_EXECUTION_NOTICE
        ):
            content = UNKNOWN_EXECUTION_NOTICE + content
        return replace(
            self,
            content=content,
            # is_error 把三种非成功状态压平；具体是未执行、失败还是未知只能看 metadata。
            is_error=status is not ToolExecutionStatus.SUCCEEDED,
            metadata={**self.metadata, "execution_status": status.value},
        )

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


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    """绑定已校验参数与执行入口，审批后不再重新解析参数。"""

    arguments: dict[str, Any]
    execution_context: dict[str, str]
    execute: Callable[[], Awaitable[ToolResult]]


class Tool(ABC):
    """供内置工具、插件和 MCP 共同实现的轻量工具契约。

    原始 Schema 工具按 JSON Schema 校验；本地工具通过 PydanticTool 声明参数模型。
    两者均在审批前绑定参数，执行器只负责授权、超时与结果归一化。
    """

    namespace = "builtin"
    name = ""
    description = ""
    capabilities: frozenset[str] = frozenset()
    risk = ToolRisk.READ
    timeout_seconds = 30.0

    @property
    def parameters(self) -> dict[str, Any]:
        """返回模型可见的 JSON Schema。"""

        return {}

    @property
    def qualified_name(self) -> str:
        """返回模型调用和注册表查找使用的稳定名称。"""

        # builtin 不加前缀以保持模型可见名兼容；MCP 等命名空间用双下划线防冲突。
        return self.name if self.namespace == "builtin" else f"{self.namespace}__{self.name}"

    def to_definition(self) -> ToolDefinition:
        """投影为厂商无关的 LLM 工具定义。"""

        return ToolDefinition(
            name=self.qualified_name,
            description=self.description,
            input_schema=self.parameters,
        )

    def approval_context(self, tool_call: ToolCall, tool_context: ToolContext) -> dict[str, str]:
        """返回由实现生成的审批展示信息，不执行操作。"""

        return {}

    def prepare_call(self, tool_call: ToolCall, tool_context: ToolContext) -> PreparedToolCall:
        """按外部 Schema 校验并绑定一次调用，不产生副作用。"""

        errors = Draft202012Validator(self.parameters).iter_errors(tool_call.arguments)
        messages = tuple(
            error.message
            for error in sorted(errors, key=lambda error: tuple(str(part) for part in error.path))
        )
        if messages:
            raise ToolNotExecutedError("工具参数校验失败：" + "；".join(messages))
        return PreparedToolCall(
            arguments=tool_call.arguments,
            execution_context=self.approval_context(tool_call, tool_context),
            execute=partial(self.execute, tool_call, tool_context),
        )

    @abstractmethod
    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        """执行一次已经通过校验和授权的工具调用。"""

        raise NotImplementedError
