from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    TextBlock,
    create_anthropic_adapter,
    create_openai_adapter,
)

from .agent import Agent
from .config import AgentSettings
from .context import CompactionEngine, ContextManager
from .events import AgentEvent
from .resources import MemoryStore, ProjectRules, Skills
from .session import SessionManager
from .session.jsonl import JsonlSessionStorage
from .state import AgentOptions
from .tools import (
    ApprovalHandler,
    PermissionMode,
    PermissionService,
    ToolExecutor,
    ToolRegistry,
)
from .tools.builtin import BuiltinToolProvider
from .tools.file_result_storage import FileToolResultStorage
from .tools.filesystem import ReadFileTool
from .tools.mcp import McpManager
from .tools.memory_tools import MemoryToolProvider
from .tools.skill_loader import SkillToolProvider
from .tools.subagent import SubagentRequest, SubagentResult, SubagentToolProvider, SubagentType

logger = logging.getLogger(__name__)


_READ_ONLY_TOOLS = frozenset({"read_file", "load_skill", "search_memory", "read_memory"})
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
        session = self._sessions.bind(session_id)

        async def registered_paths(requested_id: str) -> frozenset[Path]:
            if requested_id != session_id:
                raise ValueError("结果读取必须使用子 Agent 自身会话")
            return await session.tool_result_paths()

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


def build_session_manager(settings: AgentSettings) -> SessionManager:
    """创建入口层可独立使用的全局 JSONL 会话管理器。"""

    return SessionManager(JsonlSessionStorage(settings.resolved_session_dir()))


async def build_agent(
    settings: AgentSettings,
    session_id: str,
    workspace: Path,
    approval: ApprovalHandler,
    sessions: SessionManager,
) -> Agent:
    """在一个组合函数中创建完整 Agent。"""

    metadata = await sessions.get(session_id)
    if metadata is None:
        raise KeyError(f"会话不存在：{session_id}")
    if Path(metadata.workspace).resolve() != workspace.resolve():
        raise ValueError("Agent 工作区必须与会话工作区一致")
    llm = LlmClient(_build_llm_adapter(settings))
    mcp = McpManager(workspace, workspace / settings.mcp_config)

    async def close_resources() -> None:
        """组合层拥有连接，任一资源关闭失败仍尝试关闭其余资源。"""
        try:
            await mcp.close()
        finally:
            await llm.close()

    try:
        session = sessions.bind(session_id)
        skills = Skills(workspace)
        memories = MemoryStore(workspace) if settings.memory.enabled else None

        registry = ToolRegistry()

        async def registered_paths(session_id: str) -> frozenset[Path]:
            bound = session if session_id == session.session_id else sessions.bind(session_id)
            return await bound.tool_result_paths()

        result_storage = FileToolResultStorage(
            sessions.directory, registered_paths=registered_paths
        )
        await registry.register_provider(BuiltinToolProvider(result_storage))
        await registry.register_provider(SkillToolProvider(skills))
        if memories is not None:
            await registry.register_provider(MemoryToolProvider(memories))
        await mcp.start()
        await registry.register_provider(mcp)
        for error in mcp.errors:
            logger.warning("MCP 工具加载失败：%s", error)

        permission = PermissionService(approval)
        executor = ToolExecutor(
            registry,
            permission,
            result_storage.normalize,
        )
        options = AgentOptions(
            model=settings.model,
            max_output_tokens=settings.max_output_tokens,
            max_duration_seconds=settings.max_duration_seconds,
        )
        context = ContextManager(
            CompactionEngine(
                llm,
                settings=settings.compaction,
            ),
            ProjectRules(),
            skills,
            memories,
            memory_settings=settings.memory,
        )
        subagent = LocalSubagentRunner(
            llm,
            sessions,
            registry,
            permission,
            context,
            options,
        )
        await registry.register_provider(SubagentToolProvider(subagent.run))

        return Agent(
            session,
            workspace,
            options,
            llm,
            registry,
            executor,
            context,
            on_close=close_resources,
        )
    except BaseException:
        try:
            await close_resources()
        except Exception as exc:
            logger.warning("构造失败后资源清理异常：%s", type(exc).__name__)
        raise


def _build_llm_adapter(settings: AgentSettings) -> LlmAdapter:
    """使用官方 SDK 创建当前配置的 Provider。"""

    if settings.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("请在 ~/.camel/config.json 中配置 anthropic_api_key")
        return create_anthropic_adapter(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
        )
    if not settings.openai_api_key:
        raise ValueError("请在 ~/.camel/config.json 中配置 openai_api_key")
    return create_openai_adapter(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
