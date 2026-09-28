from pathlib import Path

from fastapi import APIRouter

from camel_harness.agent import SessionId
from camel_harness.agent.session import message_to_dict

from ..contracts import (
    SessionCreateBody,
    SessionMessagesResponse,
    SessionResponse,
    SessionsResponse,
    SessionUpdateBody,
)
from ..dependencies import HttpDependencies


def create_sessions_router(dependencies: HttpDependencies) -> APIRouter:
    """创建会话元数据与历史记录路由。"""

    router = APIRouter()

    @router.get("/sessions")
    async def sessions() -> SessionsResponse:
        result = await dependencies.require_sessions().list()
        return SessionsResponse(
            sessions=[
                SessionResponse.from_metadata(item)
                for item in result
                if item.parent_session_id is None
            ]
        )

    @router.post("/sessions", status_code=201)
    async def create_session(body: SessionCreateBody) -> SessionResponse:
        workspace = (
            Path(body.workspace).expanduser()
            if body.workspace and body.workspace.strip()
            else dependencies.workspace
        )
        result = await dependencies.require_sessions().create(
            SessionId.new().value, workspace, body.name.strip()
        )
        return SessionResponse.from_metadata(result)

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> SessionResponse:
        result = await dependencies.require_sessions().get(session_id)
        if result is None:
            raise KeyError("会话不存在")
        return SessionResponse.from_metadata(result)

    @router.patch("/sessions/{session_id}")
    async def update_session(session_id: str, body: SessionUpdateBody) -> SessionResponse:
        result = await dependencies.require_session_service().update(
            session_id, name=body.name, archived=body.archived, model_id=body.model_id
        )
        return SessionResponse.from_metadata(result)

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> None:
        await dependencies.require_session_service().delete_cascade(session_id)

    @router.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str) -> SessionMessagesResponse:
        messages = await dependencies.require_sessions().messages(session_id)
        return SessionMessagesResponse(
            messages=[message_to_dict(message) for message in messages],
            session_id=session_id,
        )

    return router
