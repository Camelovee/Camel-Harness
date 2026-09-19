from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict

from camel_harness import AgentEvent
from camel_harness.agent.events import MessageEndEvent, TurnEndEvent
from camel_harness.agent.session.codec import message_to_dict


def encode_sse(event: AgentEvent) -> str:
    """把公开 Agent 事件编码为一个标准 SSE frame。"""

    payload = asdict(event)
    if isinstance(event, MessageEndEvent):
        payload["message"] = message_to_dict(event.message)
    elif isinstance(event, TurnEndEvent):
        payload["message"] = message_to_dict(event.message)
        payload["tool_results"] = [message_to_dict(item) for item in event.tool_results]
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.type}\ndata: {data}\n\n"


async def stream_sse(events: AsyncIterator[AgentEvent]) -> AsyncIterator[str]:
    """逐事件产出 SSE frame，不在 Trigger 层缓冲模型增量。"""

    async for event in events:
        yield encode_sse(event)
