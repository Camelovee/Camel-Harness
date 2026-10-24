from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from camel_harness.llm import AssistantMessage, LlmClient, LlmModel, TextBlock

from ..agent import Agent
from ..context import ContextManager
from ..context.system_prompt import SUBAGENT_SYSTEM_PROMPTS
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

_READ_ONLY_TOOLS = frozenset({"read_file", "load_skill"})
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
        llm_client: LlmClient,
        session_manager: SessionManager,
        tool_registry: ToolRegistry,
        permission_service: PermissionService,
        context_manager: ContextManager,
        agent_options: AgentOptions,
    ) -> None:
        self._llm_client = llm_client
        self._session_manager = session_manager
        self._tool_registry = tool_registry
        self._permission_service = permission_service
        self._context_manager = context_manager
        self._agent_options = agent_options

    async def run(self, subagent_request: SubagentRequest) -> SubagentResult:
        allowed_tool_names = (
            _GENERAL_TOOLS
            if subagent_request.agent_type is SubagentType.GENERAL
            else _READ_ONLY_TOOLS
        )
        session_id = f"sub-{uuid4().hex}"
        permission_mode = (
            subagent_request.permission_mode
            if subagent_request.agent_type is SubagentType.GENERAL
            else PermissionMode.PLAN
        )
        system_prompt = SUBAGENT_SYSTEM_PROMPTS[subagent_request.agent_type.value]
        await self._session_manager.create(
            session_id,
            subagent_request.workspace,
            "子 Agent",
            parent_session_id=subagent_request.parent_session_id,
            parent_tool_call_id=subagent_request.parent_tool_call_id,
        )
        session = self._session_manager.bind(session_id)

        async def registered_paths(requested_id: str) -> frozenset[Path]:
            if requested_id != session_id:
                raise ValueError("结果读取必须使用子 Agent 自身会话")
            return await self._session_manager.tool_result_paths(requested_id)

        tool_result_storage = FileToolResultStorage(
            self._session_manager.directory, registered_paths=registered_paths
        )
        selected_tool_registry = self._tool_registry.subset(allowed_tool_names)
        tool_registry = ToolRegistry()
        for tool_definition in selected_tool_registry.llm_definitions():
            tool = selected_tool_registry.resolve(tool_definition.name)
            # 内置读取工具绑定子会话，其他扩展工具保留注册者提供的实现。
            tool_registry.register(
                ReadFileTool(tool_result_storage.resolve_read_path)
                if type(tool) is ReadFileTool
                else tool
            )
        tool_executor = ToolExecutor(
            tool_registry=tool_registry,
            permission_service=self._permission_service,
            normalize_result=tool_result_storage.normalize,
        )
        agent = Agent(
            session=session,
            workspace=subagent_request.workspace,
            agent_options=self._agent_options,
            llm_client=self._llm_client,
            tool_executor=tool_executor,
            context_manager=self._context_manager.with_system_prompt(system_prompt),
        )

        content = ""
        succeeded = False
        error = "子 Agent 未产生终态"

        def collect_text(agent_event: AgentEvent) -> None:
            nonlocal content, succeeded, error
            if agent_event.type == "message_end" and isinstance(
                agent_event.message, AssistantMessage
            ):
                content = "".join(
                    block.text
                    for block in agent_event.message.content
                    if isinstance(block, TextBlock)
                )
            elif agent_event.type == "agent_end":
                succeeded = agent_event.status == "completed"
                error = agent_event.error or f"子 Agent 未完成：{agent_event.stop_reason}"

        unsubscribe = agent.subscribe(collect_text)
        try:
            await agent.run(
                subagent_request.task,
                model=subagent_request.model,
                max_turns=8,
                permission_mode=permission_mode,
            )
        except Exception as exc:
            return SubagentResult(session_id, f"子 Agent 执行失败：{exc}", False)
        finally:
            # run() 的等待被 shield 隔离，父任务取消时必须显式关闭子任务。
            await agent.close()
            unsubscribe()
        return SubagentResult(session_id, content if succeeded else error, succeeded)
