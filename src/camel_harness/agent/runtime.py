from __future__ import annotations

import logging
from pathlib import Path

from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    create_anthropic_adapter,
    create_openai_adapter,
)

from .agent import Agent
from .config import AgentSettings
from .context import CompactionEngine, ContextManager
from .resources import MemoryStore, ProjectRules, Skills
from .session import SessionManager
from .session.jsonl import JsonlSessionStorage
from .state import AgentOptions
from .tools import (
    ApprovalHandler,
    PermissionService,
    ToolExecutor,
    ToolRegistry,
)
from .tools.builtin import BuiltinToolProvider
from .tools.file_result_storage import FileToolResultStorage
from .tools.mcp import McpManager
from .tools.skill_loader import SkillToolProvider
from .tools.subagent import LocalSubagentRunner, SubagentToolProvider

logger = logging.getLogger(__name__)


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
        memories = MemoryStore(workspace)

        registry = ToolRegistry()
        result_storage = FileToolResultStorage(sessions.directory)
        await registry.register_provider(BuiltinToolProvider(result_storage))
        await registry.register_provider(SkillToolProvider(skills))
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
