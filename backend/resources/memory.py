from .memory_storage import MemoryStorage
from .memory_types import MemoryEntry


class MemoryStore:
    """提供长期记忆的查询和修改操作。"""

    def __init__(self, storage: MemoryStorage) -> None:
        self._repository = storage

    async def list(self) -> tuple[MemoryEntry, ...]:
        """列出长期记忆摘要。"""

        return await self._repository.list()

    async def get(self, name: str) -> MemoryEntry | None:
        """按名称读取一条长期记忆。"""

        return await self._repository.get(name)

    async def save(self, entry: MemoryEntry) -> None:
        """保存或覆盖一条长期记忆。"""

        await self._repository.save(entry)

    async def delete(self, name: str) -> bool:
        """删除明确命名的长期记忆。"""

        return await self._repository.delete(name)
