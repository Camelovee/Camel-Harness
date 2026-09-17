from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SkillSource(StrEnum):
    """Skill 的来源层级；工作区层优先级最高。"""

    BUILTIN = "builtin"
    USER = "user"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """可复用工作方法的稳定表示。"""

    name: str
    description: str
    instructions: str
    source: SkillSource
    version: str = "1.0.0"
