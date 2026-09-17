from __future__ import annotations

from .skill_storage import SkillStorage
from .skill_types import SkillDefinition, SkillSource


class Skills:
    """提供 Skill 发现、保存、审计、演化与回滚。"""

    def __init__(self, storage: SkillStorage) -> None:
        self._repository = storage

    async def discover(self) -> tuple[SkillDefinition, ...]:
        """发现所有可用 Skill。"""
        return await self._repository.discover()

    async def get(self, name: str) -> SkillDefinition | None:
        """按名称读取 Skill。"""
        return await self._repository.get(name)

    async def save(
        self, name: str, description: str, instructions: str, source: SkillSource
    ) -> SkillDefinition:
        """创建或覆盖 Skill。"""
        return await self._repository.save(name, description, instructions, source)

    async def record_usage(self, name: str, session_id: str) -> None:
        """记录一次 Skill 使用。"""

        await self._repository.record_usage(name, session_id)

    async def evolve(self, name: str, lesson: str, rationale: str) -> SkillDefinition:
        """在快照和审计保护下演化 Skill。"""

        return await self._repository.evolve(name, lesson, rationale)

    async def rollback(self, name: str) -> SkillDefinition:
        """回滚到最近一次 Skill 快照。"""

        return await self._repository.rollback(name)
