from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from camel_harness.llm import AssistantMessage, LlmClient, LlmModel, TextBlock

from ..agent import Agent
from ..context import ContextManager
from ..events import AgentEvent
from ..session import SessionManager
from ..state import AgentOptions
from ..tools import (
    PermissionMode,
    PermissionService,
    ToolExecutor,
    ToolProvider,
    ToolRegistry,
)
from ..tools.file_result_storage import FileToolResultStorage
from ..tools.filesystem import ReadFileTool

_READ_ONLY_TOOLS = frozenset({"read_file", "load_skill", "search_memory", "read_memory"})
_GENERAL_TOOLS = _READ_ONLY_TOOLS | frozenset({"write_file", "edit_file", "run_shell"})


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
    model: LlmModel


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """被委派 Agent 回传给父 Agent 的最终文本。"""

    session_id: str
    content: str
    succeeded: bool


SubagentRunner = Callable[[SubagentRequest], Awaitable[SubagentResult]]
SubagentProviderFactory = Callable[[SubagentRunner], ToolProvider]


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
        session_id = f"sub-{uuid4().hex}"
        mode = (
            request.permission_mode
            if request.agent_type is SubagentType.GENERAL
            else PermissionMode.PLAN
        )
        system_prompt = (
            f"你是 {request.agent_type.value} 子 Agent。只完成被委派任务并返回精炼结果。"
        )
        await self._sessions.create(
            session_id,
            request.workspace,
            "子 Agent",
            parent_session_id=request.parent_session_id,
            parent_tool_call_id=request.parent_tool_call_id,
        )
        session = self._sessions.bind(session_id)

        async def registered_paths(requested_id: str) -> frozenset[Path]:
            if requested_id != session_id:
                raise ValueError("结果读取必须使用子 Agent 自身会话")
            return await self._sessions.tool_result_paths(requested_id)

        result_storage = FileToolResultStorage(
            self._sessions.directory, registered_paths=registered_paths
        )
        selected = self._registry.subset(allowed)
        registry = ToolRegistry()
        for definition in selected.llm_definitions():
            tool = selected.resolve(definition.name)
            # 内置读取工具绑定子会话，其他扩展工具保留注册者提供的实现。
            registry.register(
                ReadFileTool(result_storage.resolve_read_path)
                if type(tool) is ReadFileTool
                else tool
            )
        executor = ToolExecutor(registry, self._permission, result_storage.normalize)
        agent = Agent(
            session,
            request.workspace,
            self._options,
            self._llm,
            registry,
            executor,
            self._context,
            system_prompt=system_prompt,
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
                model=request.model,
                max_turns=8,
                permission_mode=mode,
            )
        except Exception as exc:
            return SubagentResult(session_id, f"子 Agent 执行失败：{exc}", False)
        finally:
            # run() 的等待被 shield 隔离，父任务取消时必须显式关闭子任务。
            await agent.close()
            unsubscribe()
        return SubagentResult(session_id, content if succeeded else error, succeeded)
