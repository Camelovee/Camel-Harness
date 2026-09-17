from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStreamEvent,
    LlmStreamEventType,
)
from camel_harness.llm.anthropic import AnthropicLlmAdapter
from camel_harness.llm.openai import OpenAiLlmAdapter
from camel_harness.types import (
    Message,
    MessageRole,
    ReasoningBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class _FakeAdapter(LlmAdapter):
    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            request.session_id,
            request.turn_id,
            request.step_id,
            1,
            self.provider_id,
            {},
        )


def test_vendor_adapters_share_llm_adapter_base() -> None:
    """厂商实现统一遵守 LLM Adapter 契约。"""

    assert issubclass(AnthropicLlmAdapter, LlmAdapter)
    assert issubclass(OpenAiLlmAdapter, LlmAdapter)


async def test_llm_service_delegates_request_to_adapter() -> None:
    service = LlmClient(_FakeAdapter())
    request = LlmRequest(
        "session", "turn", "step", "fake", "fake-model", (Message.text(MessageRole.USER, "hi"),)
    )

    events = [event async for event in service.stream(request)]

    assert events[0].provider == "fake"


async def test_llm_service_rejects_mismatched_provider() -> None:
    service = LlmClient(_FakeAdapter())
    request = LlmRequest(
        "session", "turn", "step", "missing", "model", (Message.text(MessageRole.USER, "hi"),)
    )

    with pytest.raises(ValueError, match="不匹配"):
        _ = [event async for event in service.stream(request)]


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
        AnthropicLlmAdapter._map_event(start, blocks)[0][0] is LlmStreamEventType.TOOL_CALL_STARTED
    )
    assert AnthropicLlmAdapter._map_event(delta, blocks)[0][0] is (
        LlmStreamEventType.TOOL_CALL_ARGUMENTS_DELTA
    )
    completed = AnthropicLlmAdapter._map_event(stop, blocks)[0]
    assert completed[0] is LlmStreamEventType.TOOL_CALL_COMPLETED
    assert completed[1]["arguments"] == {"path": "README.md"}


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

    assert OpenAiLlmAdapter._map_event(text, calls) == [
        (LlmStreamEventType.TEXT_DELTA, {"text": "你好"})
    ]
    assert OpenAiLlmAdapter._map_event(start, calls)[0][0] is LlmStreamEventType.TOOL_CALL_STARTED
    completed = OpenAiLlmAdapter._map_event(done, calls)[0]
    assert completed[0] is LlmStreamEventType.TOOL_CALL_COMPLETED
    assert completed[1]["arguments"] == {"path": "README.md"}


def test_anthropic_groups_consecutive_tool_results() -> None:
    assistant = Message(
        MessageRole.ASSISTANT,
        (
            ToolUseBlock("call-1", "read_file", {"path": "README.md"}),
            ToolUseBlock("call-2", "list_files", {"path": "."}),
        ),
    )
    first = Message(MessageRole.TOOL, (ToolResultBlock("call-1", "read_file", "first"),))
    second = Message(MessageRole.TOOL, (ToolResultBlock("call-2", "list_files", "second"),))

    system, messages = AnthropicLlmAdapter._convert_messages((assistant, first, second))

    assert system == ""
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
            "content": (
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
            ),
        },
    ]


def test_provider_requests_do_not_replay_reasoning_as_conversation_text() -> None:
    """思考块可供 UI 回放，但不得伪装成普通 assistant 文本进入下一轮。"""

    assistant = Message(
        MessageRole.ASSISTANT,
        (ReasoningBlock("仅用于界面展示"), TextBlock("对用户的回答")),
    )

    _, openai_items = OpenAiLlmAdapter._convert_messages((assistant,))
    _, anthropic_messages = AnthropicLlmAdapter._convert_messages((assistant,))

    assert openai_items == [{"role": "assistant", "content": "对用户的回答"}]
    assert anthropic_messages == [
        {"role": "assistant", "content": [{"type": "text", "text": "对用户的回答"}]}
    ]
