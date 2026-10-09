from collections.abc import AsyncIterator

from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStreamEvent,
    LlmStreamEventType,
    StreamFailed,
    TextDelta,
    UserMessage,
)


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
        yield TextDelta(text="ok")


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
            yield StreamFailed(error="临时断开", retryable=True)
            return
        yield TextDelta(text="ok")


async def test_retry_only_before_first_event() -> None:
    inner = FlakyAdapter()
    service = LlmClient(inner)
    request = LlmRequest("fake-model", (UserMessage("hi"),))
    events = [event async for event in service.stream(request)]
    assert inner.calls == 2
    assert events[0].text == "ok"


async def test_failure_event_before_content_is_hidden_when_retry_succeeds() -> None:
    inner = FailedEventAdapter()
    service = LlmClient(inner)
    request = LlmRequest("fake-model", (UserMessage("hi"),))

    events = [event async for event in service.stream(request)]

    assert inner.calls == 2
    assert [event.type for event in events] == [LlmStreamEventType.TEXT_DELTA]


async def test_permanent_failure_is_delivered_once_without_retry():
    class PermanentAdapter(FailedEventAdapter):
        async def stream(self, request):
            self.calls += 1
            yield StreamFailed("认证失败")

    adapter = PermanentAdapter()
    events = [event async for event in LlmClient(adapter).stream(LlmRequest("fake", ()))]
    assert adapter.calls == 1
    assert events == [StreamFailed("认证失败")]


async def test_failure_after_content_never_retries():
    class PartialAdapter(FailedEventAdapter):
        async def stream(self, request):
            self.calls += 1
            yield TextDelta("部分回答")
            yield StreamFailed("断开", retryable=True)

    adapter = PartialAdapter()
    events = [event async for event in LlmClient(adapter).stream(LlmRequest("fake", ()))]
    assert adapter.calls == 1
    assert events == [TextDelta("部分回答"), StreamFailed("断开", retryable=True)]


async def test_retry_exhaustion_delivers_last_failure_once(monkeypatch):
    async def no_sleep(delay):
        pass

    monkeypatch.setattr("camel_harness.llm.client.asyncio.sleep", no_sleep)

    class AlwaysFailed(FailedEventAdapter):
        async def stream(self, request):
            self.calls += 1
            yield StreamFailed("暂时失败", retryable=True)

    adapter = AlwaysFailed()
    events = [event async for event in LlmClient(adapter).stream(LlmRequest("fake", ()))]
    assert adapter.calls == 3
    assert events == [StreamFailed("暂时失败", retryable=True)]
