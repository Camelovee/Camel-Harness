from __future__ import annotations

from ..tools.registry import ToolProvider
from ..tools.types import (
    Tool,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolRisk,
)
from .runner import SubagentRequest, SubagentRunner, SubagentType


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

    def __init__(self, subagent_runner: SubagentRunner) -> None:
        self._subagent_runner = subagent_runner

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        if tool_context.model is None:
            return ToolResult(
                tool_call.call_id, tool_call.name, "委派缺少模型上下文", is_error=True
            )
        subagent_result = await self._subagent_runner(
            SubagentRequest(
                task=str(tool_call.arguments["task"]),
                agent_type=SubagentType(str(tool_call.arguments["agent_type"])),
                workspace=tool_context.workspace,
                permission_mode=tool_context.permission_mode,
                parent_session_id=tool_context.session_id,
                parent_tool_call_id=tool_call.call_id,
                model=tool_context.model,
            )
        )
        return ToolResult(
            tool_call.call_id,
            tool_call.name,
            subagent_result.content,
            is_error=not subagent_result.succeeded,
            metadata={"subagent_session_id": subagent_result.session_id},
        )


class SubagentToolProvider(ToolProvider):
    """提供唯一的子 Agent 委派工具。"""

    def __init__(self, subagent_runner: SubagentRunner) -> None:
        self._subagent_runner = subagent_runner

    async def discover(self) -> tuple[Tool, ...]:
        return (DelegateTaskTool(self._subagent_runner),)
