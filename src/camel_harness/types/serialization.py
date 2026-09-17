from __future__ import annotations

from typing import Any

from .message import (
    ContentBlock,
    Message,
    MessageRole,
    ReasoningBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def message_to_dict(message: Message) -> dict[str, Any]:
    """把规范消息转换为不含厂商字段的可持久化字典。"""

    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ReasoningBlock):
            blocks.append({"type": "reasoning", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append(
                {
                    "type": "tool_use",
                    "call_id": block.call_id,
                    "name": block.name,
                    "arguments": block.arguments,
                }
            )
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "call_id": block.call_id,
                    "name": block.name,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role.value, "content": blocks}


def message_from_dict(data: dict[str, Any]) -> Message:
    """从持久化字典恢复规范消息。"""

    blocks: list[ContentBlock] = []
    for item in data.get("content", []):
        block_type = item.get("type")
        if block_type == "text":
            blocks.append(TextBlock(text=item["text"]))
        elif block_type == "reasoning":
            blocks.append(ReasoningBlock(text=item["text"]))
        elif block_type == "tool_use":
            blocks.append(
                ToolUseBlock(
                    call_id=item["call_id"], name=item["name"], arguments=item["arguments"]
                )
            )
        elif block_type == "tool_result":
            blocks.append(
                ToolResultBlock(
                    call_id=item["call_id"],
                    name=item["name"],
                    content=item["content"],
                    is_error=item.get("is_error", False),
                )
            )
        else:
            raise ValueError(f"未知规范内容块：{block_type}")
    return Message(role=MessageRole(data["role"]), content=tuple(blocks))
