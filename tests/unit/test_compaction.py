from collections.abc import AsyncIterator

import pytest

from camel_harness import AgentOptions
from camel_harness.agent.context import (
    CompactionEngine,
    CompactionTrigger,
    ContextManager,
)
from camel_harness.agent.resources import (
    MemoryEntry,
    MemoryStore,
    ProjectRules,
    Skills,
)
from camel_harness.agent.session import ContextNode, ContextSnapshot
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    MessageCompleted,
    ReasoningBlock,
    TextBlock,
    TextDelta,
    UserMessage,
)

_OPTIONS = AgentOptions("fake-model")


class SummaryLlm(LlmAdapter):
    """为压缩测试返回固定摘要。"""

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield TextDelta(text="保留目标、决策和待办。")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


class RecordingSummaryLlm(SummaryLlm):
    """记录摘要请求，用于验证模型上下文投影。"""

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        async for event in super().stream(request):
            yield event


def _engine(
    threshold: int = 1,
    min_messages: int = 8,
) -> CompactionEngine:
    llm = LlmClient(SummaryLlm())
    return CompactionEngine(
        llm,
        pressure_threshold_chars=threshold,
        min_messages_to_compact=min_messages,
    )


def _log(count: int = 8) -> ContextSnapshot:
    nodes = tuple(
        ContextNode(
            f"entry-{index}",
            UserMessage(f"消息 {index}")
            if index % 2 == 0
            else AssistantMessage((TextBlock(f"消息 {index}"),), LlmStopReason.STOP),
        )
        for index in range(count)
    )
    return ContextSnapshot(f"entry-{count - 1}", f"entry-{count - 1}", nodes)


async def test_basic_compaction_summarizes_old_three_quarters() -> None:
    engine = _engine()

    result = await engine.compact_if_needed(_log(), _OPTIONS)

    assert result is not None
    assert result.first_kept_entry_id == "entry-6"
    assert result.tokens_before > 0
    assert "保留目标、决策和待办。" in result.summary


async def test_basic_compaction_skips_history_below_pressure() -> None:
    result = await _engine(threshold=100_000).compact_if_needed(_log(), _OPTIONS)

    assert result is None


async def test_context_overflow_trigger_ignores_pressure_threshold() -> None:
    result = await _engine(threshold=100_000).compact_if_needed(
        _log(), _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW
    )

    assert result is not None


async def test_basic_compaction_does_not_reinject_reasoning() -> None:
    adapter = RecordingSummaryLlm()
    llm = LlmClient(adapter)
    engine = CompactionEngine(
        llm,
        pressure_threshold_chars=1,
        min_messages_to_compact=2,
    )
    reasoning = AssistantMessage(
        (ReasoningBlock("不应重新注入的推理"), TextBlock("可见结论")),
        LlmStopReason.STOP,
    )
    log = ContextSnapshot(
        "entry-2",
        "entry-2",
        (
            ContextNode("entry-0", reasoning),
            ContextNode("entry-1", UserMessage("继续")),
            ContextNode("entry-2", AssistantMessage((TextBlock("结果"),), LlmStopReason.STOP)),
        ),
    )

    await engine.compact_if_needed(
        log,
        _OPTIONS,
        trigger=CompactionTrigger.CONTEXT_OVERFLOW,
    )

    transcript = adapter.requests[0].messages[0]
    assert isinstance(transcript, UserMessage)
    assert "可见结论" in transcript.content
    assert "不应重新注入的推理" not in transcript.content
    assert adapter.requests[0].system_prompt is not None


def test_basic_compaction_rejects_invalid_options() -> None:
    llm = LlmClient(SummaryLlm())
    with pytest.raises(ValueError, match="必须大于 0"):
        CompactionEngine(llm, pressure_threshold_chars=0)
    with pytest.raises(ValueError, match="不能小于 2"):
        CompactionEngine(llm, min_messages_to_compact=1)


async def test_context_manager_loads_workspace_rules(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("只修改工作区内文件", encoding="utf-8")
    context = ContextManager(CompactionEngine(LlmClient(SummaryLlm())), ProjectRules())

    result = await context.build_system_context("继续实现", tmp_path)

    assert "# 项目规则" in result
    assert "只修改工作区内文件" in result


async def test_context_manager_lists_skills_without_loading_instructions(tmp_path) -> None:
    skills = Skills(tmp_path, user_root=tmp_path / "user-skills")
    skill_dir = tmp_path / ".camel" / "skills" / "python-style"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: python-style\ndescription: Python style\n---\n使用类型注解\n",
        encoding="utf-8",
    )
    memories = MemoryStore(tmp_path)
    await memories.save(MemoryEntry("architecture", "architecture decision", "保持线性会话"))
    context = ContextManager(
        CompactionEngine(LlmClient(SummaryLlm())),
        skills=skills,
        memories=memories,
    )

    result = await context.build_system_context("review architecture decision", tmp_path)

    assert "python-style" in result
    assert "python-style (project)" in result
    assert "Python style" in result
    assert "使用类型注解" not in result
    assert "保持线性会话" in result
    assert "load_skill" in result
    assert "memory_load" not in result


def test_compaction_budget_excludes_response_audit_and_tool_details() -> None:
    """审计元数据不属于模型输入，不应影响压缩触发或摘要文字。"""
    from camel_harness.agent.context.compaction import _model_message_dict
    from camel_harness.llm import LlmUsage, ToolResultMessage

    message = AssistantMessage(
        (TextBlock("回答"),),
        LlmStopReason.STOP,
        provider="provider",
        model="model",
        usage=LlmUsage(100, 20),
    )
    assert _model_message_dict(message) == {
        "role": "assistant",
        "content": [{"type": "text", "text": "回答"}],
        "stop_reason": "stop",
    }
    tool = ToolResultMessage(
        "call", "read_file", "预览", details={"content_ref": {"path": "private"}}
    )
    assert "details" not in _model_message_dict(tool)
