from __future__ import annotations

from abc import ABC, abstractmethod

from backend.types import Message

from .log import SessionLogEvent
from .metadata import SessionMetadata


class SessionStorage(ABC):
    """持久化会话元数据和可恢复规范消息的可替换接口。"""

    @abstractmethod
    async def append_message(self, session_id: str, message: Message) -> None:
        """追加一条已完整生成、可用于恢复上下文的消息。"""

        raise NotImplementedError

    @abstractmethod
    async def messages(self, session_id: str) -> tuple[Message, ...]:
        raise NotImplementedError

    @abstractmethod
    async def context_events(self, session_id: str) -> tuple[SessionLogEvent, ...]:
        """读取可重放的 Agent 上下文日志。"""

        raise NotImplementedError

    @abstractmethod
    async def append_context_event(self, session_id: str, event: SessionLogEvent) -> None:
        """持久化已由 SessionLog 分配序号的上下文事件。"""

        raise NotImplementedError

    @abstractmethod
    async def create(self, metadata: SessionMetadata) -> None:
        """创建会话元数据；同名会话已存在时失败。"""

        raise NotImplementedError

    @abstractmethod
    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        raise NotImplementedError

    @abstractmethod
    async def list_metadata(self) -> tuple[SessionMetadata, ...]:
        raise NotImplementedError

    @abstractmethod
    async def update_metadata(self, metadata: SessionMetadata) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, session_id: str) -> bool:
        """删除会话消息与其唯一元数据。"""

        raise NotImplementedError
