from collections.abc import AsyncIterator

from backend.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStreamEvent,
    LlmStreamEventType,
)
from backend.types import Message, MessageRole


class FlakyAdapter(LlmAdapter):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("临时断开")
        yield LlmStreamEvent(
            LlmStreamEventType.TEXT_DELTA,
            request.session_id,
            request.turn_id,
            request.step_id,
            1,
            "fake",
            {"text": "ok"},
        )


class FailedEventAdapter(LlmAdapter):
    """模拟 Adapter 在首个有效增量前发送失败事件。"""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield LlmStreamEvent(
                LlmStreamEventType.STREAM_FAILED,
                request.session_id,
                request.turn_id,
                request.step_id,
                1,
                "fake",
                {"error": "临时断开"},
            )
            raise ConnectionError("临时断开")
        yield LlmStreamEvent(
            LlmStreamEventType.TEXT_DELTA,
            request.session_id,
            request.turn_id,
            request.step_id,
            1,
            "fake",
            {"text": "ok"},
        )


async def test_retry_only_before_first_event() -> None:
    inner = FlakyAdapter()
    service = LlmClient(inner)
    request = LlmRequest(
        "s", "t", "p", "fake", "fake-model", (Message.text(MessageRole.USER, "hi"),)
    )
    events = [event async for event in service.stream(request)]
    assert inner.calls == 2
    assert events[0].data["text"] == "ok"


async def test_failure_event_before_content_is_hidden_when_retry_succeeds() -> None:
    inner = FailedEventAdapter()
    service = LlmClient(inner)
    request = LlmRequest(
        "s", "t", "p", "fake", "fake-model", (Message.text(MessageRole.USER, "hi"),)
    )

    events = [event async for event in service.stream(request)]

    assert inner.calls == 2
    assert [event.type for event in events] == [LlmStreamEventType.TEXT_DELTA]
