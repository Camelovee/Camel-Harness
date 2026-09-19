import json

from camel_backend.api.sse import encode_sse, stream_sse
from camel_harness.agent.events import MessageUpdateEvent
from camel_harness.llm import LlmStreamEvent, LlmStreamEventType


def _event(sequence: int, text: str) -> MessageUpdateEvent:
    return MessageUpdateEvent(
        session_id="session-1",
        run_id="run-1",
        sequence=sequence,
        turn_id="turn-1",
        message_id="message-1",
        assistant_event=LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": text}),
    )


def test_encode_sse_preserves_event_identity_and_payload() -> None:
    frame = encode_sse(_event(3, "你好"))

    lines = frame.splitlines()
    assert lines[:2] == ["id: 3", "event: message_update"]
    payload = json.loads(lines[2].removeprefix("data: "))
    assert payload["session_id"] == "session-1"
    assert payload["sequence"] == 3
    assert payload["run_id"] == "run-1"
    assert payload["assistant_event"] == {"type": "text_delta", "data": {"text": "你好"}}
    assert frame.endswith("\n\n")


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
