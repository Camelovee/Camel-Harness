from fastapi import APIRouter

from .dependencies import HttpDependencies
from .routes import (
    create_agent_router,
    create_approvals_router,
    create_health_router,
    create_sessions_router,
    create_settings_router,
    create_workspaces_router,
)


def create_router(dependencies: HttpDependencies) -> APIRouter:
    """聚合只依赖应用服务的 HTTP 子路由。"""

    router = APIRouter(prefix="/api")

    router.include_router(create_health_router())
    router.include_router(create_sessions_router(dependencies))
    router.include_router(create_settings_router(dependencies))
    router.include_router(create_workspaces_router(dependencies))
    router.include_router(create_agent_router(dependencies))
    router.include_router(create_approvals_router(dependencies))
    return router
