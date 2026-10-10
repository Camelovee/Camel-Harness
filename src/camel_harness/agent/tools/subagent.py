from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .registry import ToolProvider
from .types import (
    PermissionMode,
    Tool,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolRisk,
)


class SubagentType(StrEnum):
    """内置子 Agent 的最小任务预设。"""

    EXPLORE = "explore"
    PLAN = "plan"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class SubagentRequest:
    """父 Agent 发起的一次隔离委派。"""

    task: str
    agent_type: SubagentType
    workspace: Path
    permission_mode: PermissionMode
    parent_session_id: str
    parent_tool_call_id: str


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """被委派 Agent 回传给父 Agent 的最终文本。"""

    session_id: str
    content: str
    succeeded: bool


SubagentRunner = Callable[[SubagentRequest], Awaitable[SubagentResult]]


class DelegateTaskTool(Tool):
    """把边界清晰的任务委派给隔离子 Agent。"""

    name = "delegate_task"
    description = "委派独立探索、规划或通用任务给隔离子 Agent。"
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "minLength": 1},
            "agent_type": {"type": "string", "enum": ["explore", "plan", "general"]},
        },
        "required": ["task", "agent_type"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"agent.delegate", "llm.invoke"})
    risk = ToolRisk.EXTERNAL
    timeout_seconds = 300

    def __init__(self, runner: SubagentRunner) -> None:
        self._runner = runner

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        result = await self._runner(
            SubagentRequest(
                task=str(call.arguments["task"]),
                agent_type=SubagentType(str(call.arguments["agent_type"])),
                workspace=context.workspace,
                permission_mode=context.permission_mode,
                parent_session_id=context.session_id,
                parent_tool_call_id=call.call_id,
            )
        )
        return ToolResult(
            call.call_id,
            call.name,
            result.content,
            is_error=not result.succeeded,
            metadata={"subagent_session_id": result.session_id},
        )


class SubagentToolProvider(ToolProvider):
    """提供唯一的子 Agent 委派工具。"""

    def __init__(self, runner: SubagentRunner) -> None:
        self._runner = runner

    async def discover(self) -> tuple[Tool, ...]:
        return (DelegateTaskTool(self._runner),)
