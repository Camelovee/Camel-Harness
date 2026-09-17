from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from ..dependencies import HttpDependencies


def create_workspaces_router(dependencies: HttpDependencies) -> APIRouter:
    """创建工作区查询与本机目录选择路由。"""

    router = APIRouter()

    @router.get("/workspaces")
    async def workspaces() -> dict[str, Any]:
        result = await dependencies.require().list_workspaces()
        return {
            "workspaces": [
                {
                    "workspace": workspace,
                    "updated_at": max(session.updated_at for session in sessions),
                    "sessions": [asdict(session) for session in sessions],
                }
                for workspace, sessions in result
            ]
        }

    @router.post("/workspaces/select")
    async def select_workspace() -> dict[str, Any]:
        try:
            result = await dependencies.require().select_workspace()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"workspace": str(result) if result else None}

    return router
