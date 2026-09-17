from backend.types import (
    Message,
    MessageRole,
    TextBlock,
    ToolUseBlock,
    message_from_dict,
    message_to_dict,
)


def test_canonical_message_round_trip() -> None:
    message = Message(
        MessageRole.ASSISTANT,
        (TextBlock("完成"), ToolUseBlock("call-1", "read_file", {"path": "README.md"})),
    )

    assert message_from_dict(message_to_dict(message)) == message
