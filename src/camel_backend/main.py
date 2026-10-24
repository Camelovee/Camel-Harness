from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from camel_backend.api import (
    HttpDependencies,
    create_api_app,
)
from camel_backend.approval import WebApproval
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.settings_store import SettingsStore
from camel_backend.workspace import NativeWorkspacePicker
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.resources import MemoryStore, Skills
from camel_harness.agent.runtime import AgentExtensions, build_agent, build_session_manager
from camel_harness.agent.subagent import SubagentToolProvider
from camel_harness.agent.tools import ToolProvider
from camel_harness.agent.tools.mcp import McpManager
from camel_harness.agent.tools.memory_tools import MemoryToolProvider
from camel_harness.agent.tools.skill_loader import SkillToolProvider

logger = logging.getLogger(__name__)


async def _local_extensions(agent_settings: AgentSettings, workspace: Path) -> AgentExtensions:
    """Web 入口注入的本地可选能力：Skill、Memory、MCP 与子 Agent 委派。"""

    skills = Skills(workspace)
    memory_store = (
        MemoryStore(workspace, agent_settings.memory) if agent_settings.memory.enabled else None
    )
    mcp_manager = McpManager(workspace, workspace / agent_settings.mcp_config)
    await mcp_manager.start()
    for error in mcp_manager.errors:
        logger.warning("MCP 工具加载失败：%s", error)
    tool_providers: list[ToolProvider] = [SkillToolProvider(skills), mcp_manager]
    if memory_store is not None:
        tool_providers.append(MemoryToolProvider(memory_store))
    return AgentExtensions(
        tool_providers=tuple(tool_providers),
        skills=skills,
        memories=memory_store,
        subagent_provider=SubagentToolProvider,
        on_close=(mcp_manager.close,),
    )


def create_app(workspace: Path | None = None) -> FastAPI:
    """创建 Web 应用；Agent 依赖在 lifespan 内启动和关闭。"""

    resolved_workspace = (workspace or Path.home()).resolve()
    dependencies = HttpDependencies(resolved_workspace)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        agent_settings = AgentSettings.load()
        web_approval = WebApproval()
        session_manager = build_session_manager(agent_settings)
        settings_store = SettingsStore()
        workspace_picker = NativeWorkspacePicker()

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
            select_workspace=workspace_picker.select,
        )
        try:
            yield
        finally:
            await dependencies.close()

    frontend_dist = Path(__file__).resolve().parents[2] / "webui" / "dist"
    return create_api_app(dependencies, lifespan, frontend_dist)


app = create_app()


def main() -> None:
    """启动本地 FastAPI Web Server。"""

    print(f"Camel Harness Web｜workspace={Path.home().resolve()}")
    uvicorn.run(
        "camel_backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
