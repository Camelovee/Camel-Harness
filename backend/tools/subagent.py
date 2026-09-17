from __future__ import annotations

from backend.agent import SubagentRequest, SubagentRunner, SubagentType
from backend.types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk

from .provider import ToolProvider


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
        result = await self._runner.run(
            SubagentRequest(
                parent_session_id=context.session_id,
                task=str(call.arguments["task"]),
                agent_type=SubagentType(str(call.arguments["agent_type"])),
                workspace=context.workspace,
                permission_mode=context.permission_mode,
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
    """提供子 Agent 委派工具。"""

    def __init__(self, runner: SubagentRunner) -> None:
        self._runner = runner

    async def discover(self) -> tuple[Tool, ...]:
        return (DelegateTaskTool(self._runner),)
