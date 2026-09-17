from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import typer
import uvicorn
from fastapi import FastAPI

from camel_harness.backend.api import (
    HttpDependencies,
    create_api_app,
)
from camel_harness.backend.approval import WebApproval
from camel_harness.harness.config import HarnessSettings
from camel_harness.harness.runtime import build_harness


def create_app(workspace: Path | None = None) -> FastAPI:
    """创建 Web 应用；Agent 依赖在 lifespan 内启动和关闭。"""

    resolved_workspace = (workspace or Path.cwd()).resolve()
    dependencies = HttpDependencies(resolved_workspace)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings = HarnessSettings()
        approval = WebApproval()
        harness = await build_harness(settings, resolved_workspace, approval=approval)
        dependencies.bind(harness)
        try:
            yield
        finally:
            dependencies.clear()
            await harness.close()

    frontend_dist = Path(__file__).resolve().parents[2] / "webui" / "dist"
    return create_api_app(dependencies, lifespan, frontend_dist)


app = create_app()


def main() -> None:
    """启动本地 FastAPI Web Server。"""

    typer.echo(f"Camel Harness Web｜workspace={Path.cwd().resolve()}")
    uvicorn.run(
        "camel_harness.backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
