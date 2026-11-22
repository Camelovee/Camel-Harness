from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from camel_backend.api import (
    HttpDependencies,
    create_api_app,
)
from camel_backend.approval import WebApproval
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.settings_store import SettingsStore
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.memory import MemoryManager
from camel_harness.agent.resources import Skills
from camel_harness.agent.runtime import AgentExtensions, build_agent, build_session_manager
from camel_harness.agent.subagent import SubagentToolProvider
from camel_harness.agent.tools import ToolProvider
from camel_harness.agent.tools.mcp import McpManager
from camel_harness.agent.tools.memory_tools import MemoryToolProvider
from camel_harness.agent.tools.skill_loader import SkillToolProvider

logger = logging.getLogger(__name__)


async def _local_extensions(agent_settings: AgentSettings, workspace: Path) -> AgentExtensions:
    """桌面入口注入的本地可选能力：Skill、Memory、MCP 与子 Agent 委派。"""

    skills = Skills(workspace)
    memory_manager = (
        MemoryManager(workspace, agent_settings.memory) if agent_settings.memory.enabled else None
    )
    mcp_manager = McpManager(workspace, workspace / agent_settings.mcp_config)
    await mcp_manager.start()
    for error in mcp_manager.errors:
        logger.warning("MCP 工具加载失败：%s", error)
    tool_providers: list[ToolProvider] = [SkillToolProvider(skills), mcp_manager]
    if memory_manager is not None:
        tool_providers.append(MemoryToolProvider(memory_manager))
    return AgentExtensions(
        tool_providers=tuple(tool_providers),
        skills=skills,
        memory_manager=memory_manager,
        subagent_provider=SubagentToolProvider,
        on_close=(mcp_manager.close,),
    )


def create_app(
    workspace: Path | None = None,
    *,
    frontend_dist: Path,
    desktop_token: str,
) -> FastAPI:
    """创建桌面内部服务；Agent 依赖在 lifespan 内启动和关闭。"""

    resolved_workspace = (workspace or Path.home()).resolve()
    dependencies = HttpDependencies(resolved_workspace)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        agent_settings = AgentSettings.load()
        web_approval = WebApproval()
        session_manager = build_session_manager(agent_settings)
        settings_store = SettingsStore()

        async def create_session_agent(
            session_id: str, agent_workspace: Path, agent_settings: AgentSettings
        ) -> Agent:
            return await build_agent(
                agent_settings=agent_settings,
                session_id=session_id,
                workspace=agent_workspace,
                approval_handler=web_approval,
                session_manager=session_manager,
                agent_extensions=await _local_extensions(agent_settings, agent_workspace),
            )

        run_service = RunService(
            build_agent=create_session_agent,
            session_manager=session_manager,
            settings_store=settings_store,
        )
        dependencies.bind(
            run_service=run_service,
            session_manager=session_manager,
            session_service=SessionService(
                session_manager=session_manager,
                run_service=run_service,
                settings_store=settings_store,
            ),
            settings_service=SettingsService(
                settings_store=settings_store,
                session_manager=session_manager,
                run_service=run_service,
            ),
            settings_store=settings_store,
            web_approval=web_approval,
        )
        try:
            yield
        finally:
            await dependencies.close()

    application = create_api_app(dependencies, lifespan, frontend_dist, desktop_token=desktop_token)
    application.state.dependencies = dependencies
    return application
