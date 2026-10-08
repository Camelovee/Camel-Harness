from __future__ import annotations

from dataclasses import asdict
from typing import Any

from camel_harness.llm import (
    AssistantContent,
    AssistantMessage,
    LlmStopReason,
    LlmUsage,
    Message,
    ReasoningBlock,
    TextBlock,
    ToolResultMessage,
    ToolUseBlock,
    UserMessage,
)

from .events import (
    SCHEMA_VERSION,
    SessionCompactionEvent,
    SessionEvent,
    SessionHeaderEvent,
    SessionInfoEvent,
    SessionMessageEvent,
)


def event_to_dict(event: SessionEvent) -> dict[str, Any]:
    """树条目使用 id/parentId；持久化字段映射只存在于 Session。"""
    result: dict[str, Any] = {"type": event.type, "timestamp": event.timestamp}
    if not isinstance(event, SessionHeaderEvent):
        result.update(id=event.id, parentId=event.parent_id)
    if isinstance(event, SessionHeaderEvent):
        result.update(
            version=event.version,
            id=event.session_id,
            cwd=event.workspace,
            name=event.name,
            modelId=event.model_id,
            archived=event.archived,
            parentSessionId=event.parent_session_id,
            parentToolCallId=event.parent_tool_call_id,
        )
    elif isinstance(event, SessionMessageEvent):
        result["message"] = message_to_dict(event.message)
    elif isinstance(event, SessionCompactionEvent):
        result.update(
            summary=event.summary,
            firstKeptEntryId=event.first_kept_entry_id,
            tokensBefore=event.tokens_before,
        )
        if event.usage is not None:
            result["usage"] = asdict(event.usage)
    elif isinstance(event, SessionInfoEvent):
        result.update(
            name=event.name, cwd=event.workspace, modelId=event.model_id, archived=event.archived
        )
    return result


def event_from_dict(data: dict[str, Any]) -> SessionEvent:
    """解析四种持久化事件；不将未知类型或缺失字段伪装成合法记录。"""
    event_type = data.get("type")
    timestamp = data["timestamp"]
    if not isinstance(timestamp, str):
        raise ValueError("会话事件时间戳必须是字符串")
    coordinates: dict[str, Any] = {}
    if event_type != "session":
        if not isinstance(data["id"], str) or not data["id"]:
            raise ValueError("会话条目 ID 必须是非空字符串")
        parent_id = data["parentId"]
        if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
            raise ValueError("会话父条目 ID 必须是非空字符串或空值")
        coordinates = {"id": data["id"], "parent_id": parent_id}
    if event_type in {"session", "session_info"}:
        for key in ("name", "cwd"):
            if not isinstance(data[key], str):
                raise ValueError(f"会话事件 {key} 必须是字符串")
        if type(data.get("archived", False)) is not bool:
            raise ValueError("会话归档状态必须是布尔值")
        if data.get("modelId") is not None and not isinstance(data["modelId"], str):
            raise ValueError("会话模型标识必须是字符串或空值")
    if event_type == "session":
        if type(data["version"]) is not int or data["version"] != SCHEMA_VERSION:
            raise ValueError("会话头格式不受支持")
        if not isinstance(data["id"], str) or not data["id"]:
            raise ValueError("会话标识必须是非空字符串")
        for key in ("parentSessionId", "parentToolCallId"):
            if data.get(key) is not None and not isinstance(data[key], str):
                raise ValueError(f"会话头 {key} 必须是字符串或空值")
        return SessionHeaderEvent(
            timestamp,
            data["id"],
            data["cwd"],
            data["name"],
            data.get("modelId"),
            data.get("archived", False),
            data.get("parentSessionId"),
            data.get("parentToolCallId"),
            data["version"],
        )
    if event_type == "message":
        return SessionMessageEvent(timestamp, message_from_dict(data["message"]), **coordinates)
    if event_type == "compaction":
        if not isinstance(data["summary"], str) or not isinstance(data["firstKeptEntryId"], str):
            raise ValueError("压缩摘要及保留条目 ID 必须是字符串")
        return SessionCompactionEvent(
            timestamp,
            data["summary"],
            data["firstKeptEntryId"],
            data["tokensBefore"],
            LlmUsage(**data["usage"]) if data.get("usage") is not None else None,
            **coordinates,
        )
    if event_type == "session_info":
        return SessionInfoEvent(
            timestamp,
            data["name"],
            data["cwd"],
            data.get("modelId"),
            data["archived"],
            **coordinates,
        )
    raise ValueError(f"未知会话记录类型：{event_type}")


def message_to_dict(message: Message) -> dict[str, Any]:
    """把判别消息联合转换为 JSONL 使用的稳定字典。"""

    if isinstance(message, UserMessage):
        return {"role": message.role, "content": message.content}
    if isinstance(message, ToolResultMessage):
        result = {
            "role": message.role,
            "tool_call_id": message.call_id,
            "tool_name": message.name,
            "content": message.content,
            "is_error": message.is_error,
        }
        if message.details:
            result["details"] = message.details
        return result

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
    result = {
        "role": message.role,
        "content": blocks,
        "stop_reason": message.stop_reason.value,
    }
    for key in ("provider", "model", "response_model"):
        value = getattr(message, key)
        if value is not None:
            result[key] = value
    if message.usage is not None:
        result["usage"] = {**asdict(message.usage), "total_tokens": message.usage.total_tokens}
    return result


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
            details=dict(data.get("details", {})),
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
    usage_data = data.get("usage")
    usage = (
        LlmUsage(**{key: value for key, value in usage_data.items() if key != "total_tokens"})
        if usage_data is not None
        else None
    )
    return AssistantMessage(
        tuple(blocks),
        LlmStopReason(str(data["stop_reason"])),
        provider=data.get("provider"),
        model=data.get("model"),
        response_model=data.get("response_model"),
        usage=usage,
    )
