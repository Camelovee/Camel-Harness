from __future__ import annotations

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
from camel_backend.web_settings import WebSettingsStore
from camel_backend.workspace import NativeWorkspacePicker
from camel_harness.agent import Agent
from camel_harness.agent.config import AgentSettings
from camel_harness.agent.runtime import build_agent, build_session_manager


def create_app(workspace: Path | None = None) -> FastAPI:
    """创建 Web 应用；Agent 依赖在 lifespan 内启动和关闭。"""

    resolved_workspace = (workspace or Path.cwd()).resolve()
    dependencies = HttpDependencies(resolved_workspace)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings = AgentSettings()
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
            )

        dependencies.bind(
            create_session_agent, sessions, approval, workspace_picker.select, web_settings
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

    typer.echo(f"Camel Harness Web｜workspace={Path.cwd().resolve()}")
    uvicorn.run(
        "camel_backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
