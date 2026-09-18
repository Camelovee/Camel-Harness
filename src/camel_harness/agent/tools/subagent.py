from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from camel_harness.llm import LlmClient, Message, UserMessage

from ..agent_loop import agent_loop
from ..compaction import ContextManager
from ..events import AgentStreamEventType
from ..session import SessionManager
from ..state import AgentOptions, AgentRun, SessionId
from .executor import ToolExecutor
from .permissions import PermissionService
from .registry import ToolProvider, ToolRegistry
from .types import (
    PermissionMode,
    Tool,
    ToolArgumentValidator,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolRisk,
)


class SubagentType(StrEnum):
    """内置子 Agent 委派预设。"""

    EXPLORE = "explore"
    PLAN = "plan"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class SubagentRequest:
    """父 Agent 发起的一次隔离委派。"""

    parent_session_id: str
    task: str
    agent_type: SubagentType
    workspace: Path
    permission_mode: PermissionMode


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """被委派 Agent 回传给父 Agent 的最终文本。"""

    session_id: str
    content: str
    succeeded: bool


SubagentRunner = Callable[[SubagentRequest], Awaitable[SubagentResult]]

_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "list_files",
        "grep_search",
        "skill_list",
        "skill_load",
        "memory_list",
        "memory_load",
    }
)
_GENERAL_TOOLS = _READ_ONLY_TOOLS | frozenset({"write_file", "edit_file", "run_shell"})


class LocalSubagentRunner:
    """复用 Agent Loop，但使用隔离会话、预算和工具目录。"""

    def __init__(
        self,
        llm: LlmClient,
        sessions: SessionManager,
        registry: ToolRegistry,
        validate_arguments: ToolArgumentValidator,
        permission: PermissionService,
        context: ContextManager,
        options: AgentOptions,
    ) -> None:
        self._llm = llm
        self._sessions = sessions
        self._registry = registry
        self._validate_arguments = validate_arguments
        self._permission = permission
        self._context = context
        self._options = options

    async def run(self, request: SubagentRequest) -> SubagentResult:
        allowed = _GENERAL_TOOLS if request.agent_type is SubagentType.GENERAL else _READ_ONLY_TOOLS
        registry = self._registry.subset(allowed)
        executor = ToolExecutor(registry, self._validate_arguments, self._permission)
        options = replace(self._options, max_steps=8)
        session_id = f"sub-{SessionId.new().value}"
        mode = (
            request.permission_mode
            if request.agent_type is SubagentType.GENERAL
            else PermissionMode.PLAN
        )
        prompt = f"你是 {request.agent_type.value} 子 Agent。只完成被委派任务并返回精炼结果。"
        await self._sessions.ensure(session_id, request.workspace, "子 Agent")
        await self._sessions.append_message(session_id, UserMessage(request.task))
        dynamic_context = await self._context.build(request.task, request.workspace)
        if dynamic_context:
            prompt = f"{prompt}\n\n{dynamic_context}"

        async def load_context(run: AgentRun, active_options: AgentOptions) -> tuple[Message, ...]:
            log = await self._sessions.context_log(session_id)
            compacted = await self._context.compact_if_needed(log, active_options)
            if compacted is not None:
                await self._sessions.replace_context(
                    session_id,
                    compacted.start_seq,
                    compacted.end_seq,
                    compacted.summary_message,
                )
                run.record_tokens(compacted.input_tokens, compacted.output_tokens)
            return await self._sessions.context_messages(session_id)

        async def on_message(message: Message) -> None:
            await self._sessions.append_message(session_id, message)

        parts: list[str] = []
        try:
            async for event in agent_loop(
                llm=self._llm,
                tools=registry,
                tool_executor=executor,
                session_id=session_id,
                workspace=request.workspace,
                system_prompt=prompt,
                options=options,
                load_context=load_context,
                on_message=on_message,
                permission_mode=mode,
            ):
                if event.type is AgentStreamEventType.TEXT_DELTA:
                    parts.append(str(event.data.get("text", "")))
        except Exception as exc:
            return SubagentResult(session_id, f"子 Agent 执行失败：{exc}", False)
        return SubagentResult(session_id, "".join(parts), True)


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
