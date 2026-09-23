from collections.abc import Awaitable, Callable

from fastapi.responses import StreamingResponse

from camel_backend.api.sse import open_agent_sse
from camel_harness import Agent


async def agent_stream_response(
    agent: Agent,
    operation: Callable[[], Awaitable[None]],
) -> StreamingResponse:
    """把公开 Agent 事件流封装为无代理缓冲的 SSE 响应。"""

    events = await open_agent_sse(agent, operation)
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
