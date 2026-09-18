from camel_harness.agent.session import message_from_dict, message_to_dict
from camel_harness.llm import AssistantMessage, LlmStopReason, TextBlock, ToolUseBlock


def test_canonical_message_round_trip() -> None:
    message = AssistantMessage(
        (TextBlock("完成"), ToolUseBlock("call-1", "read_file", {"path": "README.md"})),
        LlmStopReason.TOOL_USE,
    )

    assert message_from_dict(message_to_dict(message)) == message
