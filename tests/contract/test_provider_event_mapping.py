from collections.abc import AsyncIterator
from types import SimpleNamespace

from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    LlmUsage,
    MessageCompleted,
    ReasoningBlock,
    StreamStarted,
    TextBlock,
    TextDelta,
    ToolResultMessage,
    ToolUseBlock,
    UsageUpdated,
    UserMessage,
)
from camel_harness.llm.anthropic import AnthropicLlmAdapter
from camel_harness.llm.openai import OpenAiLlmAdapter

_MODEL = LlmModel("fake", "fake-model")


class _FakeAdapter(LlmAdapter):
    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield MessageCompleted(LlmStopReason.STOP, model=None)


def test_vendor_adapters_share_llm_adapter_base() -> None:
    """厂商实现统一遵守 LLM Adapter 契约。"""

    assert issubclass(AnthropicLlmAdapter, LlmAdapter)
    assert issubclass(OpenAiLlmAdapter, LlmAdapter)


async def test_llm_service_delegates_request_to_adapter() -> None:
    service = LlmClient(_FakeAdapter())
    request = LlmRequest(_MODEL, (UserMessage("hi"),))

    events = [event async for event in service.stream(request)]

    assert events[0].type == LlmStreamEventType.MESSAGE_COMPLETED
    assert service.supports("fake")
    assert not service.supports("openai")


def test_anthropic_maps_text_and_tool_call() -> None:
    blocks: dict[int, dict[str, str]] = {}
    start = SimpleNamespace(
        type="content_block_start",
        index=0,
        content_block=SimpleNamespace(type="tool_use", id="call-1", name="read_file"),
    )
    delta = SimpleNamespace(
        type="content_block_delta",
        index=0,
        delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":"README.md"}'),
    )
    stop = SimpleNamespace(type="content_block_stop", index=0)

    assert (
        AnthropicLlmAdapter._map_event(start, blocks, {})[0].type
        == LlmStreamEventType.TOOL_CALL_STARTED
    )
    assert AnthropicLlmAdapter._map_event(delta, blocks, {})[0].type == (
        LlmStreamEventType.TOOL_CALL_ARGUMENTS_DELTA
    )
    completed = AnthropicLlmAdapter._map_event(stop, blocks, {})[0]
    assert completed.type == LlmStreamEventType.TOOL_CALL_COMPLETED
    assert completed.tool_call.arguments == {"path": "README.md"}


def test_anthropic_exposes_input_tokens_as_usage_event() -> None:
    start = SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(usage=SimpleNamespace(input_tokens=42)),
    )

    events = AnthropicLlmAdapter._map_event(start, {}, {})

    assert events == [
        StreamStarted(),
        UsageUpdated(LlmUsage(**{"input_tokens": 42})),
    ]


def test_openai_maps_text_and_tool_call() -> None:
    calls: dict[str, dict[str, str]] = {}
    text = SimpleNamespace(type="response.output_text.delta", delta="你好")
    start = SimpleNamespace(
        type="response.output_item.added",
        item=SimpleNamespace(type="function_call", id="item-1", call_id="call-1", name="read_file"),
    )
    done = SimpleNamespace(
        type="response.function_call_arguments.done",
        item_id="item-1",
        name="read_file",
        arguments='{"path":"README.md"}',
    )

    completion_state = {"has_tool_call": False}
    assert OpenAiLlmAdapter._map_event(text, calls, completion_state) == [TextDelta(text="你好")]
    assert (
        OpenAiLlmAdapter._map_event(start, calls, completion_state)[0].type
        == LlmStreamEventType.TOOL_CALL_STARTED
    )
    completed = OpenAiLlmAdapter._map_event(done, calls, completion_state)[0]
    assert completed.type == LlmStreamEventType.TOOL_CALL_COMPLETED
    assert completed.tool_call.arguments == {"path": "README.md"}


def test_anthropic_normalizes_stop_reason() -> None:
    completion: dict[str, LlmStopReason] = {}
    delta = SimpleNamespace(
        type="message_delta",
        usage=SimpleNamespace(output_tokens=10),
        delta=SimpleNamespace(stop_reason="max_tokens"),
    )
    stop = SimpleNamespace(type="message_stop")

    AnthropicLlmAdapter._map_event(delta, {}, completion)
    completed = AnthropicLlmAdapter._map_event(stop, {}, completion)

    assert completed == [MessageCompleted(LlmStopReason.LENGTH, model=None)]


def test_openai_normalizes_stop_reason() -> None:
    completed_response = SimpleNamespace(usage=None)
    completed = SimpleNamespace(type="response.completed", response=completed_response)
    incomplete = SimpleNamespace(
        type="response.incomplete",
        response=SimpleNamespace(
            usage=None,
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        ),
    )

    stopped = OpenAiLlmAdapter._map_event(completed, {}, {"has_tool_call": False})
    tool_use = OpenAiLlmAdapter._map_event(completed, {}, {"has_tool_call": True})
    length = OpenAiLlmAdapter._map_event(incomplete, {}, {"has_tool_call": False})

    assert stopped[-1].stop_reason == LlmStopReason.STOP.value
    assert tool_use[-1].stop_reason == LlmStopReason.TOOL_USE.value
    assert length[-1].stop_reason == LlmStopReason.LENGTH.value


def test_anthropic_groups_consecutive_tool_results() -> None:
    assistant = AssistantMessage(
        (
            ToolUseBlock("call-1", "read_file", {"path": "README.md"}),
            ToolUseBlock("call-2", "list_files", {"path": "."}),
        ),
        LlmStopReason.TOOL_USE,
    )
    first = ToolResultMessage("call-1", "read_file", "first")
    second = ToolResultMessage("call-2", "list_files", "second")

    messages = AnthropicLlmAdapter._convert_messages((assistant, first, second))

    assert messages == [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                },
                {"type": "tool_use", "id": "call-2", "name": "list_files", "input": {"path": "."}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "first",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call-2",
                    "content": "second",
                    "is_error": False,
                },
            ],
        },
    ]


def test_provider_requests_do_not_replay_reasoning_as_conversation_text() -> None:
    """思考块可供 UI 回放，但不得伪装成普通 assistant 文本进入下一轮。"""

    assistant = AssistantMessage(
        (ReasoningBlock("仅用于界面展示"), TextBlock("对用户的回答")),
        LlmStopReason.STOP,
        provider="private-provider",
        model="private-model",
        usage=LlmUsage(10, 5),
    )
    tool = ToolResultMessage(
        "call", "read_file", "内容", details={"content_ref": {"path": "/private/artifact"}}
    )

    openai_items = OpenAiLlmAdapter._convert_messages((assistant,))
    anthropic_messages = AnthropicLlmAdapter._convert_messages((assistant,))

    assert openai_items == [{"role": "assistant", "content": "对用户的回答"}]
    assert anthropic_messages == [
        {"role": "assistant", "content": [{"type": "text", "text": "对用户的回答"}]}
    ]
    assert OpenAiLlmAdapter._convert_messages((tool,)) == [
        {"type": "function_call_output", "call_id": "call", "output": "内容"}
    ]
    assert AnthropicLlmAdapter._convert_messages((tool,)) == [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call", "content": "内容", "is_error": False}
            ],
        }
    ]


def test_anthropic_usage_includes_cache_without_double_counting() -> None:
    event = SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(
            model="actual-model",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=0,
                cache_read_input_tokens=50,
                cache_creation_input_tokens=20,
            ),
        ),
    )
    events = AnthropicLlmAdapter._map_event(event, {}, {})
    assert events[0] == StreamStarted(model="actual-model")
    assert events[1].usage == LlmUsage(
        **{
            "input_tokens": 80,
            "output_tokens": 0,
            "cache_read_tokens": 50,
            "cache_write_tokens": 20,
        }
    )


def test_openai_usage_preserves_total_input_and_cache_reasoning_details() -> None:
    event = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            model="actual-model",
            usage=SimpleNamespace(
                input_tokens=80,
                output_tokens=15,
                input_tokens_details=SimpleNamespace(cached_tokens=50, cache_write_tokens=0),
                output_tokens_details=SimpleNamespace(reasoning_tokens=5),
            ),
        ),
    )
    events = OpenAiLlmAdapter._map_event(event, {}, {"has_tool_call": False})
    assert events[0].usage == LlmUsage(
        **{
            "input_tokens": 80,
            "output_tokens": 15,
            "cache_read_tokens": 50,
            "cache_write_tokens": 0,
            "reasoning_tokens": 5,
        }
    )
    assert events[-1] == MessageCompleted(LlmStopReason.STOP, model="actual-model")


def test_openai_missing_usage_fields_are_not_reported_as_zero() -> None:
    event = SimpleNamespace(
        type="response.completed", response=SimpleNamespace(usage=SimpleNamespace(input_tokens=5))
    )
    events = OpenAiLlmAdapter._map_event(event, {}, {"has_tool_call": False})
    assert events[0].usage == LlmUsage(**{"input_tokens": 5})


def test_anthropic_final_usage_merges_partial_cumulative_fields() -> None:
    state: dict[str, int] = {}
    first = AnthropicLlmAdapter._map_usage(
        SimpleNamespace(
            input_tokens=10,
            cache_read_input_tokens=50,
            cache_creation_input_tokens=20,
        ),
        state,
    )
    assert first["input_tokens"] == 80
    delta = SimpleNamespace(
        type="message_delta",
        delta=SimpleNamespace(stop_reason="end_turn"),
        usage=SimpleNamespace(
            input_tokens=15,
            output_tokens=30,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
            output_tokens_details=SimpleNamespace(thinking_tokens=5),
        ),
    )
    for _ in range(2):
        result = AnthropicLlmAdapter._map_event(delta, {}, {}, state)[0].usage
        assert result == LlmUsage(
            **{
                "input_tokens": 85,
                "output_tokens": 30,
                "cache_read_tokens": 50,
                "cache_write_tokens": 20,
                "reasoning_tokens": 5,
            }
        )
    assert AnthropicLlmAdapter._map_usage(SimpleNamespace(), None) == {}
