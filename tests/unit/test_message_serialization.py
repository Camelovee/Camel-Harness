from camel_harness.agent.session import message_from_dict, message_to_dict
from camel_harness.llm import (
    AssistantMessage,
    LlmStopReason,
    LlmUsage,
    TextBlock,
    ToolResultMessage,
    ToolUseBlock,
)


def test_canonical_message_round_trip() -> None:
    message = AssistantMessage(
        (TextBlock("完成"), ToolUseBlock("call-1", "read_file", {"path": "README.md"})),
        LlmStopReason.TOOL_USE,
    )

    assert message_from_dict(message_to_dict(message)) == message


def test_assistant_audit_round_trip_and_unknown_usage() -> None:
    message = AssistantMessage(
        (TextBlock("完成"),),
        LlmStopReason.STOP,
        provider="openai",
        model="requested",
        response_model="actual",
        usage=LlmUsage(100, 20, cache_read_tokens=30, reasoning_tokens=5),
    )
    payload = message_to_dict(message)
    assert payload["usage"]["total_tokens"] == 120
    assert payload["usage"]["cache_write_tokens"] is None
    assert message_from_dict(payload) == message
    assert LlmUsage(input_tokens=0, output_tokens=0).total_tokens == 0
    assert LlmUsage(input_tokens=10).total_tokens is None


def test_tool_result_details_round_trip() -> None:
    message = ToolResultMessage(
        "call",
        "read_file",
        "预览",
        details={
            "content_ref": {"path": "/sessions/id/tool-results/result.txt", "size_bytes": 123},
            "subagent_session_id": "child",
        },
    )
    assert message_from_dict(message_to_dict(message)) == message
