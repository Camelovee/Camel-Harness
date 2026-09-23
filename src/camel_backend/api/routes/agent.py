from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from camel_harness.agent import AgentBusyError, AgentNotRunningError, AgentResumeError
from camel_harness.agent.tools import PermissionMode

from ..contracts import ResumeBody, RunBody, SteerBody
from ..dependencies import HttpDependencies
from ..responses import agent_stream_response


def create_agent_router(dependencies: HttpDependencies) -> APIRouter:
    """创建 Agent Run 流式执行路由。"""

    router = APIRouter()

    @router.post("/sessions/{session_id}/runs")
    async def run_agent(session_id: str, body: RunBody) -> StreamingResponse:
        try:
            async with dependencies.agent_for_run(session_id, body.model_id) as agent:
                if agent.is_running:
                    raise AgentBusyError("会话已有正在运行的 Agent")
                if body.model_id:
                    await dependencies.require_sessions().update(session_id, model_id=body.model_id)
                response = await agent_stream_response(
                    agent,
                    lambda: agent.run(
                        body.prompt,
                        max_turns=body.max_turns,
                        permission_mode=PermissionMode(body.permission_mode),
                        thinking=body.thinking,
                    ),
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="会话不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AgentBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return response

    @router.post("/sessions/{session_id}/runs/current/steer", status_code=202)
    async def steer_agent(session_id: str, body: SteerBody) -> dict[str, str]:
        agent = dependencies.find_agent(session_id)
        if agent is None:
            raise HTTPException(status_code=409, detail="会话没有活动 Run")
        try:
            result = await agent.steer(body.content)
        except AgentNotRunningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OverflowError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return {"input_id": result.input_id, "run_id": result.run_id, "status": "queued"}

    @router.post("/sessions/{session_id}/runs/current/abort", status_code=202)
    async def abort_agent(session_id: str) -> dict[str, bool]:
        agent = dependencies.find_agent(session_id)
        if agent is None or not await agent.abort():
            raise HTTPException(status_code=409, detail="会话没有活动 Run")
        return {"accepted": True}

    @router.post("/sessions/{session_id}/runs/resume")
    async def resume_agent(session_id: str, body: ResumeBody) -> StreamingResponse:
        try:
            async with dependencies.agent_for_run(session_id, body.model_id) as agent:
                if agent.is_running:
                    raise AgentBusyError("会话已有正在运行的 Agent")
                if body.model_id:
                    await dependencies.require_sessions().update(session_id, model_id=body.model_id)
                response = await agent_stream_response(
                    agent,
                    lambda: agent.resume(
                        max_turns=body.max_turns,
                        permission_mode=PermissionMode(body.permission_mode),
                        thinking=body.thinking,
                    ),
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="会话不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (AgentBusyError, AgentResumeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return response

    return router
