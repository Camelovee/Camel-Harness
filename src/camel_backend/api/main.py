from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .dependencies import HttpDependencies
from .desktop import DesktopApiGuard
from .errors import install_exception_handlers
from .router import create_router

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_api_app(
    dependencies: HttpDependencies,
    lifespan: Lifespan,
    frontend_dist: Path | None = None,
    *,
    desktop_token: str | None = None,
) -> FastAPI:
    """创建 HTTP API，只负责路由、中间件和传输投影。"""

    application = FastAPI(
        title="Camel Harness Web",
        version="1.1.0",
        lifespan=lifespan,
        docs_url=None if desktop_token else "/docs",
        redoc_url=None if desktop_token else "/redoc",
        openapi_url=None if desktop_token else "/openapi.json",
    )
    if desktop_token:
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

    else:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    install_exception_handlers(application)
    application.include_router(create_router(dependencies))

    if frontend_dist is not None and frontend_dist.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=frontend_dist, html=True),
            name="webui",
        )
    else:

        @application.get("/")
        async def root() -> dict[str, str]:
            return {"name": "Camel Harness Web", "docs": "/docs", "api": "/api/health"}

    return application
