from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from camel_harness.llm import AssistantMessage, LlmClient, TextBlock

from ..agent import Agent
from ..context import ContextManager
from ..events import AgentEvent
from ..session import SessionManager
from ..state import AgentOptions
from .executor import ToolExecutor
from .file_result_storage import FileToolResultStorage
from .permissions import PermissionService
from .registry import ToolProvider, ToolRegistry
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

_READ_ONLY_TOOLS = frozenset({"read_file", "load_skill"})
_GENERAL_TOOLS = _READ_ONLY_TOOLS | frozenset({"write_file", "edit_file", "run_shell"})


class LocalSubagentRunner:
    """复用 Agent 执行隔离任务，但不向子 Agent 暴露二次委派能力。"""

    def __init__(
        self,
        llm: LlmClient,
        sessions: SessionManager,
        registry: ToolRegistry,
        permission: PermissionService,
        context: ContextManager,
        options: AgentOptions,
    ) -> None:
        self._llm = llm
        self._sessions = sessions
        self._registry = registry
        self._permission = permission
        self._context = context
        self._options = options

    async def run(self, request: SubagentRequest) -> SubagentResult:
        allowed = _GENERAL_TOOLS if request.agent_type is SubagentType.GENERAL else _READ_ONLY_TOOLS
        registry = self._registry.subset(allowed)
        executor = ToolExecutor(
            registry,
            self._permission,
            FileToolResultStorage(self._sessions.directory).normalize,
        )
        options = replace(self._options, max_turns=8)
        session_id = f"sub-{uuid4().hex}"
        mode = (
            request.permission_mode
            if request.agent_type is SubagentType.GENERAL
            else PermissionMode.PLAN
        )
        prompt = f"你是 {request.agent_type.value} 子 Agent。只完成被委派任务并返回精炼结果。"
        await self._sessions.create(
            session_id,
            request.workspace,
            "子 Agent",
            parent_session_id=request.parent_session_id,
            parent_tool_call_id=request.parent_tool_call_id,
        )
        agent = Agent(
            self._sessions.bind(session_id),
            request.workspace,
            options,
            self._llm,
            registry,
            executor,
            self._context,
        )

        content = ""
        succeeded = False
        error = "子 Agent 未产生终态"

        def collect_text(event: AgentEvent) -> None:
            nonlocal content, succeeded, error
            if event.type == "message_end" and isinstance(event.message, AssistantMessage):
                content = "".join(
                    block.text for block in event.message.content if isinstance(block, TextBlock)
                )
            elif event.type == "agent_end":
                succeeded = event.status == "completed"
                error = event.error or f"子 Agent 未完成：{event.stop_reason}"

        unsubscribe = agent.subscribe(collect_text)
        try:
            await agent.run(
                request.task,
                system_prompt=prompt,
                permission_mode=mode,
            )
        except Exception as exc:
            return SubagentResult(session_id, f"子 Agent 执行失败：{exc}", False)
        finally:
            # run() 的等待被 shield 隔离，父任务取消时必须显式关闭子任务。
            await agent.close()
            unsubscribe()
        return SubagentResult(session_id, content if succeeded else error, succeeded)


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
