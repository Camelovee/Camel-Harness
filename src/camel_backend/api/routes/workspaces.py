from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from camel_harness.agent.session import SessionMetadata

from ..dependencies import HttpDependencies


def create_workspaces_router(dependencies: HttpDependencies) -> APIRouter:
    """创建工作区查询与本机目录选择路由。"""

    router = APIRouter()

    @router.get("/workspaces")
    async def workspaces() -> dict[str, Any]:
        result = _group_sessions(await dependencies.require_sessions().list())
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
            result = await dependencies.select_workspace()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"workspace": str(result) if result else None}

    return router


def _group_sessions(
    sessions: tuple[SessionMetadata, ...],
) -> tuple[tuple[str, tuple[SessionMetadata, ...]], ...]:
    """按工作目录分组，并保持会话仓储提供的最近活动顺序。"""

    grouped: dict[str, list[SessionMetadata]] = {}
    for session in sessions:
        if session.parent_session_id is not None:
            continue
        grouped.setdefault(session.workspace, []).append(session)
    return tuple((workspace, tuple(items)) for workspace, items in grouped.items())
