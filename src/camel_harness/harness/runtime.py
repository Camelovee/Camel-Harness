from __future__ import annotations

from pathlib import Path

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from camel_harness import Agent, AgentLoop, AgentOptions
from camel_harness.harness.compaction import CompactionEngine, ContextManager
from camel_harness.harness.config import HarnessSettings
from camel_harness.harness.resources import (
    MemoryStore,
    ProjectRules,
    Skills,
)
from camel_harness.harness.session import SessionManager
from camel_harness.harness.session.jsonl import JsonlSessionStorage
from camel_harness.harness.tools.builtin import BuiltinToolProvider
from camel_harness.harness.tools.file_result_storage import FileToolResultStorage
from camel_harness.harness.tools.knowledge import KnowledgeToolProvider
from camel_harness.harness.tools.mcp import McpManager
from camel_harness.harness.tools.plugins import EntryPointToolProvider
from camel_harness.harness.tools.search import ToolSearchProvider
from camel_harness.harness.tools.subagent import SubagentToolProvider
from camel_harness.llm import LlmAdapter, LlmClient
from camel_harness.llm.anthropic import AnthropicLlmAdapter
from camel_harness.llm.openai import OpenAiLlmAdapter
from camel_harness.tools import (
    ApprovalCenter,
    ApprovalHandler,
    PermissionService,
    ToolExecutor,
    ToolRegistry,
)
from camel_harness.tools.jsonschema_validator import validate_tool_arguments

from .agent_harness import Harness
from .local_subagent import LocalSubagentRunner
from .workspace import NativeWorkspacePicker


async def build_harness(
    settings: HarnessSettings,
    workspace: Path,
    approval: ApprovalHandler,
) -> Harness:
    """在一个组合函数中创建完整 Harness，避免分层 wiring。"""

    llm = LlmClient(_build_llm_adapter(settings))
    sessions = SessionManager(JsonlSessionStorage(settings.resolved_session_dir()))
    skills = Skills(workspace)
    memories = MemoryStore(workspace)

    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    await registry.register_provider(KnowledgeToolProvider(skills, memories))
    plugins = EntryPointToolProvider()
    await registry.register_provider(plugins)
    mcp = McpManager(workspace, workspace / settings.mcp_config)
    await mcp.start()
    await registry.register_provider(mcp)
    await registry.register_provider(ToolSearchProvider(registry))

    permission = PermissionService(approval)
    executor = ToolExecutor(
        registry,
        validate_tool_arguments,
        permission,
        FileToolResultStorage().normalize,
    )
    options = AgentOptions(
        provider=settings.provider,
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
    agent = Agent(options, AgentLoop(llm, registry, executor))
    return Harness(
        agent=agent,
        sessions=sessions,
        context=context,
        mcp=mcp,
        select_workspace=NativeWorkspacePicker().select,
        approvals=approval if isinstance(approval, ApprovalCenter) else None,
        plugin_errors=tuple(plugins.errors),
    )


def _build_llm_adapter(settings: HarnessSettings) -> LlmAdapter:
    """使用官方 SDK 创建当前配置的 Provider。"""

    if settings.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("默认 Anthropic Provider 需要设置 ANTHROPIC_API_KEY")
        return AnthropicLlmAdapter(
            AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                base_url=settings.anthropic_base_url,
            )
        )
    if not settings.openai_api_key:
        raise ValueError("OpenAI Provider 需要设置 OPENAI_API_KEY")
    return OpenAiLlmAdapter(
        AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    )
