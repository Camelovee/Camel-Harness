from __future__ import annotations

import json
from collections.abc import AsyncIterator

from backend.agent import AgentStreamEvent


def encode_sse(event: AgentStreamEvent) -> str:
    """把公开 Agent 事件编码为一个标准 SSE frame。"""

    payload = {
        "type": event.type.value,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "step_id": event.step_id,
        "sequence": event.sequence,
        "source": event.source,
        "data": event.data,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.type.value}\ndata: {data}\n\n"


async def stream_sse(events: AsyncIterator[AgentStreamEvent]) -> AsyncIterator[str]:
    """逐事件产出 SSE frame，不在 Trigger 层缓冲模型增量。"""

    async for event in events:
        yield encode_sse(event)
