from __future__ import annotations

import json
from asyncio import FIRST_COMPLETED, Queue, Task, create_task, gather, wait
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict

from camel_harness import Agent, AgentEvent
from camel_harness.agent.events import MessageEndEvent, TurnEndEvent
from camel_harness.agent.session import message_to_dict


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


async def open_agent_sse(
    agent: Agent,
    operation: Callable[[], Awaitable[None]],
) -> AsyncIterator[str]:
    """启动 Agent 并在首事件就绪后返回 SSE 迭代器。"""

    queue: Queue[AgentEvent] = Queue(maxsize=256)

    async def enqueue(event: AgentEvent) -> None:
        await queue.put(event)

    async def execute() -> None:
        await operation()

    unsubscribe = agent.subscribe(enqueue)
    operation_task: Task[None] = create_task(execute())
    first_get: Task[AgentEvent] = create_task(queue.get())
    done, _ = await wait(
        {operation_task, first_get},
        return_when=FIRST_COMPLETED,
    )
    if first_get not in done:
        first_get.cancel()
        await gather(first_get, return_exceptions=True)
        unsubscribe()
        await operation_task
        raise RuntimeError("Agent 未产生起始事件")
    first_event = first_get.result()

    async def drain() -> AsyncIterator[str]:
        pending_get: Task[AgentEvent] | None = None
        received_terminal = False
        event = first_event
        try:
            while True:
                if event.type == "agent_end":
                    received_terminal = True
                yield encode_sse(event)
                if received_terminal:
                    await operation_task
                    return
                if operation_task.done() and queue.empty():
                    await operation_task
                    return
                pending_get = create_task(queue.get())
                done, _ = await wait(
                    {operation_task, pending_get},
                    return_when=FIRST_COMPLETED,
                )
                if pending_get in done:
                    event = pending_get.result()
                    pending_get = None
                else:
                    pending_get.cancel()
                    await gather(pending_get, return_exceptions=True)
                    pending_get = None
                    await operation_task
        finally:
            unsubscribe()
            if pending_get is not None and not pending_get.done():
                pending_get.cancel()
                await gather(pending_get, return_exceptions=True)
            if not received_terminal and not operation_task.done():
                await agent.abort()
            await gather(operation_task, return_exceptions=True)

    return drain()
