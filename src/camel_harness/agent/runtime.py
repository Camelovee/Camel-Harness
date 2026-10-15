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


def build_session_manager(settings: AgentSettings) -> SessionManager:
    """创建入口层可独立使用的全局 JSONL 会话管理器。"""

    return SessionManager(JsonlSessionStorage(settings.resolved_session_dir()))


async def build_agent(
    settings: AgentSettings,
    session_id: str,
    workspace: Path,
    approval: ApprovalHandler,
    sessions: SessionManager,
    extensions: AgentExtensions | None = None,
) -> Agent:
    """组装核心运行时；可选 Provider 由入口层经 AgentExtensions 注入。"""

    extensions = extensions or AgentExtensions()
    metadata = await sessions.get(session_id)
    if metadata is None:
        raise KeyError(f"会话不存在：{session_id}")
    if Path(metadata.workspace).resolve() != workspace.resolve():
        raise ValueError("Agent 工作区必须与会话工作区一致")
    llm = _build_llm_client()

    async def close_resources() -> None:
        """组合层拥有扩展资源，任一资源关闭失败仍尝试关闭其余资源。"""
        error: Exception | None = None
        for closer in extensions.on_close:
            try:
                await closer()
            except Exception as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error

    try:
        session = sessions.bind(session_id)
        result_storage = FileToolResultStorage(
            sessions.directory, registered_paths=sessions.tool_result_paths
        )

        registry = ToolRegistry()
        await registry.register_provider(BuiltinToolProvider(result_storage))
        for provider in extensions.tool_providers:
            await registry.register_provider(provider)

        permission = PermissionService(approval)
        executor = ToolExecutor(
            registry,
            permission,
            result_storage.normalize,
        )
        options = AgentOptions(
            max_output_tokens=settings.max_output_tokens,
            max_duration_seconds=settings.max_duration_seconds,
            max_steering_messages=settings.max_steering_messages,
            max_steering_chars=settings.max_steering_chars,
        )
        context = ContextManager(
            CompactionEngine(
                llm,
                settings=settings.compaction,
            ),
            ProjectRules(),
            extensions.skills,
            extensions.memories,
            memory_settings=settings.memory,
        )
        if extensions.subagent_provider is not None:
            subagent = LocalSubagentRunner(
                llm,
                sessions,
                registry,
                permission,
                context,
                options,
            )
            await registry.register_provider(extensions.subagent_provider(subagent.run))

        return Agent(
            session,
            workspace,
            options,
            llm,
            registry,
            executor,
            context,
            system_prompt="你是一个可靠的 Harness Agent。",
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
