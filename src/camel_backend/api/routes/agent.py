from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from camel_harness.agent.tools import PermissionMode

from ..contracts import RunBody
from ..dependencies import HttpDependencies
from ..responses import agent_stream_response


def create_agent_router(dependencies: HttpDependencies) -> APIRouter:
    """创建 Agent Run 流式执行路由。"""

    router = APIRouter()

    @router.post("/sessions/{session_id}/runs")
    async def run_agent(session_id: str, body: RunBody) -> StreamingResponse:
        agent = dependencies.require()
        session = await dependencies.require_sessions().get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return agent_stream_response(
            agent.run(
                session_id,
                body.prompt,
                Path(session.workspace),
                max_turns=body.max_turns,
                permission_mode=PermissionMode(body.permission_mode),
                thinking=body.thinking,
            )
        )

    return router
