from dataclasses import FrozenInstanceError, asdict, fields
from typing import get_args

import pytest

from camel_harness.llm import (
    AssistantMessageAccumulator,
    LlmErrorCode,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    LlmUsage,
    MessageCompleted,
    ReasoningBlock,
    ReasoningDelta,
    StreamFailed,
    StreamStarted,
    TextBlock,
    TextDelta,
    ToolCallCompleted,
    ToolUseBlock,
    UsageUpdated,
)


def test_llm_event_types_do_not_contain_agent_runtime_events() -> None:
    """审批和工具结果属于 Agent Runtime，不得重新进入 LLM 协议。"""

    values = {event_type.value for event_type in LlmStreamEventType}
    assert "approval_requested" not in values
    assert "tool_result" not in values
    assert "stream_cancelled" not in values


def test_llm_events_are_concrete_frozen_discriminated_types() -> None:
    """每种事件固定自己的判别值和有效载荷，不开放通用 data 容器。"""
    variants = get_args(LlmStreamEvent)
    assert len(variants) == 9
    assert {cls.__dataclass_fields__["type"].default for cls in variants} == {
        item.value for item in LlmStreamEventType
    }
    for cls in variants:
        assert not cls.__dataclass_fields__["type"].init
        assert not {"data", "session_id", "run_id", "turn_id", "sequence"} & {
            item.name for item in fields(cls)
        }
    event = TextDelta("你好")
    assert asdict(event) == {"type": "text_delta", "text": "你好"}
    with pytest.raises(FrozenInstanceError):
        event.text = "覆盖"
    with pytest.raises(TypeError):
        TextDelta("你好", type="stream_failed")


def test_tool_and_usage_events_reuse_llm_value_types() -> None:
    call = ToolUseBlock("call", "read_file", {"path": "README.md"})
    assert ToolCallCompleted(call).tool_call is call
    usage = LlmUsage(input_tokens=10, output_tokens=5)
    assert UsageUpdated(usage).usage is usage
    assert usage.merge(LlmUsage(output_tokens=8, cache_read_tokens=3)) == LlmUsage(10, 8, 3)
    assert usage.merge(LlmUsage(input_tokens=4, output_tokens=5)) == usage
    assert LlmUsage(input_tokens=0).merge(LlmUsage()).input_tokens == 0


@pytest.mark.parametrize("invalid", [-1, True, 1.5])
def test_usage_rejects_invalid_token_counts(invalid: object) -> None:
    with pytest.raises(ValueError, match="非负整数"):
        LlmUsage(input_tokens=invalid)


def test_completion_requires_normalized_stop_reason() -> None:
    assert MessageCompleted(LlmStopReason.STOP).stop_reason is LlmStopReason.STOP
    with pytest.raises(ValueError, match="LlmStopReason"):
        MessageCompleted("stop")


def test_stream_failure_requires_normalized_error_code() -> None:
    assert (
        StreamFailed("超限", code=LlmErrorCode.CONTEXT_OVERFLOW).code
        is LlmErrorCode.CONTEXT_OVERFLOW
    )
    with pytest.raises(ValueError, match="LlmErrorCode"):
        StreamFailed("超限", code="context_overflow")


def test_accumulator_assembles_canonical_message_and_cumulative_usage() -> None:
    accumulator = AssistantMessageAccumulator("anthropic", "requested")
    call = ToolUseBlock("call", "read_file", {"path": "README.md"})
    for event in (
        StreamStarted("actual"),
        ReasoningDelta("先"),
        ReasoningDelta("分析"),
        TextDelta("你好"),
        TextDelta("世界"),
        ToolCallCompleted(call),
        UsageUpdated(LlmUsage(input_tokens=10, output_tokens=2)),
        UsageUpdated(LlmUsage(output_tokens=5)),
        MessageCompleted(LlmStopReason.TOOL_USE),
    ):
        accumulator.feed(event)
    message = accumulator.finish()
    assert message.content == (ReasoningBlock("先分析"), TextBlock("你好世界"), call)
    assert message.model == "requested"
    assert message.response_model == "actual"
    assert message.provider == "anthropic"
    assert message.usage == LlmUsage(input_tokens=10, output_tokens=5)


@pytest.mark.parametrize("scenario", ["missing_end", "missing_call", "unexpected_call", "failure"])
def test_accumulator_rejects_incomplete_or_inconsistent_message(scenario) -> None:
    accumulator = AssistantMessageAccumulator("fake", "model")
    accumulator.feed(TextDelta("部分内容"))
    accumulator.feed(UsageUpdated(LlmUsage(output_tokens=3)))
    if scenario == "missing_call":
        accumulator.feed(MessageCompleted(LlmStopReason.TOOL_USE))
    elif scenario == "unexpected_call":
        accumulator.feed(ToolCallCompleted(ToolUseBlock("call", "read_file", {})))
        accumulator.feed(MessageCompleted(LlmStopReason.STOP))
    elif scenario == "failure":
        accumulator.feed(MessageCompleted(LlmStopReason.STOP))
        accumulator.feed(StreamFailed("流失败"))
    with pytest.raises(RuntimeError):
        accumulator.finish()
    assert accumulator.usage == LlmUsage(output_tokens=3)


def test_accumulator_keeps_unknown_usage_and_accepts_truncated_call_for_rejection() -> None:
    accumulator = AssistantMessageAccumulator("fake", "model")
    accumulator.feed(StreamStarted("model"))
    call = ToolUseBlock("call", "write_file", {"_invalid_json": "截断"})
    accumulator.feed(ToolCallCompleted(call))
    accumulator.feed(MessageCompleted(LlmStopReason.LENGTH))
    message = accumulator.finish()
    assert message.content == (call,)
    assert message.stop_reason is LlmStopReason.LENGTH
    assert message.usage is None
    assert message.response_model is None
