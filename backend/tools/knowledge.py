from __future__ import annotations

from backend.resources import MemoryStore, Skills
from backend.types import Tool

from .memory import MemoryDeleteTool, MemoryListTool, MemoryLoadTool, MemorySaveTool
from .provider import ToolProvider
from .skill import (
    SkillCreateTool,
    SkillEvolveTool,
    SkillListTool,
    SkillLoadTool,
    SkillRollbackTool,
)


class KnowledgeToolProvider(ToolProvider):
    """聚合 Skills 与 Memory 工具，但保持两类依赖彼此独立。"""

    def __init__(
        self,
        skills: Skills,
        memories: MemoryStore,
    ) -> None:
        self._skills = skills
        self._memories = memories

    async def discover(self) -> tuple[Tool, ...]:
        return (
            SkillListTool(self._skills),
            SkillLoadTool(self._skills),
            SkillCreateTool(self._skills),
            MemoryListTool(self._memories),
            MemoryLoadTool(self._memories),
            MemorySaveTool(self._memories),
            MemoryDeleteTool(self._memories),
            SkillEvolveTool(self._skills),
            SkillRollbackTool(self._skills),
        )
