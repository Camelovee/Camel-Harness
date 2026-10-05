from __future__ import annotations

from pathlib import Path

from camel_harness.llm import Message

from ..events import AgentEvent, MessageEndEvent
from .jsonl import JsonlSessionStorage
from .log import SessionLog, SessionLogEvent
from .metadata import SessionMetadata


class SessionManager:
    """管理会话元数据、消息和压缩上下文。"""

    def __init__(self, storage: JsonlSessionStorage) -> None:
        self._repository = storage

    async def record_agent_event(self, event: AgentEvent) -> None:
        """只把完整消息终态写入会话；增量和运行事件不属于持久化消息。"""

        if isinstance(event, MessageEndEvent):
            await self.append_message(event.session_id, event.message)

    async def append_message(self, session_id: str, message: Message) -> None:
        """追加一条完整规范消息；流式增量不得调用此方法。"""

        await self._repository.append_message(session_id, message)

    async def messages(self, session_id: str) -> tuple[Message, ...]:
        """读取完整规范消息，供 UI 展示、审计和重新压缩使用。"""
        return await self._repository.messages(session_id)

    async def context_log(self, session_id: str) -> SessionLog:
        """从追加式持久化事件重建 Agent 的上下文日志。"""

        return SessionLog(session_id, await self._repository.context_events(session_id))

    async def context_messages(self, session_id: str) -> tuple[Message, ...]:
        """读取已应用历史压缩操作的模型上下文。"""

        return (await self.context_log(session_id)).derive_messages()

    async def replace_context(
        self,
        session_id: str,
        start_seq: int,
        end_seq: int,
        summary: Message,
    ) -> SessionLogEvent:
        """用摘要遮蔽一段旧上下文，同时保留完整原始对话。"""

        log = await self.context_log(session_id)
        event = log.replace_context(start_seq, end_seq, summary)
        await self._repository.append_context_event(session_id, event)
        return event

    async def create(
        self,
        session_id: str,
        workspace: Path,
        name: str,
        *,
        parent_session_id: str | None = None,
        parent_tool_call_id: str | None = None,
    ) -> SessionMetadata:
        """创建会话及其唯一元数据。"""

        if parent_session_id is None and parent_tool_call_id is not None:
            raise ValueError("没有父会话时不能关联父工具调用")
        if parent_session_id == session_id:
            raise ValueError("会话不能成为自己的父会话")
        if parent_session_id is not None:
            parent = await self._repository.get_metadata(parent_session_id)
            if parent is None:
                raise ValueError(f"父会话不存在：{parent_session_id}")
            if parent.workspace != str(workspace.resolve()):
                raise ValueError("子会话工作目录必须与父会话一致")
        metadata = SessionMetadata.create(
            session_id,
            name,
            str(workspace.resolve()),
            parent_session_id=parent_session_id,
            parent_tool_call_id=parent_tool_call_id,
        )
        await self._repository.create(metadata)
        return metadata

    async def ensure(self, session_id: str, workspace: Path, name: str) -> SessionMetadata:
        """确保会话存在；已有会话的名称和创建时间保持不变。"""

        current = await self._repository.get_metadata(session_id)
        if current is not None:
            return current
        return await self.create(session_id, workspace, name)

    async def get(self, session_id: str) -> SessionMetadata | None:
        """查询单个会话元数据。"""

        return await self._repository.get_metadata(session_id)

    async def list(self) -> tuple[SessionMetadata, ...]:
        """按最近活动时间降序列出会话元数据。"""

        return await self._repository.list_metadata()

    async def update(
        self,
        session_id: str,
        *,
        name: str | None = None,
        workspace: Path | None = None,
        archived: bool | None = None,
        model_id: str | None = None,
    ) -> SessionMetadata:
        """更新会话名称、工作目录或归档状态。"""

        return await self._repository.update_metadata(
            session_id,
            name=name,
            workspace=str(workspace.resolve()) if workspace is not None else None,
            archived=archived,
            model_id=model_id,
        )

    async def delete(self, session_id: str) -> bool:
        """从后代到父会话依次删除消息和元数据。"""

        order = await self.deletion_order(session_id)
        deleted = False
        for current_id in order:
            deleted = await self._repository.delete(current_id) or deleted
        return deleted

    async def deletion_order(self, session_id: str) -> tuple[str, ...]:
        """返回目标会话及其全部后代的后序删除顺序。"""

        children: dict[str, list[str]] = {}
        for metadata in await self._repository.list_metadata():
            if metadata.parent_session_id is not None:
                children.setdefault(metadata.parent_session_id, []).append(metadata.session_id)

        pending = [session_id]
        visited: set[str] = set()
        preorder: list[str] = []
        while pending:
            current_id = pending.pop()
            if current_id in visited:
                continue
            visited.add(current_id)
            preorder.append(current_id)
            pending.extend(children.get(current_id, ()))
        return tuple(reversed(preorder))
