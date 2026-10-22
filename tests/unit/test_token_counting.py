from dataclasses import replace

import pytest

from camel_harness.agent.context.token_counting import (
    ContextTokenEstimate,
    estimate_context_overhead_tokens,
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_text_tokens,
)
from camel_harness.agent.session import ContextNode, ContextSnapshot
from camel_harness.llm import (
    AssistantMessage,
    LlmModel,
    LlmStopReason,
    LlmUsage,
    Message,
    ReasoningBlock,
    TextBlock,
    ToolDefinition,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)

_MODEL = LlmModel("fake", "fake-model")
_ANSWER = AssistantMessage(
    (TextBlock("已确认"),),
    LlmStopReason.STOP,
    provider=_MODEL.provider,
    model=_MODEL.model,
    usage=LlmUsage(1200, 80, cache_read_tokens=900, cache_write_tokens=100, reasoning_tokens=40),
)


def _snapshot(*messages: Message, usage_start_index: int = 0) -> ContextSnapshot:
    return ContextSnapshot(
        nodes=tuple(ContextNode(str(index), message) for index, message in enumerate(messages)),
        usage_start_index=usage_start_index,
    )


@pytest.mark.parametrize(
    ("text", "expected_tokens"), [("", 0), ("abcd", 2), ("中文", 2), ("中文abc", 3)]
)
def test_text_estimation_uses_utf8_bytes(text: str, expected_tokens: int) -> None:
    assert estimate_text_tokens(text) == expected_tokens


def test_usage_covers_response_and_overhead_without_recounting_cache_or_reasoning() -> None:
    snapshot = _snapshot(UserMessage("问题" * 3000), _ANSWER)
    assert estimate_context_tokens(snapshot, _MODEL, context_overhead_tokens=500) == (
        ContextTokenEstimate(1280, 1280, 0, 1)
    )


def test_latest_usage_replaces_earlier_usage_and_only_adds_trailing_messages() -> None:
    latest_answer = replace(_ANSWER, usage=LlmUsage(2000, 100))
    tool_result = ToolResultMessage("call", "read_file", "工具返回正文")
    user_message = UserMessage("继续分析")
    snapshot = _snapshot(UserMessage("开始"), _ANSWER, latest_answer, tool_result, user_message)
    trailing_tokens = estimate_message_tokens(tool_result) + estimate_message_tokens(user_message)
    assert estimate_context_tokens(snapshot, _MODEL, context_overhead_tokens=500) == (
        ContextTokenEstimate(2100 + trailing_tokens, 2100, trailing_tokens, 2)
    )


@pytest.mark.parametrize(
    "invalid_answer",
    [
        replace(_ANSWER, usage=None),
        replace(_ANSWER, usage=LlmUsage()),
        replace(_ANSWER, usage=LlmUsage(input_tokens=100)),
        replace(_ANSWER, usage=LlmUsage(output_tokens=10)),
        replace(_ANSWER, usage=LlmUsage(0, 0, cache_read_tokens=100)),
        replace(_ANSWER, provider="other"),
        replace(_ANSWER, model="other"),
        replace(_ANSWER, provider=None),
        replace(_ANSWER, model=None),
    ],
)
def test_invalid_usage_falls_back_or_uses_earlier_valid_response(
    invalid_answer: AssistantMessage,
) -> None:
    messages = (UserMessage("当前任务"), invalid_answer)
    full_tokens = 500 + sum(estimate_message_tokens(message) for message in messages)
    assert estimate_context_tokens(_snapshot(*messages), _MODEL, context_overhead_tokens=500) == (
        ContextTokenEstimate(full_tokens, 0, full_tokens, None)
    )
    trailing_tokens = estimate_message_tokens(invalid_answer)
    assert estimate_context_tokens(
        _snapshot(UserMessage("任务"), _ANSWER, invalid_answer), _MODEL
    ) == ContextTokenEstimate(1280 + trailing_tokens, 1280, trailing_tokens, 1)


@pytest.mark.parametrize("usage", [LlmUsage(100, 0), LlmUsage(0, 10)])
def test_known_zero_component_is_not_missing_usage(usage: LlmUsage) -> None:
    estimate = estimate_context_tokens(_snapshot(replace(_ANSWER, usage=usage)), _MODEL)
    assert estimate.total_tokens == usage.total_tokens
    assert estimate.last_usage_index == 0


def test_compaction_boundary_excludes_old_usage_until_a_new_response() -> None:
    summary = UserMessage("[Compacted History]\n\n历史摘要")
    snapshot = _snapshot(summary, _ANSWER, UserMessage("继续"), usage_start_index=2)
    full_tokens = 50 + sum(estimate_message_tokens(message) for message in snapshot.messages)
    assert estimate_context_tokens(snapshot, _MODEL, context_overhead_tokens=50) == (
        ContextTokenEstimate(full_tokens, 0, full_tokens, None)
    )
    new_answer = replace(_ANSWER, usage=LlmUsage(300, 50))
    snapshot = _snapshot(*snapshot.messages, new_answer, usage_start_index=2)
    assert estimate_context_tokens(snapshot, _MODEL, context_overhead_tokens=50) == (
        ContextTokenEstimate(350, 350, 0, 3)
    )


def test_fallback_counts_prompt_tool_schema_and_model_visible_history() -> None:
    tool_definitions = (
        ToolDefinition(
            "lookup", "查找资料", {"type": "object", "properties": {"path": {"type": "string"}}}
        ),
    )
    snapshot = _snapshot(UserMessage("问题"), replace(_ANSWER, usage=None))
    overhead = estimate_context_overhead_tokens(
        "基础提示\n项目规则\nSkills\nMemory", tool_definitions
    )
    estimate = estimate_context_tokens(snapshot, _MODEL, context_overhead_tokens=overhead)
    assert overhead > estimate_context_overhead_tokens("基础提示", tool_definitions)
    assert overhead > estimate_context_overhead_tokens("基础提示\n项目规则\nSkills\nMemory")
    assert estimate == ContextTokenEstimate(
        overhead + sum(estimate_message_tokens(message) for message in snapshot.messages),
        0,
        overhead + sum(estimate_message_tokens(message) for message in snapshot.messages),
        None,
    )
    assert estimate_context_tokens(ContextSnapshot(), _MODEL, context_overhead_tokens=overhead) == (
        ContextTokenEstimate(overhead, 0, overhead, None)
    )


def test_message_estimation_only_counts_model_visible_content() -> None:
    visible_answer = replace(_ANSWER, usage=None)
    audited_answer = replace(
        _ANSWER,
        content=(ReasoningBlock("展示推理" * 1000), *visible_answer.content),
        response_model="响应审计模型",
    )
    assert estimate_message_tokens(audited_answer) == estimate_message_tokens(visible_answer)
    visible_result = ToolResultMessage("call", "lookup", "正文")
    assert estimate_message_tokens(visible_result) == estimate_message_tokens(
        replace(visible_result, details={"private": "审计信息" * 1000})
    )
    tool_answer = replace(
        visible_answer,
        content=(*visible_answer.content, ToolUseBlock("call", "lookup", {"path": "目录" * 100})),
        stop_reason=LlmStopReason.TOOL_USE,
    )
    assert estimate_message_tokens(tool_answer) > estimate_message_tokens(visible_answer)
