from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import typer
import uvicorn
from fastapi import FastAPI

from camel_backend.api import (
    HttpDependencies,
    create_api_app,
)
from camel_backend.approval import WebApproval
from camel_backend.orchestration import RunService, SessionService, SettingsService
from camel_backend.web_settings import WebSettingsStore
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


async def _local_extensions(settings: AgentSettings, workspace: Path) -> AgentExtensions:
    """Web 入口注入的本地可选能力：Skill、Memory、MCP 与子 Agent 委派。"""

    skills = Skills(workspace)
    memories = MemoryStore(workspace) if settings.memory.enabled else None
    mcp = McpManager(workspace, workspace / settings.mcp_config)
    await mcp.start()
    for error in mcp.errors:
        logger.warning("MCP 工具加载失败：%s", error)
    providers: list[ToolProvider] = [SkillToolProvider(skills), mcp]
    if memories is not None:
        providers.append(MemoryToolProvider(memories))
    return AgentExtensions(
        tool_providers=tuple(providers),
        skills=skills,
        memories=memories,
        subagent_provider=SubagentToolProvider,
        on_close=(mcp.close,),
    )


def create_app(workspace: Path | None = None) -> FastAPI:
    """创建 Web 应用；Agent 依赖在 lifespan 内启动和关闭。"""

    resolved_workspace = (workspace or Path.home()).resolve()
    dependencies = HttpDependencies(resolved_workspace)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings = AgentSettings.load()
        approval = WebApproval()
        sessions = build_session_manager(settings)
        web_settings = WebSettingsStore(
            settings.resolved_session_dir().parent / "web-settings.json", settings
        )
        workspace_picker = NativeWorkspacePicker()

        async def create_session_agent(
            session_id: str, agent_workspace: Path, model_settings: AgentSettings
        ) -> Agent:
            return await build_agent(
                model_settings,
                session_id,
                agent_workspace,
                approval=approval,
                sessions=sessions,
                extensions=await _local_extensions(model_settings, agent_workspace),
            )

        runs = RunService(create_session_agent, sessions, web_settings)
        dependencies.bind(
            runs=runs,
            sessions=sessions,
            session_service=SessionService(sessions, runs, web_settings),
            settings_service=SettingsService(web_settings, sessions, runs),
            settings=web_settings,
            approvals=approval,
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

    typer.echo(f"Camel Harness Web｜workspace={Path.home().resolve()}")
    uvicorn.run(
        "camel_backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
