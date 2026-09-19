from __future__ import annotations

import re
from pathlib import Path

from ..resources import MemoryStore, ProjectRules, Skills
from ..session.log import SessionLog
from ..state import AgentOptions
from .compaction import CompactionEngine, CompactionResult, CompactionTrigger


class ContextManager:
    """为 Agent 提供动态系统上下文和历史压缩能力。"""

    def __init__(
        self,
        compaction: CompactionEngine,
        project_rules: ProjectRules | None = None,
        skills: Skills | None = None,
        memories: MemoryStore | None = None,
        max_rules_chars: int = 12_000,
    ) -> None:
        if max_rules_chars <= 0:
            raise ValueError("项目规则最大字符数必须大于 0")
        self._compaction = compaction
        self._project_rules = project_rules
        self._skills = skills
        self._memories = memories
        self._max_rules_chars = max_rules_chars

    async def build_system_context(self, prompt: str, workspace: Path) -> str:
        """组装项目规则、Skill 索引和与当前任务相关的 Memory。"""

        rules = await self._project_rules.read(workspace) if self._project_rules else ""
        skills = await self._skills.discover() if self._skills is not None else ()
        memories = await self._memories.list() if self._memories is not None else ()
        sections: list[str] = []
        if rules:
            sections.append(f"# 项目规则\n{rules[: self._max_rules_chars]}")
        if skills:
            content = "\n\n".join(
                f"## {item.name} ({item.source.value})\n{item.description}\n\n{item.instructions}"
                for item in skills
            )
            sections.append(f"# Skills\n{content}")
        if memories:
            manifest = "\n".join(f"- {item.name}: {item.description}" for item in memories)
            sections.append(f"# 长期 Memory 索引\n{manifest}")
            tokens = set(re.findall(r"[\w-]+", prompt.lower()))
            ranked = sorted(
                memories,
                key=lambda item: len(tokens & _memory_tokens(item.name, item.description)),
                reverse=True,
            )
            relevant = [
                item for item in ranked[:3] if tokens & _memory_tokens(item.name, item.description)
            ]
            if relevant:
                content = "\n\n".join(f"## {item.name}\n{item.content[:4000]}" for item in relevant)
                sections.append(f"# 与当前任务相关的 Memory\n{content}")
        return "\n\n".join(sections)

    async def compact_if_needed(
        self,
        session: SessionLog,
        options: AgentOptions,
        *,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
    ) -> CompactionResult | None:
        """根据只读 SessionLog 生成压缩计划，不执行会话持久化。"""

        return await self._compaction.compact_if_needed(session, options, trigger=trigger)


def _memory_tokens(name: str, description: str) -> set[str]:
    return set(re.findall(r"[\w-]+", f"{name} {description}".lower()))
