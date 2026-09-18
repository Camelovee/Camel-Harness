"""按 HTTP 资源分组的路由工厂。"""

from .agent import create_agent_router
from .approvals import create_approvals_router
from .health import create_health_router
from .sessions import create_sessions_router
from .workspaces import create_workspaces_router

__all__ = [
    "create_agent_router",
    "create_approvals_router",
    "create_health_router",
    "create_sessions_router",
    "create_workspaces_router",
]
