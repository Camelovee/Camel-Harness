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
    validate_tool_arguments,
)
from .tools.builtin import BuiltinToolProvider
from .tools.file_result_storage import FileToolResultStorage
from .tools.mcp import McpManager
from .tools.subagent import LocalSubagentRunner, SubagentToolProvider

logger = logging.getLogger(__name__)


def build_session_manager(settings: AgentSettings) -> SessionManager:
    """创建入口层可独立使用的全局 JSONL 会话管理器。"""

    return SessionManager(JsonlSessionStorage(settings.resolved_session_dir()))


async def build_agent(
    settings: AgentSettings,
    workspace: Path,
    approval: ApprovalHandler,
    sessions: SessionManager | None = None,
) -> Agent:
    """在一个组合函数中创建完整 Agent。"""

    llm = LlmClient(_build_llm_adapter(settings))
    sessions = sessions or build_session_manager(settings)
    skills = Skills(workspace)
    memories = MemoryStore(workspace)

    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    mcp = McpManager(workspace, workspace / settings.mcp_config)
    await mcp.start()
    await registry.register_provider(mcp)
    for error in mcp.errors:
        logger.warning("MCP 工具加载失败：%s", error)

    permission = PermissionService(approval)
    executor = ToolExecutor(
        registry,
        validate_tool_arguments,
        permission,
        FileToolResultStorage().normalize,
    )
    options = AgentOptions(
        model=settings.model,
        max_output_tokens=settings.max_output_tokens,
        max_total_tokens=settings.max_total_tokens,
        max_duration_seconds=settings.max_duration_seconds,
    )
    context = ContextManager(
        CompactionEngine(
            llm,
            pressure_threshold_chars=settings.max_context_chars,
            min_messages_to_compact=settings.compaction_min_messages,
            max_summary_tokens=settings.max_compaction_tokens,
        ),
        ProjectRules(),
        skills,
        memories,
    )
    subagent = LocalSubagentRunner(
        llm,
        sessions,
        registry,
        validate_tool_arguments,
        permission,
        context,
        options,
    )
    await registry.register_provider(SubagentToolProvider(subagent.run))

    return Agent(
        options,
        llm,
        registry,
        executor,
        sessions,
        context,
        on_close=mcp.close,
    )


def _build_llm_adapter(settings: AgentSettings) -> LlmAdapter:
    """使用官方 SDK 创建当前配置的 Provider。"""

    if settings.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("默认 Anthropic Provider 需要设置 ANTHROPIC_API_KEY")
        return create_anthropic_adapter(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
        )
    if not settings.openai_api_key:
        raise ValueError("OpenAI Provider 需要设置 OPENAI_API_KEY")
    return create_openai_adapter(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
