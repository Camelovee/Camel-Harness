from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from camel_harness.types import PermissionMode

from ..contracts import TurnBody
from ..dependencies import HttpDependencies
from ..responses import agent_stream_response


def create_agent_router(dependencies: HttpDependencies) -> APIRouter:
    """创建 Agent Turn 流式执行路由。"""

    router = APIRouter()

    @router.post("/sessions/{session_id}/turns")
    async def run_turn(session_id: str, body: TurnBody) -> StreamingResponse:
        harness = dependencies.require()
        session = await harness.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return agent_stream_response(
            harness.run_turn(
                session_id,
                body.prompt,
                Path(session.workspace),
                max_steps=body.max_steps,
                permission_mode=PermissionMode(body.permission_mode),
                thinking=body.thinking,
            )
        )

    return router
