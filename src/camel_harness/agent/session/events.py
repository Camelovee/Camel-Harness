from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias
from uuid import uuid4

from camel_harness.llm import LlmUsage, Message, UserMessage

from .metadata import SessionMetadata

SCHEMA_VERSION = 1


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
    updated_at = header.timestamp
    message_count = 0
    last_message_type: str | None = None
    for event in iterator:
        if isinstance(event, SessionMessageEvent):
            message_count += 1
            last_message_type = event.message.role
            updated_at = event.timestamp
        elif isinstance(event, SessionInfoEvent):
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
    )
