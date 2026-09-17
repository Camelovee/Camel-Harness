from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """按项目隔离的长期记忆条目。"""

    name: str
    description: str
    content: str
    kind: str = "project"
