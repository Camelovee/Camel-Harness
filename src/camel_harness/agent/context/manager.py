from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from camel_harness.llm import LlmUsage, Message, ToolDefinition, UserMessage

from ..resources import MemoryStore, ProjectRules, Skills
from ..session import ContextSnapshot
from .compaction import CompactionEngine, CompactionResult, CompactionTrigger


class ContextResourceError(RuntimeError):
    """动态资源加载失败，与压缩失败保持独立错误边界。"""


@dataclass(frozen=True, slots=True)
class PreparedContext:
    """提示和可选压缩计划；只有 Session 提交后才能使用新的历史投影。"""

    system_prompt: str
    compaction: CompactionResult | None


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

    @staticmethod
    def resolve_query(messages: tuple[Message, ...]) -> str:
        """恢复任务时优先使用最近用户文字，不以压缩摘要代替用户意图。"""

        return next(
            (message.content for message in reversed(messages) if isinstance(message, UserMessage)),
            "继续当前任务",
        )

    async def prepare(
        self,
        snapshot: ContextSnapshot,
        model: str,
        *,
        query: str,
        workspace: Path,
        system_prompt: str,
        tools: tuple[ToolDefinition, ...] = (),
        on_usage: Callable[[LlmUsage], None] | None = None,
    ) -> PreparedContext:
        """每 Turn 从基础提示组装资源并生成计划，不读取或提交会话日志。"""

        try:
            dynamic_context = await self.build_system_context(query, workspace)
        except Exception as exc:
            raise ContextResourceError(str(exc)) from exc
        if dynamic_context:
            system_prompt = f"{system_prompt}\n\n{dynamic_context}"
        compacted = await self.compact_if_needed(
            snapshot, model, on_usage=on_usage, system_prompt=system_prompt, tools=tools
        )
        return PreparedContext(system_prompt, compacted)

    async def build_system_context(self, prompt: str, workspace: Path) -> str:
        """组装项目规则、Skill 索引和与当前任务相关的 Memory。"""

        rules = await self._project_rules.read(workspace) if self._project_rules else ""
        skills = await self._skills.discover() if self._skills is not None else ()
        memories = await self._memories.list() if self._memories is not None else ()
        sections: list[str] = []
        if rules:
            sections.append(f"# 项目规则\n{rules[: self._max_rules_chars]}")
        if skills:
            manifest = "\n".join(
                f"- {item.name} ({item.source.value}): {item.description}" for item in skills
            )
            sections.append(
                "# 可用 Skills\n"
                "以下仅为名称和描述。任务相关时先调用 load_skill 读取完整说明；"
                "说明引用的支持文件再按需用 load_skill 的 path 参数读取。\n"
                f"{manifest}"
            )
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
        snapshot: ContextSnapshot,
        model: str,
        *,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
        on_usage: Callable[[LlmUsage], None] | None = None,
        system_prompt: str = "",
        tools: tuple[ToolDefinition, ...] = (),
    ) -> CompactionResult | None:
        """根据只读 ContextSnapshot 生成压缩计划，不执行会话持久化。"""

        tool_text = json.dumps(
            [
                {"name": tool.name, "description": tool.description, "schema": tool.input_schema}
                for tool in tools
            ],
            ensure_ascii=False,
        )
        overhead = (len((system_prompt + tool_text).encode("utf-8")) + 2) // 3
        return await self._compaction.compact_if_needed(
            snapshot,
            model,
            trigger=trigger,
            on_usage=on_usage,
            context_overhead_tokens=overhead,
        )


def _memory_tokens(name: str, description: str) -> set[str]:
    return set(re.findall(r"[\w-]+", f"{name} {description}".lower()))
