from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from camel_harness.llm import LlmModel, LlmUsage, Message, ToolDefinition

from ..resources import MemoryStore, ProjectRules, Skills
from ..session.events import ContextSnapshot
from ..session.manager import Session
from .compaction import CompactionEngine, CompactionError, CompactionResult
from .output_budget import context_input_limit
from .token_counting import (
    estimate_context_overhead_tokens,
    estimate_context_tokens,
)


class CompactionTrigger(StrEnum):
    """触发上下文准备时压缩评估的原因。"""

    PRESSURE = "pressure"
    CONTEXT_OVERFLOW = "context_overflow"


class ContextResourceError(RuntimeError):
    """动态资源加载失败，与压缩失败保持独立错误边界。"""


class ContextSessionError(RuntimeError):
    """上下文准备期间读取或提交会话失败，不混同资源与摘要错误。"""


@dataclass(frozen=True, slots=True)
class PreparedContext:
    """准备完成的系统提示与消息；必要的压缩已由 Session 提交。"""

    system_prompt: str
    messages: tuple[Message, ...]


class ContextManager:
    """为 Agent 提供动态系统上下文和历史压缩能力。"""

    def __init__(
        self,
        compaction_engine: CompactionEngine,
        project_rules: ProjectRules | None = None,
        skills: Skills | None = None,
        memory_store: MemoryStore | None = None,
        max_rules_chars: int = 12_000,
        *,
        system_prompt: str,
    ) -> None:
        system_prompt = system_prompt.strip()
        if not system_prompt:
            raise ValueError("系统提示不能为空")
        if max_rules_chars <= 0:
            raise ValueError("项目规则最大字符数必须大于 0")
        self._system_prompt = system_prompt
        self._compaction_engine = compaction_engine
        self._project_rules = project_rules
        self._skills = skills
        self._memory_store = memory_store
        self._max_rules_chars = max_rules_chars

    def with_system_prompt(self, system_prompt: str) -> ContextManager:
        """为独立角色创建上下文实例，共享借用资源，不转移资源关闭权。"""

        return ContextManager(
            compaction_engine=self._compaction_engine,
            project_rules=self._project_rules,
            skills=self._skills,
            memory_store=self._memory_store,
            max_rules_chars=self._max_rules_chars,
            system_prompt=system_prompt,
        )

    async def prepare(
        self,
        session: Session,
        model: LlmModel,
        *,
        workspace: Path,
        tool_definitions: tuple[ToolDefinition, ...] = (),
        on_usage: Callable[[LlmUsage], None] | None = None,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
    ) -> PreparedContext:
        """按次借用 Session 准备上下文；压缩提交成功后才返回请求内容。"""
        try:
            snapshot = await session.context_snapshot()
        except Exception as exc:
            raise ContextSessionError(str(exc)) from exc
        system_prompt = await self._build_system_prompt(workspace)
        compacted = await self.compact_if_needed(
            snapshot,
            model,
            trigger=trigger,
            on_usage=on_usage,
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
        )
        if compacted is not None:
            snapshot = await self._commit_compaction(session, snapshot, compacted)
        return PreparedContext(system_prompt, snapshot.messages)

    async def _build_system_prompt(self, workspace: Path) -> str:
        """始终从基础提示组装资源，保留独立的资源故障边界。"""
        try:
            dynamic_context = await self.build_system_context(workspace)
        except Exception as exc:
            raise ContextResourceError(str(exc)) from exc
        if dynamic_context:
            return f"{self._system_prompt}\n\n{dynamic_context}"
        return self._system_prompt

    @staticmethod
    async def _commit_compaction(
        session: Session, snapshot: ContextSnapshot, compacted: CompactionResult
    ) -> ContextSnapshot:
        """提交一致性和在途取消语义仍由 Session 保证。"""
        try:
            return await session.compact(
                compacted.summary,
                compacted.first_kept_entry_id,
                snapshot=snapshot,
                tokens_before=compacted.tokens_before,
                usage=compacted.usage,
            )
        except Exception as exc:
            raise ContextSessionError(str(exc)) from exc

    async def build_system_context(self, workspace: Path) -> str:
        """组装项目规则、Skill 索引与完整的有界 Memory。"""

        rules = await self._project_rules.read(workspace) if self._project_rules else ""
        skills = await self._skills.discover() if self._skills is not None else ()
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
        if self._memory_store is not None:
            memory_context = await self._memory_store.prompt_context(tolerant=True)
            if memory_context:
                sections.append(memory_context)
        return "\n\n".join(sections)

    async def compact_if_needed(
        self,
        snapshot: ContextSnapshot,
        model: LlmModel,
        *,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
        on_usage: Callable[[LlmUsage], None] | None = None,
        system_prompt: str = "",
        tool_definitions: tuple[ToolDefinition, ...] = (),
    ) -> CompactionResult | None:
        """判断是否需要压缩；需要时才生成计划，不执行会话持久化。"""

        compaction_settings = self._compaction_engine.compaction_settings
        input_limit = context_input_limit(model, compaction_settings.reserve_tokens)
        if input_limit <= 0:
            raise CompactionError("上下文容量不足：模型窗口无法容纳配置的输出与压缩预留")
        overhead = estimate_context_overhead_tokens(system_prompt, tool_definitions)
        estimated_tokens = estimate_context_tokens(
            snapshot, model, context_overhead_tokens=overhead
        ).total_tokens
        if not compaction_settings.enabled:
            if trigger is CompactionTrigger.CONTEXT_OVERFLOW or estimated_tokens >= input_limit:
                raise CompactionError("上下文超出安全预算且自动压缩未开启")
            return None
        if (
            len(snapshot.nodes) < compaction_settings.min_messages
            and trigger is not CompactionTrigger.CONTEXT_OVERFLOW
        ):
            if estimated_tokens >= input_limit:
                raise CompactionError("上下文超限，但没有足够的完整消息可压缩")
            return None
        if trigger is not CompactionTrigger.CONTEXT_OVERFLOW and estimated_tokens < input_limit:
            return None
        return await self._compaction_engine.compact(
            snapshot,
            model,
            require_reduction=trigger is CompactionTrigger.CONTEXT_OVERFLOW,
            on_usage=on_usage,
            context_overhead_tokens=overhead,
        )
