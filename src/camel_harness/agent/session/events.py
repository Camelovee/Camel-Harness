from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypeAlias
from uuid import uuid4

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

from .types import SCHEMA_VERSION, SessionMetadata, TaskOutcome


@dataclass(frozen=True, slots=True)
class SessionHeaderEvent:
    """日志唯一会话头，记录身份、创建时间及初始元数据。"""

    timestamp: str
    session_id: str
    workspace: str
    name: str
    model_id: str | None = None
    archived: bool = False
    parent_session_id: str | None = None
    parent_tool_call_id: str | None = None
    version: int = SCHEMA_VERSION
    type: Literal["session"] = field(default="session", init=False)


@dataclass(frozen=True, slots=True)
class SessionEntryBase:
    """树条目的持久化身份；根条目的 parent_id 为 None。"""

    id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)
    parent_id: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class SessionMessageEvent(SessionEntryBase):
    """只持久化完整规范消息，不包含运行事件或 Provider 增量。"""

    timestamp: str
    message: Message
    type: Literal["message"] = field(default="message", init=False)


@dataclass(frozen=True, slots=True)
class SessionCompactionEvent(SessionEntryBase):
    """摘要替代当前分支前缀，通过条目 ID 标记保留边界。"""

    timestamp: str
    summary: str
    first_kept_entry_id: str
    tokens_before: int = 0
    usage: LlmUsage | None = None
    type: Literal["compaction"] = field(default="compaction", init=False)

    def __post_init__(self) -> None:
        if not self.first_kept_entry_id or not self.summary.strip():
            raise ValueError("压缩必须包含摘要与保留条目 ID")
        if type(self.tokens_before) is not int or self.tokens_before < 0:
            raise ValueError("压缩前 Token 估算必须是非负整数")


@dataclass(frozen=True, slots=True)
class SessionInfoEvent(SessionEntryBase):
    """元数据更新快照，不参与模型上下文。"""

    timestamp: str
    name: str
    workspace: str
    model_id: str | None = None
    archived: bool = False
    task_outcome: TaskOutcome | None = None
    type: Literal["session_info"] = field(default="session_info", init=False)


SessionEvent: TypeAlias = (
    SessionHeaderEvent | SessionMessageEvent | SessionCompactionEvent | SessionInfoEvent
)
SessionEntry: TypeAlias = SessionMessageEvent | SessionCompactionEvent | SessionInfoEvent


@dataclass(frozen=True, slots=True)
class ContextNode:
    """模型可见消息及其来源条目 ID，摘要引用压缩条目自身。"""

    entry_id: str
    message: Message


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """当前分支的只读上下文；revision 标记日志最后一次消息或压缩写入。"""

    leaf_id: str | None = None
    revision: str | None = None
    nodes: tuple[ContextNode, ...] = ()
    # 仅投影到内存；此前节点的 usage 基于压缩前历史，不再代表当前上下文。
    usage_start_index: int = 0

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(node.message for node in self.nodes)


def build_branch(
    events: Iterable[SessionEvent], leaf_id: str | None = None, *, use_latest: bool = True
) -> tuple[SessionEntry, ...]:
    """校验整棵追加树，再沿当前叶节点回溯；父条目必须先于子条目出现。"""
    by_id: dict[str, SessionEntry] = {}
    latest: str | None = None
    for event in events:
        if isinstance(event, SessionHeaderEvent):
            continue
        if not isinstance(event.id, str) or not event.id or event.id in by_id:
            raise ValueError("会话条目 ID 为空或重复")
        if event.parent_id is not None and event.parent_id not in by_id:
            raise ValueError("会话条目的父 ID 不存在或引用未来条目")
        by_id[event.id] = event
        latest = event.id
    # use_latest 优先于 leaf_id：latest 是日志最后条目，而不是叶节点子树的末端。
    current = latest if use_latest else leaf_id
    if current is not None and current not in by_id:
        raise ValueError("当前分支叶节点不存在")
    path: list[SessionEntry] = []
    while current is not None:
        entry = by_id[current]
        path.append(entry)
        current = entry.parent_id
    return tuple(reversed(path))


def project_context(
    events: Iterable[SessionEvent], leaf_id: str | None = None, *, use_latest: bool = True
) -> ContextSnapshot:
    """学习 pi：当前叶节点路径中的最新压缩摘要加保留历史，再加后续消息。"""
    entries = tuple(events)
    path = build_branch(entries, leaf_id, use_latest=use_latest)
    # revision 取全日志最后一条消息/压缩，故意不含 SessionInfoEvent，用作跨并发写 CAS。
    revision = next(
        (
            event.id
            for event in reversed(entries)
            if isinstance(event, SessionMessageEvent | SessionCompactionEvent)
        ),
        None,
    )
    nodes: list[ContextNode] = []
    usage_start_index = 0
    for entry in path:
        if isinstance(entry, SessionMessageEvent):
            nodes.append(ContextNode(entry.id, entry.message))
        elif isinstance(entry, SessionCompactionEvent):
            # 沿路径依次应用压缩；kept == 0 被拒绝，压缩必须至少吃掉一个可见节点。
            kept = next(
                (
                    index
                    for index, node in enumerate(nodes)
                    if node.entry_id == entry.first_kept_entry_id
                ),
                None,
            )
            if kept is None or kept == 0:
                raise ValueError("压缩保留条目必须位于当前可见上下文，且存在待压缩前缀")
            nodes = [
                # 固定前缀让压缩器识别上一次摘要，从而做增量更新而不是摘要套摘要。
                ContextNode(entry.id, UserMessage(f"[Compacted History]\n\n{entry.summary}")),
                *nodes[kept:],
            ]
            usage_start_index = len(nodes)
    return ContextSnapshot(
        leaf_id=path[-1].id if path else None,
        revision=revision,
        nodes=tuple(nodes),
        usage_start_index=usage_start_index,
    )


def project_metadata(events: Iterable[SessionEvent]) -> SessionMetadata:
    """从同一日志投影元数据；会话身份与创建时间只取自唯一会话头。"""
    iterator = iter(events)
    header = next(iterator)
    if not isinstance(header, SessionHeaderEvent):
        raise ValueError("会话日志必须以会话头开始")
    name, workspace = header.name, header.workspace
    model_id, archived = header.model_id, header.archived
    task_outcome: TaskOutcome | None = None
    updated_at = header.timestamp
    # 身份与创建时间只来自 header；message_count 统计整棵树，而非当前分支。
    message_count = 0
    last_message_type: str | None = None
    for event in iterator:
        if isinstance(event, SessionMessageEvent):
            message_count += 1
            last_message_type = event.message.role
            updated_at = event.timestamp
        elif isinstance(event, SessionInfoEvent):
            task_outcome = event.task_outcome
            name, workspace = event.name, event.workspace
            model_id, archived = event.model_id, event.archived
            updated_at = event.timestamp
    return SessionMetadata(
        session_id=header.session_id,
        name=name,
        workspace=workspace,
        created_at=header.timestamp,
        updated_at=updated_at,
        message_count=message_count,
        last_message_type=last_message_type,
        model_id=model_id,
        archived=archived,
        parent_session_id=header.parent_session_id,
        parent_tool_call_id=header.parent_tool_call_id,
        schema_version=header.version,
        task_outcome=task_outcome,
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
        if event.task_outcome is not None:
            result["taskOutcome"] = asdict(event.task_outcome)
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
        # 版本必须严格相等；未知类型与旧格式都直接报错，不做兼容降级。
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
            TaskOutcome(**data["taskOutcome"]) if data.get("taskOutcome") is not None else None,
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
