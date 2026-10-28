from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .dependencies import HttpDependencies
from .desktop import DesktopApiGuard
from .errors import install_exception_handlers
from .router import create_router

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_api_app(
    dependencies: HttpDependencies,
    lifespan: Lifespan,
    frontend_dist: Path,
    *,
    desktop_token: str,
) -> FastAPI:
    """创建 HTTP API，只负责路由、中间件和传输投影。"""

    application = FastAPI(
        title="Camel Harness Desktop Backend",
        version="1.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(DesktopApiGuard, token=desktop_token)

    @application.get("/api/desktop/status")
    async def desktop_status() -> dict[str, int]:
        return {"active_runs": dependencies.require_runs().active_count}

    @application.post("/api/desktop/exit")
    async def prepare_exit() -> dict[str, int]:
        return {"active_runs": await dependencies.require_runs().prepare_exit()}

    @application.delete("/api/desktop/exit", status_code=204)
    async def cancel_exit() -> None:
        await dependencies.require_runs().cancel_exit()

    install_exception_handlers(application)
    application.include_router(create_router(dependencies))

    application.mount("/", StaticFiles(directory=frontend_dist, html=True), name="renderer")
    return application
