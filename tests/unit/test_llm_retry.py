from collections.abc import AsyncIterator

from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmErrorCode,
    LlmModel,
    LlmRequest,
    LlmStreamEvent,
    LlmStreamEventType,
    StreamFailed,
    TextDelta,
    UserMessage,
)

_MODEL = LlmModel("fake", "fake-model")


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
    request = LlmRequest(_MODEL, (UserMessage("hi"),))
    events = [event async for event in service.stream(request)]
    assert inner.calls == 2
    assert events[0].text == "ok"


async def test_failure_event_before_content_is_hidden_when_retry_succeeds() -> None:
    inner = FailedEventAdapter()
    service = LlmClient(inner)
    request = LlmRequest(_MODEL, (UserMessage("hi"),))

    events = [event async for event in service.stream(request)]

    assert inner.calls == 2
    assert [event.type for event in events] == [LlmStreamEventType.TEXT_DELTA]


async def test_permanent_failure_is_delivered_once_without_retry():
    class PermanentAdapter(FailedEventAdapter):
        async def stream(self, request):
            self.calls += 1
            yield StreamFailed("认证失败")

    adapter = PermanentAdapter()
    events = [event async for event in LlmClient(adapter).stream(LlmRequest(_MODEL, ()))]
    assert adapter.calls == 1
    assert events == [StreamFailed("认证失败")]


async def test_failure_after_content_never_retries():
    class PartialAdapter(FailedEventAdapter):
        async def stream(self, request):
            self.calls += 1
            yield TextDelta("部分回答")
            yield StreamFailed("断开", retryable=True)

    adapter = PartialAdapter()
    events = [event async for event in LlmClient(adapter).stream(LlmRequest(_MODEL, ()))]
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
    events = [event async for event in LlmClient(adapter).stream(LlmRequest(_MODEL, ()))]
    assert adapter.calls == 3
    assert events == [StreamFailed("暂时失败", retryable=True)]


async def test_context_overflow_never_retries_unchanged_request_even_if_marked_transient():
    class OverflowAdapter(FailedEventAdapter):
        async def stream(self, request):
            self.calls += 1
            yield StreamFailed("上下文超限", retryable=True, code=LlmErrorCode.CONTEXT_OVERFLOW)

    adapter = OverflowAdapter()
    events = [event async for event in LlmClient(adapter).stream(LlmRequest(_MODEL, ()))]
    assert adapter.calls == 1
    assert events == [
        StreamFailed("上下文超限", retryable=True, code=LlmErrorCode.CONTEXT_OVERFLOW)
    ]


class _MarkerAdapter(LlmAdapter):
    def __init__(self, provider: str) -> None:
        self._provider = provider
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return self._provider

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        yield TextDelta(f"来自{self._provider}")


async def test_routes_by_request_provider() -> None:
    first, second = _MarkerAdapter("a"), _MarkerAdapter("b")
    service = LlmClient({"a": first, "b": second})

    events = [
        event
        async for event in service.stream(LlmRequest(LlmModel("b", "m-x"), (UserMessage("hi"),)))
    ]

    assert events == [TextDelta("来自b")]
    assert not first.requests
    assert second.requests[0].model.model == "m-x"


async def test_unknown_provider_fails_before_any_event() -> None:
    service = LlmClient(_MarkerAdapter("a"))

    events = [
        event
        async for event in service.stream(LlmRequest(LlmModel("b", "m"), (UserMessage("hi"),)))
    ]

    assert len(events) == 1
    assert isinstance(events[0], StreamFailed)
    assert "未配置的 Provider：b" in events[0].error
    assert not events[0].retryable


async def test_single_adapter_normalizes_to_its_provider() -> None:
    adapter = _MarkerAdapter("solo")
    service = LlmClient(adapter)
    assert service.supports("solo")
    assert not service.supports("other")


async def test_empty_adapters_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="至少需要一个"):
        LlmClient({})


async def test_adapter_rejects_missing_api_key_without_network() -> None:
    from camel_harness.llm import create_anthropic_adapter, create_openai_adapter

    for adapter in (create_anthropic_adapter(), create_openai_adapter()):
        events = [
            event
            async for event in adapter.stream(
                LlmRequest(LlmModel(adapter.provider_id, "m"), (UserMessage("hi"),))
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamFailed)
        assert "API Key" in events[0].error
        assert not events[0].retryable
