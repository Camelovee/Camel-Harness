import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import fields
from typing import cast

from camel_backend.api.sse import encode_sse, open_agent_sse, stream_sse
from camel_harness import Agent, AgentEvent
from camel_harness.agent.events import AgentEventBase, MessageUpdateEvent
from camel_harness.llm import TextDelta


def _event(sequence: int, text: str) -> MessageUpdateEvent:
    return MessageUpdateEvent(
        session_id="session-1",
        run_id="run-1",
        sequence=sequence,
        turn_id="turn-1",
        message_id="message-1",
        assistant_event=TextDelta(text=text),
    )


def test_encode_sse_preserves_event_identity_and_payload() -> None:
    frame = encode_sse(_event(3, "你好"))

    lines = frame.splitlines()
    assert lines[:2] == ["id: 3", "event: message_update"]
    payload = json.loads(lines[2].removeprefix("data: "))
    assert payload["session_id"] == "session-1"
    assert payload["sequence"] == 3
    assert payload["run_id"] == "run-1"
    assert payload["assistant_event"] == {"type": "text_delta", "text": "你好"}
    assert frame.endswith("\n\n")


def test_agent_event_inherits_common_coordinates_without_changing_payload() -> None:
    event = _event(3, "你好")

    assert isinstance(event, AgentEventBase)
    assert [item.name for item in fields(event)] == [
        "session_id",
        "run_id",
        "sequence",
        "turn_id",
        "message_id",
        "assistant_event",
        "type",
    ]


async def test_stream_sse_does_not_buffer_events() -> None:
    released_second = False

    async def events():
        nonlocal released_second
        yield _event(1, "你")
        released_second = True
        yield _event(2, "好")

    stream = stream_sse(events())

    first = await anext(stream)

    assert "data:" in first
    assert released_second is False
    assert len([frame async for frame in stream]) == 1


class FakeAgent:
    """只实现 SSE 桥接所需的订阅和取消表面。"""

    def __init__(self) -> None:
        self.listener: Callable[[AgentEvent], Awaitable[None] | None] | None = None
        self.cancelled = asyncio.Event()

    def subscribe(
        self,
        listener: Callable[[AgentEvent], Awaitable[None] | None],
    ) -> Callable[[], None]:
        self.listener = listener

        def unsubscribe() -> None:
            self.listener = None

        return unsubscribe

    async def emit(self, event: AgentEvent) -> None:
        assert self.listener is not None
        result = self.listener(event)
        if result is not None:
            await result

    async def abort(self) -> bool:
        self.cancelled.set()
        return True


async def test_agent_sse_disconnect_explicitly_aborts_run() -> None:
    fake = FakeAgent()

    async def operation() -> None:
        await fake.emit(_event(1, "开始"))
        await fake.cancelled.wait()

    stream = await open_agent_sse(cast(Agent, fake), operation)
    assert "开始" in await anext(stream)

    await stream.aclose()

    assert fake.cancelled.is_set()
    assert fake.listener is None


async def test_nested_child_terminal_does_not_close_parent_sse() -> None:
    from camel_harness.agent.events import AgentEndEvent, SubagentEvent
    from camel_harness.agent.state import StopReason

    agent = FakeAgent()
    child_end = SubagentEvent(
        session_id="session-1",
        run_id="run-1",
        sequence=1,
        turn_id="turn-1",
        call_id="delegate",
        agent_type="explore",
        event={
            "type": "agent_end",
            "session_id": "child",
            "run_id": "child-run",
            "sequence": 20,
            "status": "completed",
            "stop_reason": "model_completed",
            "total_tokens": 10,
            "error": None,
        },
    )

    async def operation() -> None:
        await agent.emit(child_end)
        await agent.emit(_event(2, "父任务继续"))
        await agent.emit(
            AgentEndEvent(
                session_id="session-1",
                run_id="run-1",
                sequence=3,
                status="completed",
                stop_reason=StopReason.MODEL_COMPLETED,
                total_tokens=20,
            )
        )

    stream = await open_agent_sse(cast(Agent, agent), operation)
    frames = [frame async for frame in stream]
    assert len(frames) == 3
    assert "event: subagent_event" in frames[0]
    assert "父任务继续" in frames[1]
    assert "event: agent_end" in frames[-1]
    assert not agent.cancelled.is_set()
