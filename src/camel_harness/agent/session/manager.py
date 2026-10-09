from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from camel_harness.llm import LlmUsage, Message

from .events import ContextSnapshot, SessionCompactionEvent, SessionEntry
from .jsonl import JsonlSessionStorage
from .metadata import SessionMetadata


class Session:
    """绑定单个已存在会话，只提供运行所需的历史与保存能力。"""

    def __init__(self, session_id: str, storage: JsonlSessionStorage) -> None:
        storage.directory(session_id)
        self._session_id = session_id
        self._repository = storage
        self._leaf_id: str | None = None
        self._use_latest = True

    @property
    def session_id(self) -> str:
        return self._session_id

    async def append_message(self, message: Message) -> str:
        """追加完整规范消息，不接受运行事件或流式增量。"""
        entry_id = await self._repository.append_message(
            self._session_id, message, leaf_id=self._leaf_id, use_latest=self._use_latest
        )
        if not self._use_latest:
            self._leaf_id = entry_id
        return entry_id

    async def messages(self) -> tuple[Message, ...]:
        """读取当前分支的原始完整消息；全树审计使用 entries。"""
        return await self._repository.messages(
            self._session_id, leaf_id=self._leaf_id, use_latest=self._use_latest
        )

    async def context_snapshot(self) -> ContextSnapshot:
        """读取已重放的只读快照，不向 Agent 暴露可变日志。"""
        return await self._repository.context_snapshot(
            self._session_id, leaf_id=self._leaf_id, use_latest=self._use_latest
        )

    async def entries(self) -> tuple[SessionEntry, ...]:
        """读取整棵历史树供审计；运行与恢复使用当前分支，不混入兄弟分支。"""
        return await self._repository.entries(self._session_id)

    async def branch(self, entry_id: str | None) -> ContextSnapshot:
        """选择历史节点或空根；后续追加形成新分支，不改写既有条目。"""
        snapshot = await self._repository.context_snapshot(
            self._session_id, leaf_id=entry_id, use_latest=False
        )
        self._leaf_id = entry_id
        self._use_latest = False
        return snapshot

    async def compact(
        self,
        summary: str,
        first_kept_entry_id: str,
        *,
        snapshot: ContextSnapshot,
        tokens_before: int = 0,
        usage: LlmUsage | None = None,
    ) -> ContextSnapshot:
        """校验计划快照并追加压缩条目，保留历史树，返回落盘后的新快照。"""

        event = SessionCompactionEvent(
            datetime.now(UTC).isoformat(), summary, first_kept_entry_id, tokens_before, usage
        )
        updated = await self._repository.compact(
            self._session_id, event, snapshot, leaf_id=self._leaf_id, use_latest=self._use_latest
        )
        if not self._use_latest:
            self._leaf_id = updated.leaf_id
        return updated


class SessionManager:
    """入口层管理会话创建、元数据、父子关系与级联删除。"""

    def __init__(self, storage: JsonlSessionStorage) -> None:
        self._repository = storage

    def bind(self, session_id: str) -> Session:
        """绑定已存在的会话；创建和工作区选择由入口层完成。"""
        return Session(session_id, self._repository)

    def directory(self, session_id: str) -> Path:
        """返回关联文件所属的会话目录，包含子会话的真实位置。"""
        return self._repository.directory(session_id)

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
