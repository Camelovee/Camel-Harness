from fastapi import APIRouter

from camel_harness.agent.session import SessionMetadata

from ..contracts import SessionResponse, WorkspaceGroupResponse, WorkspacesResponse
from ..dependencies import HttpDependencies


def create_workspaces_router(dependencies: HttpDependencies) -> APIRouter:
    """创建工作区查询与本机目录选择路由。"""

    router = APIRouter()

    @router.get("/workspaces")
    async def workspaces() -> WorkspacesResponse:
        result = _group_sessions(await dependencies.require_sessions().list())
        return WorkspacesResponse(
            workspaces=[
                WorkspaceGroupResponse(
                    workspace=workspace,
                    updated_at=max(session.updated_at for session in sessions),
                    sessions=[SessionResponse.from_metadata(session) for session in sessions],
                )
                for workspace, sessions in result
            ]
        )

    @router.post("/workspaces/select")
    async def select_workspace() -> dict[str, str | None]:
        result = await dependencies.select_workspace()
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
