from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from camel_harness.types import Message, MessageRole


class SurfaceOperation(StrEnum):
    """SessionLog 对模型上下文表面的操作类型。"""

    APPEND = "append"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True)
class SessionLogEvent:
    """一条追加式上下文日志事件。

    APPEND 事件通过 ``message_index`` 引用 ``messages.jsonl`` 中的完整消息，
    避免重复持久化；REPLACE 事件携带摘要并遮蔽指定事件序号区间。
    """

    seq: int
    operation: SurfaceOperation
    message: Message
    message_index: int | None = None
    replace_start: int | None = None
    replace_end: int | None = None

    def __post_init__(self) -> None:
        """强制不同操作的载荷互斥且区间只能指向既有事件。"""

        if self.seq < 0:
            raise ValueError("SessionLog 序号不能为负数")
        if self.operation is SurfaceOperation.APPEND:
            if (
                self.message_index is None
                or self.message_index < 0
                or self.replace_start is not None
                or self.replace_end is not None
            ):
                raise ValueError("SessionLog Append 事件载荷无效")
            return
        if (
            self.message_index is not None
            or self.replace_start is None
            or self.replace_end is None
            or self.replace_start < 0
            or self.replace_end < self.replace_start
            or self.replace_end >= self.seq
        ):
            raise ValueError("SessionLog Replace 事件载荷无效")

    @classmethod
    def append(cls, seq: int, message_index: int, message: Message) -> SessionLogEvent:
        """创建引用原始完整消息的追加事件。"""

        if seq < 0 or message_index < 0:
            raise ValueError("SessionLog 序号和消息下标不能为负数")
        return cls(seq, SurfaceOperation.APPEND, message, message_index=message_index)

    @classmethod
    def replace(
        cls,
        seq: int,
        start: int,
        end: int,
        message: Message,
    ) -> SessionLogEvent:
        """创建用摘要替换已有上下文区间的事件。"""

        if seq < 0 or start < 0 or end < start or end >= seq:
            raise ValueError("SessionLog 替换区间无效")
        return cls(
            seq,
            SurfaceOperation.REPLACE,
            message,
            replace_start=start,
            replace_end=end,
        )


@dataclass(frozen=True, slots=True)
class SurfaceNode:
    """当前模型上下文表面上的一条消息及其来源事件。"""

    seq: int
    message: Message


class SessionLog:
    """可从 JSONL 重放的会话上下文日志。

    日志本身只允许追加；模型可见上下文通过重放 APPEND/REPLACE 事件派生。
    这种设计保留完整对话，同时允许压缩摘要跨进程重启继续生效。
    """

    def __init__(self, session_id: str, events: tuple[SessionLogEvent, ...] = ()) -> None:
        if not session_id:
            raise ValueError("session_id 不能为空")
        self._session_id = session_id
        self._events: list[SessionLogEvent] = []
        self.restore(events)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def next_seq(self) -> int:
        return len(self._events)

    def append_message(self, message_index: int, message: Message) -> SessionLogEvent:
        """向内存日志追加完整消息引用，并分配连续事件序号。"""

        event = SessionLogEvent.append(self.next_seq, message_index, message)
        self._events.append(event)
        return event

    def replace_context(
        self,
        start_seq: int,
        end_seq: int,
        summary: Message,
    ) -> SessionLogEvent:
        """追加摘要替换事件；原事件仍保留在日志中。"""

        visible = {node.seq for node in self.surface()}
        if not any(start_seq <= seq <= end_seq for seq in visible):
            raise ValueError("SessionLog 替换区间不包含可见上下文")
        event = SessionLogEvent.replace(self.next_seq, start_seq, end_seq, summary)
        self._events.append(event)
        return event

    def restore(self, events: tuple[SessionLogEvent, ...]) -> None:
        """从持久化事件恢复，并拒绝乱序或有空洞的日志。"""

        for expected, event in enumerate(events):
            if event.seq != expected:
                raise ValueError(
                    f"SessionLog 序号必须从 0 连续递增：期望 {expected}，实际 {event.seq}"
                )
        self._events = list(events)
        # 重放一次以校验每个 Replace 都能命中当时可见的上下文。
        self.surface()

    def surface(self) -> tuple[SurfaceNode, ...]:
        """重放日志，派生当前模型可见的消息表面。"""

        nodes: list[SurfaceNode] = []
        for event in self._events:
            if event.operation is SurfaceOperation.APPEND:
                nodes.append(SurfaceNode(event.seq, event.message))
                continue
            assert event.replace_start is not None
            assert event.replace_end is not None
            insertion_index = next(
                (
                    index
                    for index, node in enumerate(nodes)
                    if event.replace_start <= node.seq <= event.replace_end
                ),
                len(nodes),
            )
            if insertion_index == len(nodes):
                raise ValueError("SessionLog Replace 区间不包含可见上下文")
            nodes = [
                node for node in nodes if not event.replace_start <= node.seq <= event.replace_end
            ]
            nodes.insert(insertion_index, SurfaceNode(event.seq, event.message))
        return tuple(nodes)

    def derive_messages(self) -> tuple[Message, ...]:
        """返回按模型可见顺序排列的上下文消息。"""

        projected: list[Message] = []
        for node in self.surface():
            message = node.message
            if (
                message.role is MessageRole.TOOL
                and projected
                and projected[-1].role is MessageRole.TOOL
            ):
                previous = projected[-1]
                projected[-1] = Message(previous.role, previous.content + message.content)
            else:
                projected.append(message)
        return tuple(projected)
