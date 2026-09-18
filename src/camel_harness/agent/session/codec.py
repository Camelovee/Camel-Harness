from __future__ import annotations

from typing import Any

from camel_harness.llm import (
    AssistantContent,
    AssistantMessage,
    LlmStopReason,
    Message,
    ReasoningBlock,
    TextBlock,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)


def message_to_dict(message: Message) -> dict[str, Any]:
    """把判别消息联合转换为 JSONL 使用的稳定字典。"""

    if isinstance(message, UserMessage):
        return {"role": message.role, "content": message.content}
    if isinstance(message, ToolResultMessage):
        return {
            "role": message.role,
            "tool_call_id": message.call_id,
            "tool_name": message.name,
            "content": message.content,
            "is_error": message.is_error,
        }

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
    return {
        "role": message.role,
        "content": blocks,
        "stop_reason": message.stop_reason.value,
    }


def message_from_dict(data: dict[str, Any]) -> Message:
    """从 JSONL 字典恢复判别消息联合。"""

    role = data.get("role")
    if role == "user":
        return UserMessage(str(data["content"]))
    if role == "toolResult":
        return ToolResultMessage(
            call_id=str(data["tool_call_id"]),
            name=str(data["tool_name"]),
            content=str(data["content"]),
            is_error=bool(data.get("is_error", False)),
        )
    if role != "assistant":
        raise ValueError(f"未知规范消息角色：{role}")

    blocks: list[AssistantContent] = []
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
        else:
            raise ValueError(f"未知 Assistant 内容块：{block_type}")
    return AssistantMessage(tuple(blocks), LlmStopReason(str(data["stop_reason"])))
