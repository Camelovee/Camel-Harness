import json
from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmErrorCode,
    LlmModel,
    LlmRequest,
    LlmUsage,
    StreamFailed,
    UsageUpdated,
    UserMessage,
)
from camel_harness.llm.anthropic import AnthropicLlmAdapter
from camel_harness.llm.openai import OpenAiLlmAdapter


def _adapter(provider: str, handler: Callable[[httpx.Request], httpx.Response]) -> LlmAdapter:
    """官方 SDK 通过本地 HTTP Transport 返回错误，不访问外部模型。"""

    def client_factory(api_key: str, base_url: str | None):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        if provider == "anthropic":
            return AsyncAnthropic(api_key=api_key, http_client=http, max_retries=0)
        return AsyncOpenAI(api_key=api_key, http_client=http, max_retries=0)

    if provider == "anthropic":
        return AnthropicLlmAdapter(client_factory)
    return OpenAiLlmAdapter(client_factory)


def _model(provider: str) -> LlmModel:
    return LlmModel(provider, "fake", api_key="test")


@pytest.mark.parametrize(
    "provider, detail",
    [
        ("openai", {"code": "context_length_exceeded", "message": "上下文超限"}),
        (
            "anthropic",
            {"type": "invalid_request_error", "message": "prompt is too long: 201 > 200"},
        ),
        (
            "anthropic",
            {
                "type": "invalid_request_error",
                "message": "input length and `max_tokens` exceed context limit: 201 > 200",
            },
        ),
        ("anthropic", {"code": "context_length_exceeded", "message": "兼容端点超限"}),
    ],
)
async def test_sdk_context_overflow_is_classified_without_retrying_same_input(provider, detail):
    requests = []

    def reject(request):
        requests.append(request)
        return httpx.Response(400, json={"error": detail})

    adapter = _adapter(provider, reject)
    events = [
        event
        async for event in LlmClient(adapter).stream(
            LlmRequest(_model(provider), (UserMessage("任务"),))
        )
    ]

    assert len(requests) == 1
    assert len(events) == 1
    assert isinstance(events[0], StreamFailed)
    assert events[0].code is LlmErrorCode.CONTEXT_OVERFLOW
    assert not events[0].retryable


@pytest.mark.parametrize(
    "provider, status, detail",
    [
        ("openai", 400, {"code": "invalid_request", "message": "context length in bad schema"}),
        ("openai", 401, {"code": "context_length_exceeded", "message": "认证失败"}),
        (
            "anthropic",
            400,
            {"type": "invalid_request_error", "message": "invalid schema: prompt is too long"},
        ),
        (
            "anthropic",
            401,
            {"type": "authentication_error", "message": "prompt is too long"},
        ),
        ("anthropic", 413, {"type": "request_too_large", "message": "request body too large"}),
    ],
)
async def test_unrelated_sdk_errors_cannot_trigger_compaction(provider, status, detail):
    adapter = _adapter(provider, lambda request: httpx.Response(status, json={"error": detail}))
    events = [event async for event in LlmClient(adapter).stream(LlmRequest(_model(provider), ()))]
    assert len(events) == 1
    assert isinstance(events[0], StreamFailed)
    assert events[0].code is None
    assert not events[0].retryable


@pytest.mark.parametrize(
    "provider, event_type",
    [("openai", "response.failed"), ("openai", "error"), ("anthropic", "error")],
)
async def test_sse_failure_is_not_lost_by_sdk_mapping(provider, event_type):
    if provider == "anthropic":
        detail = {"type": "invalid_request_error", "message": "prompt is too long: 201 > 200"}
        payload = {"type": event_type, "error": detail}
    else:
        detail = {"code": "context_length_exceeded", "message": "上下文超限"}
        payload = (
            {"type": event_type, "response": {"error": detail}}
            if event_type == "response.failed"
            else {"type": event_type, **detail}
        )
    content = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
    adapter = _adapter(
        provider,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=content
        ),
    )
    events = [event async for event in adapter.stream(LlmRequest(_model(provider), ()))]
    assert len(events) == 1
    assert isinstance(events[0], StreamFailed)
    assert events[0].code is LlmErrorCode.CONTEXT_OVERFLOW
    assert not events[0].retryable


def test_openai_failed_response_preserves_known_usage_and_generic_error():
    event = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=2),
            error=SimpleNamespace(code="server_error", message="服务失败"),
        ),
    )
    assert OpenAiLlmAdapter._map_event(event, {}, {}) == [
        UsageUpdated(LlmUsage(10, 2)),
        StreamFailed("服务失败"),
    ]


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_sdk_thinking_parameters_require_explicit_model_capability(
    provider: str, explicit: bool
) -> None:
    from camel_harness.llm import LlmThinkingEffort, LlmThinkingMode

    bodies: list[dict[str, object]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "受控测试结束"}})

    mode = LlmThinkingMode.PROVIDER_DEFAULT
    if explicit:
        mode = LlmThinkingMode(
            f"{provider}_{'adaptive' if provider == 'anthropic' else 'reasoning'}"
        )
    model = LlmModel(provider, "fake", api_key="test", thinking_mode=mode)
    events = [
        event
        async for event in _adapter(provider, capture).stream(
            LlmRequest(model, (), max_output_tokens=16384, effort=LlmThinkingEffort.MAX)
        )
    ]
    assert len(events) == 1 and isinstance(events[0], StreamFailed)
    assert len(bodies) == 1
    body = bodies[0]
    assert body["max_tokens" if provider == "anthropic" else "max_output_tokens"] == 16384
    if not explicit:
        assert not {"thinking", "output_config", "reasoning"} & body.keys()
    elif provider == "anthropic":
        assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert body["output_config"] == {"effort": "max"}
    else:
        assert body["reasoning"] == {"effort": "xhigh", "summary": "auto"}
