from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from camel_harness.llm import (
    LlmClient,
    create_anthropic_adapter,
    create_openai_adapter,
)

from .agent import Agent, CloseHandler
from .config import AgentSettings
from .context import CompactionEngine, ContextManager
from .context.system_prompt import DEFAULT_SYSTEM_PROMPT
from .resources import MemoryStore, ProjectRules, Skills
from .session import SessionManager
from .session.jsonl import JsonlSessionStorage
from .state import AgentOptions
from .subagent.runner import LocalSubagentRunner, SubagentProviderFactory
from .tools import (
    ApprovalHandler,
    PermissionService,
    ToolExecutor,
    ToolProvider,
    ToolRegistry,
)
from .tools.builtin import BuiltinToolProvider
from .tools.file_result_storage import FileToolResultStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentExtensions:
    """入口层按需注入的可选能力；缺省时 Agent 只注册内置工具。"""

    tool_providers: tuple[ToolProvider, ...] = ()
    skills: Skills | None = None
    memories: MemoryStore | None = None
    subagent_provider: SubagentProviderFactory | None = None
    on_close: tuple[CloseHandler, ...] = ()


def build_session_manager(agent_settings: AgentSettings) -> SessionManager:
    """创建入口层可独立使用的全局 JSONL 会话管理器。"""

    return SessionManager(JsonlSessionStorage(agent_settings.resolved_session_dir()))


async def build_agent(
    agent_settings: AgentSettings,
    session_id: str,
    workspace: Path,
    approval_handler: ApprovalHandler,
    session_manager: SessionManager,
    agent_extensions: AgentExtensions | None = None,
) -> Agent:
    """组装核心运行时；可选 Provider 由入口层经 AgentExtensions 注入。"""

    agent_extensions = agent_extensions or AgentExtensions()
    metadata = await session_manager.get(session_id)
    if metadata is None:
        raise KeyError(f"会话不存在：{session_id}")
    if Path(metadata.workspace).resolve() != workspace.resolve():
        raise ValueError("Agent 工作区必须与会话工作区一致")
    llm_client = _build_llm_client()

    async def close_resources() -> None:
        """组合层拥有扩展资源，任一资源关闭失败仍尝试关闭其余资源。"""
        error: Exception | None = None
        for closer in agent_extensions.on_close:
            try:
                await closer()
            except Exception as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error

    try:
        session = session_manager.bind(session_id)
        tool_result_storage = FileToolResultStorage(
            session_manager.directory, registered_paths=session_manager.tool_result_paths
        )

        tool_registry = ToolRegistry()
        await tool_registry.register_provider(BuiltinToolProvider(tool_result_storage))
        for provider in agent_extensions.tool_providers:
            await tool_registry.register_provider(provider)

        permission_service = PermissionService(approval_handler)
        tool_executor = ToolExecutor(
            tool_registry=tool_registry,
            permission_service=permission_service,
            normalize_result=tool_result_storage.normalize,
        )
        agent_options = AgentOptions(
            max_duration_seconds=agent_settings.max_duration_seconds,
            max_steering_messages=agent_settings.max_steering_messages,
            max_steering_chars=agent_settings.max_steering_chars,
        )
        context_manager = ContextManager(
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            compaction_engine=CompactionEngine(
                llm_client=llm_client,
                compaction_settings=agent_settings.compaction,
            ),
            project_rules=ProjectRules(),
            skills=agent_extensions.skills,
            memory_store=agent_extensions.memories,
            memory_settings=agent_settings.memory,
        )
        if agent_extensions.subagent_provider is not None:
            subagent_runner = LocalSubagentRunner(
                llm_client=llm_client,
                session_manager=session_manager,
                tool_registry=tool_registry,
                permission_service=permission_service,
                context_manager=context_manager,
                agent_options=agent_options,
            )
            await tool_registry.register_provider(
                agent_extensions.subagent_provider(subagent_runner.run)
            )

        return Agent(
            session=session,
            workspace=workspace,
            agent_options=agent_options,
            llm_client=llm_client,
            tool_executor=tool_executor,
            context_manager=context_manager,
            on_close=close_resources,
        )
    except BaseException:
        try:
            await close_resources()
        except Exception as exc:
            logger.warning("构造失败后资源清理异常：%s", type(exc).__name__)
        raise


def _build_llm_client() -> LlmClient:
    """创建路由全部内置 Provider 的无状态 LLM Client；凭据随每次请求进入。"""

    return LlmClient(
        {
            "anthropic": create_anthropic_adapter(),
            "openai": create_openai_adapter(),
        }
    )
