from __future__ import annotations

from abc import ABC, abstractmethod

from .skill_types import SkillDefinition, SkillSource


class SkillStorage(ABC):
    """Skill 发现、持久化、使用审计、演化和回滚接口。"""

    @abstractmethod
    async def discover(self) -> tuple[SkillDefinition, ...]:
        raise NotImplementedError

    @abstractmethod
    async def get(self, name: str) -> SkillDefinition | None:
        raise NotImplementedError

    @abstractmethod
    async def save(
        self, name: str, description: str, instructions: str, source: SkillSource
    ) -> SkillDefinition:
        raise NotImplementedError

    @abstractmethod
    async def record_usage(self, name: str, session_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def evolve(self, name: str, lesson: str, rationale: str) -> SkillDefinition:
        raise NotImplementedError

    @abstractmethod
    async def rollback(self, name: str) -> SkillDefinition:
        raise NotImplementedError
