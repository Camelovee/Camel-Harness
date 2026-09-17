from abc import ABC, abstractmethod

from .memory_types import MemoryEntry


class MemoryStorage(ABC):
    """长期记忆的可替换存储接口。"""

    @abstractmethod
    async def list(self) -> tuple[MemoryEntry, ...]:
        raise NotImplementedError

    @abstractmethod
    async def get(self, name: str) -> MemoryEntry | None:
        raise NotImplementedError

    @abstractmethod
    async def save(self, entry: MemoryEntry) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, name: str) -> bool:
        raise NotImplementedError
