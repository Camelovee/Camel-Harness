from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from camel_harness import AgentStreamEvent
from camel_harness.backend.api.sse import stream_sse


def agent_stream_response(events: AsyncIterator[AgentStreamEvent]) -> StreamingResponse:
    """把公开 Agent 事件流封装为无代理缓冲的 SSE 响应。"""

    return StreamingResponse(
        stream_sse(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
