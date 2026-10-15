from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from camel_harness.agent.tools import PermissionMode

from ..contracts import ResumeBody, RunBody, SteerBody, SteerQueuedResponse
from ..dependencies import HttpDependencies
from ..responses import agent_stream_response


def create_agent_router(dependencies: HttpDependencies) -> APIRouter:
    """创建 Agent Run 流式执行路由。"""

    router = APIRouter()

    @router.post("/sessions/{session_id}/runs")
    async def run_agent(session_id: str, body: RunBody) -> StreamingResponse:
        async with dependencies.require_runs().acquire(session_id, body.model_id) as (agent, model):
            return await agent_stream_response(
                agent,
                lambda: agent.run(
                    body.prompt,
                    model=model,
                    max_turns=body.max_turns,
                    permission_mode=PermissionMode(body.permission_mode),
                    thinking=body.thinking,
                ),
            )

    @router.post("/sessions/{session_id}/runs/current/steer", status_code=202)
    async def steer_agent(session_id: str, body: SteerBody) -> SteerQueuedResponse:
        result = await dependencies.require_runs().steer(session_id, body.content)
        return SteerQueuedResponse(input_id=result.input_id, run_id=result.run_id, status="queued")

    @router.post("/sessions/{session_id}/runs/current/abort", status_code=202)
    async def abort_agent(session_id: str) -> dict[str, bool]:
        await dependencies.require_runs().abort(session_id)
        return {"accepted": True}

    @router.post("/sessions/{session_id}/runs/resume")
    async def resume_agent(session_id: str, body: ResumeBody) -> StreamingResponse:
        async with dependencies.require_runs().acquire(session_id, body.model_id) as (agent, model):
            return await agent_stream_response(
                agent,
                lambda: agent.resume(
                    model=model,
                    max_turns=body.max_turns,
                    permission_mode=PermissionMode(body.permission_mode),
                    thinking=body.thinking,
                ),
            )

    return router
