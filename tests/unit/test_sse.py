import json

from backend.agent import AgentStreamEvent, AgentStreamEventType
from backend.api.sse import encode_sse, stream_sse


def _event(sequence: int, text: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        type=AgentStreamEventType.TEXT_DELTA,
        session_id="session-1",
        turn_id="turn-1",
        step_id="step-1",
        sequence=sequence,
        source="fake",
        data={"text": text},
    )


def test_encode_sse_preserves_event_identity_and_payload() -> None:
    frame = encode_sse(_event(3, "你好"))

    lines = frame.splitlines()
    assert lines[:2] == ["id: 3", "event: text_delta"]
    payload = json.loads(lines[2].removeprefix("data: "))
    assert payload["session_id"] == "session-1"
    assert payload["sequence"] == 3
    assert payload["data"] == {"text": "你好"}
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
