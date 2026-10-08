from dataclasses import FrozenInstanceError, asdict, fields
from typing import get_args

import pytest

from camel_harness.llm import (
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    LlmUsage,
    MessageCompleted,
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
