from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from camel_harness.agent import SessionId
from camel_harness.agent.session import message_to_dict

from ..contracts import SessionCreateBody, SessionUpdateBody
from ..dependencies import HttpDependencies


def create_sessions_router(dependencies: HttpDependencies) -> APIRouter:
    """创建会话元数据与历史记录路由。"""

    router = APIRouter()

    @router.get("/sessions")
    async def sessions() -> dict[str, Any]:
        result = await dependencies.require_sessions().list()
        return {"sessions": [asdict(item) for item in result]}

    @router.post("/sessions", status_code=201)
    async def create_session(body: SessionCreateBody) -> dict[str, Any]:
        sessions = dependencies.require_sessions()
        workspace = (
            Path(body.workspace).expanduser()
            if body.workspace and body.workspace.strip()
            else dependencies.workspace
        )
        result = await sessions.create(SessionId.new().value, workspace, body.name.strip())
        return asdict(result)

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        result = await dependencies.require_sessions().get(session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return asdict(result)

    @router.patch("/sessions/{session_id}")
    async def update_session(session_id: str, body: SessionUpdateBody) -> dict[str, Any]:
        try:
            result = await dependencies.require_sessions().update(
                session_id, name=body.name, archived=body.archived
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="会话不存在") from exc
        return asdict(result)

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> None:
        deleted = await dependencies.require_sessions().delete(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="会话不存在")

    @router.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str) -> dict[str, Any]:
        messages = await dependencies.require_sessions().messages(session_id)
        return {
            "messages": [message_to_dict(message) for message in messages],
            "session_id": session_id,
        }

    return router
