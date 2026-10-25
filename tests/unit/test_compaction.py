import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from camel_harness.agent.config import CompactionSettings, MemorySettings
from camel_harness.agent.context import (
    CompactionEngine,
    CompactionResult,
    CompactionTrigger,
    ContextManager,
    ContextResourceError,
)
from camel_harness.agent.context.compaction import CompactionError
from camel_harness.agent.context.token_counting import estimate_message_tokens
from camel_harness.agent.memory import MemoryManager
from camel_harness.agent.resources import ProjectRules, Skills
from camel_harness.agent.session import ContextNode, ContextSnapshot, Session
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmThinkingEffort,
    LlmUsage,
    MessageCompleted,
    ReasoningBlock,
    TextBlock,
    TextDelta,
    ToolDefinition,
    UsageUpdated,
    UserMessage,
)

_MODEL = LlmModel("fake", "fake-model")


def _session_with_snapshot(snapshot: ContextSnapshot | None = None) -> AsyncMock:
    """用公开 Session 接口替身检查准备与提交次序。"""
    session = AsyncMock(spec=Session)
    session.context_snapshot.return_value = snapshot or _context_snapshot()
    return session


async def test_context_prepare_returns_committed_messages_and_keeps_original_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compaction_engine = _compaction_engine()
    context_manager = ContextManager(
        system_prompt=" \n基础\t ", compaction_engine=compaction_engine
    )
    snapshot = _context_snapshot()
    original_nodes = snapshot.nodes
    plan = CompactionResult("entry-6", 100, "摘要", LlmUsage(100, 10))
    committed = replace(
        snapshot, nodes=(ContextNode("summary", UserMessage("摘要")), *snapshot.nodes[6:])
    )
    calls: list[tuple[ContextSnapshot, LlmModel, int]] = []

    async def build_context(workspace: Path) -> str:
        assert workspace == tmp_path
        return "资源"

    async def plan_compaction(
        current: ContextSnapshot, model: LlmModel, *, context_overhead_tokens: int, **kwargs: object
    ) -> CompactionResult:
        calls.append((current, model, context_overhead_tokens))
        return plan

    monkeypatch.setattr(context_manager, "build_system_context", build_context)
    monkeypatch.setattr(compaction_engine, "compact", plan_compaction)
    tool_definitions = (ToolDefinition("read_file", "读取", {"type": "object"}),)
    for _ in range(2):
        session = _session_with_snapshot(snapshot)
        session.compact.return_value = committed
        prepared = await context_manager.prepare(
            session,
            _MODEL,
            workspace=tmp_path,
            tool_definitions=tool_definitions,
        )
        assert prepared.system_prompt == "基础\n\n资源"
        assert prepared.messages == committed.messages
        session.context_snapshot.assert_awaited_once_with()
        session.compact.assert_awaited_once_with(
            plan.summary,
            plan.first_kept_entry_id,
            snapshot=snapshot,
            tokens_before=plan.tokens_before,
            usage=plan.usage,
        )
    assert snapshot.nodes == original_nodes
    assert all(
        current is snapshot and model == _MODEL and overhead > 0
        for current, model, overhead in calls
    )


async def test_context_prepare_keeps_resource_errors_distinct_and_propagates_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context_manager = ContextManager(
        system_prompt="基础", compaction_engine=_compaction_engine(input_limit=100_000)
    )

    async def fail(*args: object) -> None:
        raise OSError("规则读取失败")

    monkeypatch.setattr(context_manager, "build_system_context", fail)
    with pytest.raises(ContextResourceError, match="规则读取失败"):
        await context_manager.prepare(_session_with_snapshot(), _MODEL, workspace=tmp_path)

    async def cancel(*args: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(context_manager, "build_system_context", cancel)
    with pytest.raises(asyncio.CancelledError):
        await context_manager.prepare(_session_with_snapshot(), _MODEL, workspace=tmp_path)


@pytest.mark.parametrize("system_prompt", ["", " ", "\n\t", "\u3000"])
def test_context_rejects_empty_base_prompt_and_role_prompt(system_prompt: str) -> None:
    compaction_engine = _compaction_engine(input_limit=100_000)
    with pytest.raises(ValueError, match="系统提示不能为空"):
        ContextManager(compaction_engine=compaction_engine, system_prompt=system_prompt)

    context_manager = ContextManager(compaction_engine=compaction_engine, system_prompt="父角色")
    with pytest.raises(ValueError, match="系统提示不能为空"):
        context_manager.with_system_prompt(system_prompt)


async def test_context_uses_trimmed_base_prompt_without_resources(tmp_path: Path) -> None:
    context_manager = ContextManager(
        compaction_engine=_compaction_engine(input_limit=100_000), system_prompt=" \n基础提示\t "
    )

    prepared = await context_manager.prepare(_session_with_snapshot(), _MODEL, workspace=tmp_path)

    assert prepared.system_prompt == "基础提示"
    assert prepared.messages == _context_snapshot().messages


async def test_role_prompt_is_counted_in_shared_compaction_budget(tmp_path: Path) -> None:
    adapter = RecordingSummaryLlm()
    context_manager = ContextManager(
        compaction_engine=CompactionEngine(
            llm_client=LlmClient(adapter),
            compaction_settings=CompactionSettings(
                reserve_tokens=128_000 - 2048,
                keep_recent_tokens=128,
                max_output_tokens=64,
                retry_max_output_tokens=128,
            ),
        ),
        system_prompt="父角色",
    )
    snapshot = _context_snapshot(count=2)
    prepared = await context_manager.prepare(
        _session_with_snapshot(snapshot), _MODEL, workspace=tmp_path
    )
    assert prepared.messages == snapshot.messages

    child_context_manager = context_manager.with_system_prompt("子角色" * 3000)
    with pytest.raises(CompactionError, match="没有足够的完整消息"):
        await child_context_manager.prepare(
            _session_with_snapshot(snapshot), _MODEL, workspace=tmp_path
        )

    prepared = await context_manager.prepare(
        _session_with_snapshot(snapshot), _MODEL, workspace=tmp_path
    )
    assert prepared.system_prompt == "父角色"
    assert prepared.messages == snapshot.messages
    assert not adapter.requests


class SummaryLlm(LlmAdapter):
    """为压缩测试返回固定摘要。"""

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield TextDelta(text="保留目标、决策和待办。")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


class RecordingSummaryLlm(SummaryLlm):
    """记录摘要请求，用于验证模型上下文投影。"""

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(llm_request)
        async for event in super().stream(llm_request):
            yield event


def _compaction_settings(
    *,
    input_limit: int = 384,
    enabled: bool = True,
    min_messages: int = 8,
) -> CompactionSettings:
    """用保留量反推输入上限，便于直接表达预算边界。"""
    return CompactionSettings(
        enabled=enabled,
        reserve_tokens=128_000 - input_limit,
        keep_recent_tokens=140,
        max_output_tokens=64,
        retry_max_output_tokens=128,
        min_messages=min_messages,
    )


def _compaction_engine(*, input_limit: int = 384) -> CompactionEngine:
    return CompactionEngine(
        llm_client=LlmClient(SummaryLlm()),
        compaction_settings=_compaction_settings(input_limit=input_limit),
    )


def _context_manager(
    *,
    input_limit: int = 384,
    enabled: bool = True,
    min_messages: int = 8,
) -> ContextManager:
    return ContextManager(
        compaction_engine=CompactionEngine(
            llm_client=LlmClient(SummaryLlm()),
            compaction_settings=_compaction_settings(
                input_limit=input_limit, enabled=enabled, min_messages=min_messages
            ),
        ),
        system_prompt="基础",
    )


def _context_snapshot(count: int = 8) -> ContextSnapshot:
    nodes = tuple(
        ContextNode(
            f"entry-{index}",
            UserMessage(f"消息 {index}" + "。" * 100)
            if index % 2 == 0
            else AssistantMessage((TextBlock(f"消息 {index}" + "。" * 100),), LlmStopReason.STOP),
        )
        for index in range(count)
    )
    return ContextSnapshot(f"entry-{count - 1}", f"entry-{count - 1}", nodes)


async def test_compaction_keeps_recent_token_budget() -> None:
    compaction_engine = _compaction_engine()

    result = await compaction_engine.compact(_context_snapshot(), _MODEL)

    assert result.first_kept_entry_id == "entry-6"
    assert result.tokens_before > 0
    assert "保留目标、决策和待办。" in result.summary


async def test_context_manager_skips_history_below_pressure() -> None:
    result = await _context_manager(input_limit=100_000).compact_if_needed(
        _context_snapshot(), _MODEL
    )

    assert result is None


async def test_usage_controls_pressure_but_cut_and_tokens_before_use_content_estimates() -> None:
    snapshot = _context_snapshot()
    last_message = snapshot.nodes[-1].message
    assert isinstance(last_message, AssistantMessage)
    last_message = replace(
        last_message, provider=_MODEL.provider, model=_MODEL.model, usage=LlmUsage(100, 20)
    )
    snapshot = replace(snapshot, nodes=(*snapshot.nodes[:-1], ContextNode("entry-7", last_message)))
    context_manager = _context_manager(input_limit=600)
    assert await context_manager.compact_if_needed(snapshot, _MODEL) is None

    last_message = replace(last_message, usage=LlmUsage(5000, 100))
    snapshot = replace(snapshot, nodes=(*snapshot.nodes[:-1], ContextNode("entry-7", last_message)))
    result = await context_manager.compact_if_needed(snapshot, _MODEL)
    assert result is not None
    assert result.first_kept_entry_id == "entry-6"
    assert result.tokens_before == sum(
        estimate_message_tokens(message) for message in snapshot.messages
    )
    assert result.tokens_before != 5100


@pytest.mark.parametrize("enabled", [False, True])
async def test_usage_guards_capacity_with_compression_disabled_or_short_history(
    enabled: bool,
) -> None:
    context_manager = _context_manager(input_limit=2048, enabled=enabled)
    answer = AssistantMessage(
        (TextBlock("结果"),),
        LlmStopReason.STOP,
        provider=_MODEL.provider,
        model=_MODEL.model,
        usage=LlmUsage(2048, 1),
    )
    snapshot = ContextSnapshot(nodes=(ContextNode("answer", answer),))
    with pytest.raises(CompactionError, match="安全预算|完整消息"):
        await context_manager.compact_if_needed(snapshot, _MODEL)
    snapshot = replace(
        snapshot, nodes=(ContextNode("answer", replace(answer, usage=LlmUsage(100, 20))),)
    )
    assert await context_manager.compact_if_needed(snapshot, _MODEL) is None


async def test_projected_capacity_still_counts_current_prompt_after_usage_trigger() -> None:
    snapshot = _context_snapshot()
    last_message = snapshot.nodes[-1].message
    assert isinstance(last_message, AssistantMessage)
    last_message = replace(
        last_message, provider=_MODEL.provider, model=_MODEL.model, usage=LlmUsage(1000, 20)
    )
    snapshot = replace(snapshot, nodes=(*snapshot.nodes[:-1], ContextNode("entry-7", last_message)))
    with pytest.raises(CompactionError, match="仍超过安全预算"):
        await _compaction_engine().compact(snapshot, _MODEL, context_overhead_tokens=384)


async def test_short_history_cannot_bypass_context_capacity_check():
    snapshot = ContextSnapshot("entry", "entry", (ContextNode("entry", UserMessage("。" * 5000)),))
    with pytest.raises(CompactionError, match="没有足够"):
        await _context_manager().compact_if_needed(snapshot, _MODEL)


async def test_disabled_compaction_does_not_disable_capacity_guard():
    snapshot = ContextSnapshot(
        "entry", "entry", (ContextNode("entry", UserMessage("。" * 130_000)),)
    )
    with pytest.raises(CompactionError, match="未开启"):
        await _context_manager(enabled=False).compact_if_needed(snapshot, _MODEL)


async def test_context_overflow_trigger_ignores_pressure_threshold() -> None:
    result = await _context_manager(input_limit=100_000).compact_if_needed(
        _context_snapshot(), _MODEL, trigger=CompactionTrigger.CONTEXT_OVERFLOW
    )

    assert result is not None


async def test_capacity_failure_precedes_disabled_compaction() -> None:
    """容量不足必须先于压缩开关报告，保持原有错误顺序。"""
    context_manager = ContextManager(
        compaction_engine=CompactionEngine(
            llm_client=LlmClient(SummaryLlm()),
            compaction_settings=CompactionSettings(enabled=False, reserve_tokens=32_768),
        ),
        system_prompt="基础",
    )
    model = replace(_MODEL, context_window_tokens=32_768)

    with pytest.raises(CompactionError, match="容量不足"):
        await context_manager.compact_if_needed(_context_snapshot(), model)


async def test_basic_compaction_does_not_reinject_reasoning() -> None:
    adapter = RecordingSummaryLlm()
    llm_client = LlmClient(adapter)
    compaction_engine = CompactionEngine(
        llm_client=llm_client,
        compaction_settings=CompactionSettings(min_messages=2),
    )
    reasoning = AssistantMessage(
        (ReasoningBlock("不应重新注入的推理"), TextBlock("可见结论")),
        LlmStopReason.STOP,
    )
    context_snapshot = ContextSnapshot(
        "entry-2",
        "entry-2",
        (
            ContextNode("entry-0", reasoning),
            ContextNode("entry-1", UserMessage("继续")),
            ContextNode("entry-2", AssistantMessage((TextBlock("结果"),), LlmStopReason.STOP)),
        ),
    )

    await compaction_engine.compact(
        context_snapshot,
        _MODEL,
        require_reduction=True,
    )

    transcript = adapter.requests[0].messages[0]
    assert isinstance(transcript, UserMessage)
    assert "可见结论" in transcript.content
    assert "不应重新注入的推理" not in transcript.content
    assert adapter.requests[0].system_prompt is not None


def test_basic_compaction_rejects_invalid_options() -> None:
    with pytest.raises(ValueError, match="模型窗口"):
        LlmModel("fake", "m", context_window_tokens=0)
    with pytest.raises(ValueError, match="min_messages"):
        CompactionSettings(min_messages=1)
    with pytest.raises(ValueError, match="重试预算"):
        CompactionSettings(max_output_tokens=8192, retry_max_output_tokens=4096)


async def test_context_manager_loads_workspace_rules(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("只修改工作区内文件", encoding="utf-8")
    context_manager = ContextManager(
        system_prompt="测试提示",
        compaction_engine=CompactionEngine(llm_client=LlmClient(SummaryLlm())),
        project_rules=ProjectRules(),
    )

    result = await context_manager.build_system_context(tmp_path)

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
    memory_manager = MemoryManager(tmp_path, MemorySettings(), user_root=tmp_path / "user-memory")
    await memory_manager.add("memory", "保持线性会话")
    context_manager = ContextManager(
        system_prompt="测试提示",
        compaction_engine=CompactionEngine(llm_client=LlmClient(SummaryLlm())),
        skills=skills,
        memory_manager=memory_manager,
    )

    result = await context_manager.build_system_context(tmp_path)

    assert "python-style" in result
    assert "python-style (project)" in result
    assert "Python style" in result
    assert "使用类型注解" not in result
    assert "保持线性会话" in result
    assert "load_skill" in result
    assert "memory_load" not in result


def test_compaction_budget_excludes_response_audit_and_tool_details() -> None:
    """审计元数据不属于模型输入，不应影响压缩触发或摘要文字。"""
    from camel_harness.agent.context.token_counting import model_message_dict
    from camel_harness.llm import LlmUsage, ToolResultMessage

    message = AssistantMessage(
        (TextBlock("回答"),),
        LlmStopReason.STOP,
        provider="provider",
        model="model",
        usage=LlmUsage(100, 20),
    )
    assert model_message_dict(message) == {
        "role": "assistant",
        "content": [{"type": "text", "text": "回答"}],
        "stop_reason": "stop",
    }
    tool = ToolResultMessage(
        "call", "read_file", "预览", details={"content_ref": {"path": "private"}}
    )
    assert "details" not in model_message_dict(tool)


class ScriptedSummaryLlm(RecordingSummaryLlm):
    """通过有序结束原因注入截断、空摘要及缺少结束事件。"""

    def __init__(self, reasons: list[LlmStopReason | None], text: str = "完整摘要") -> None:
        super().__init__()
        self.reasons = reasons
        self.text = text

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        index = len(self.requests)
        self.requests.append(llm_request)
        yield TextDelta(self.text)
        yield UsageUpdated(LlmUsage(100, 20))
        yield UsageUpdated(LlmUsage(100, 30))
        if self.reasons[index] is not None:
            yield MessageCompleted(self.reasons[index])  # type: ignore[arg-type]


async def test_length_retry_has_independent_budget_and_counts_both_requests() -> None:
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.STOP])
    compaction_engine = CompactionEngine(llm_client=LlmClient(adapter))
    charged: list[LlmUsage] = []
    result = await compaction_engine.compact(
        _context_snapshot(),
        _MODEL,
        require_reduction=True,
        on_usage=charged.append,
    )
    assert result is not None
    assert [request.max_output_tokens for request in adapter.requests] == [8192, 16384]
    assert all(request.effort is LlmThinkingEffort.LOW for request in adapter.requests)
    assert charged == [LlmUsage(100, 30), LlmUsage(100, 30)]
    assert result.usage == LlmUsage(200, 60)
    assert "上次摘要" in (adapter.requests[1].system_prompt or "")


@pytest.mark.parametrize(
    "reasons, text, error",
    [
        ([LlmStopReason.LENGTH, LlmStopReason.LENGTH], "截断", "两次"),
        ([LlmStopReason.STOP], "  ", "空摘要"),
        ([None], "未完成", "缺少"),
        ([LlmStopReason.TOOL_USE], "工具请求", "未正常完成"),
    ],
)
async def test_failed_summary_never_returns_a_commit_plan(reasons, text, error) -> None:
    adapter = ScriptedSummaryLlm(reasons, text)
    snapshot = _context_snapshot()
    before = snapshot.nodes
    with pytest.raises(CompactionError, match=error):
        await CompactionEngine(llm_client=LlmClient(adapter)).compact(
            snapshot, _MODEL, require_reduction=True
        )
    assert snapshot.nodes == before
    assert len(adapter.requests) == len(reasons)


async def test_compaction_rejects_summary_input_larger_than_context_window() -> None:
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.STOP])
    compaction_settings = CompactionSettings(
        reserve_tokens=128,
        keep_recent_tokens=140,
        max_output_tokens=64,
        retry_max_output_tokens=128,
    )
    with pytest.raises(CompactionError, match="安全上下文预算"):
        await CompactionEngine(
            llm_client=LlmClient(adapter), compaction_settings=compaction_settings
        ).compact(
            _context_snapshot(count=100),
            replace(_MODEL, context_window_tokens=8192, max_output_tokens=128),
            require_reduction=True,
        )
    assert adapter.requests == []


async def test_retry_budget_is_not_reduced_by_previous_request_usage() -> None:
    snapshot = _context_snapshot()
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.STOP])
    charged: list[LlmUsage] = []
    result = await CompactionEngine(llm_client=LlmClient(adapter)).compact(
        snapshot, _MODEL, require_reduction=True, on_usage=charged.append
    )
    assert result is not None
    assert [request.max_output_tokens for request in adapter.requests] == [8192, 16384]
    assert charged == [LlmUsage(100, 30), LlmUsage(100, 30)]


async def test_oversized_completed_summary_is_not_committed() -> None:
    adapter = ScriptedSummaryLlm([LlmStopReason.STOP], "。" * 300)
    compaction_settings = CompactionSettings(
        reserve_tokens=128_000 - 384,
        keep_recent_tokens=140,
        max_output_tokens=64,
        retry_max_output_tokens=128,
    )
    with pytest.raises(CompactionError, match="仍超过安全预算"):
        await CompactionEngine(
            llm_client=LlmClient(adapter), compaction_settings=compaction_settings
        ).compact(_context_snapshot(), _MODEL, require_reduction=True)
    assert len(adapter.requests) == 1


async def test_cancelled_compaction_propagates_and_accounts_known_usage() -> None:
    started = asyncio.Event()

    class BlockingSummaryLlm(SummaryLlm):
        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            yield UsageUpdated(LlmUsage(10, 2))
            started.set()
            await asyncio.Event().wait()

    charged: list[LlmUsage] = []
    task = asyncio.create_task(
        CompactionEngine(llm_client=LlmClient(BlockingSummaryLlm())).compact(
            _context_snapshot(),
            _MODEL,
            require_reduction=True,
            on_usage=charged.append,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert charged == [LlmUsage(10, 2)]


async def test_disabled_compaction_does_not_call_provider() -> None:
    adapter = RecordingSummaryLlm()
    context_manager = ContextManager(
        compaction_engine=CompactionEngine(
            llm_client=LlmClient(adapter),
            compaction_settings=CompactionSettings(enabled=False),
        ),
        system_prompt="基础",
    )
    assert await context_manager.compact_if_needed(_context_snapshot(), _MODEL) is None
    with pytest.raises(CompactionError, match="未开启"):
        await context_manager.compact_if_needed(
            _context_snapshot(), _MODEL, trigger=CompactionTrigger.CONTEXT_OVERFLOW
        )
    assert not adapter.requests


async def test_previous_summary_is_updated_with_new_history() -> None:
    adapter = RecordingSummaryLlm()
    snapshot = _context_snapshot()
    nodes = (
        ContextNode("summary", UserMessage("[Compacted History]\n\n已有约束")),
    ) + snapshot.nodes
    await CompactionEngine(llm_client=LlmClient(adapter)).compact(
        ContextSnapshot(snapshot.leaf_id, snapshot.revision, nodes),
        _MODEL,
        require_reduction=True,
    )
    assert "更新" in (adapter.requests[0].system_prompt or "")
    assert "已有约束" in adapter.requests[0].messages[0].content


async def test_compaction_timeout_never_retries() -> None:
    requests: list[LlmRequest] = []

    class SlowSummaryLlm(SummaryLlm):
        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            requests.append(llm_request)
            yield TextDelta("未完成")
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await CompactionEngine(llm_client=LlmClient(SlowSummaryLlm())).compact(
                _context_snapshot(), _MODEL, require_reduction=True
            )
    assert len(requests) == 1


def test_cross_attempt_usage_does_not_fabricate_unknown_fields() -> None:
    from camel_harness.agent.context.compaction import _sum_usage

    result = _sum_usage(LlmUsage(), LlmUsage(100, 20))
    assert result.input_tokens is None
    assert result.output_tokens is None


async def test_tool_boundary_is_not_split_by_recent_budget() -> None:
    from camel_harness.llm import ToolResultMessage, ToolUseBlock

    snapshot = ContextSnapshot(
        "last",
        "last",
        (
            ContextNode("first", UserMessage("旧历史")),
            ContextNode(
                "call", AssistantMessage((ToolUseBlock("c", "read", {}),), LlmStopReason.TOOL_USE)
            ),
            ContextNode("result", ToolResultMessage("c", "read", "。" * 100)),
            ContextNode("last", UserMessage("最新请求")),
        ),
    )
    adapter = RecordingSummaryLlm()
    compaction_engine = CompactionEngine(
        llm_client=LlmClient(adapter),
        compaction_settings=CompactionSettings(min_messages=2, keep_recent_tokens=50),
    )
    result = await compaction_engine.compact(snapshot, _MODEL, require_reduction=True)
    assert result is not None
    assert result.first_kept_entry_id == "last"
    transcript = adapter.requests[0].messages[0].content
    assert '"tool_use"' in transcript
    assert '"toolResult"' in transcript


async def test_overflow_retains_entire_final_multi_tool_interval() -> None:
    from camel_harness.llm import ToolResultMessage, ToolUseBlock

    nodes = _context_snapshot(2).nodes + (
        ContextNode(
            "calls",
            AssistantMessage(
                (ToolUseBlock("first", "read", {}), ToolUseBlock("second", "read", {})),
                LlmStopReason.TOOL_USE,
            ),
        ),
        ContextNode("result-1", ToolResultMessage("first", "read", "第一份结果" * 50)),
        ContextNode("result-2", ToolResultMessage("second", "read", "第二份结果" * 50)),
    )
    snapshot = ContextSnapshot("result-2", "result-2", nodes)
    adapter = RecordingSummaryLlm()
    compaction_engine = CompactionEngine(
        llm_client=LlmClient(adapter), compaction_settings=CompactionSettings(keep_recent_tokens=50)
    )

    result = await compaction_engine.compact(snapshot, _MODEL, require_reduction=True)

    assert result is not None
    assert result.first_kept_entry_id == "calls"
    assert '"tool_use"' not in adapter.requests[0].messages[0].content
    assert "第一份结果" not in adapter.requests[0].messages[0].content
    assert snapshot.nodes == nodes


async def test_failed_compaction_keeps_persisted_history_and_emits_one_terminal_event(
    tmp_path,
) -> None:
    from camel_harness import Agent, AgentOptions
    from camel_harness.agent.session import SessionManager
    from camel_harness.agent.session.jsonl import JsonlSessionStorage
    from camel_harness.agent.tools import ToolExecutor, ToolRegistry

    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("failed-compaction", tmp_path, "压缩失败")
    session = session_manager.bind("failed-compaction")
    for node in _context_snapshot().nodes:
        await session.append_message(node.message)
    previous = await session.entries()
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.LENGTH])
    llm_client = LlmClient(adapter)
    tool_registry = ToolRegistry()
    compaction_settings = CompactionSettings(
        reserve_tokens=128_000 - 384,
        keep_recent_tokens=140,
        max_output_tokens=64,
        retry_max_output_tokens=128,
    )
    agent = Agent(
        session=session,
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="测试提示",
            compaction_engine=CompactionEngine(
                llm_client=llm_client, compaction_settings=compaction_settings
            ),
        ),
    )
    events: list = []
    agent.subscribe(events.append)
    await agent.run("继续", model=_MODEL)
    entries = await session.entries()
    assert entries[: len(previous)] == previous
    assert len(entries) == len(previous) + 1
    assert not any(entry.type == "compaction" for entry in entries)
    terminal = [event for event in events if event.type == "agent_end"]
    assert len(terminal) == 1
    assert terminal[0].status == "failed"
    assert "两次" in terminal[0].error
    assert terminal[0].stop_reason == "context_failed"
    assert len(adapter.requests) == 2
    await agent.close()


async def test_recent_and_summary_budgets_are_validated_against_selected_model() -> None:
    with pytest.raises(CompactionError, match="上下文空间"):
        await CompactionEngine(
            LlmClient(SummaryLlm()), CompactionSettings(keep_recent_tokens=120_000)
        ).compact(_context_snapshot(), _MODEL, require_reduction=True)


async def test_summary_cannot_retry_with_identical_capped_budget() -> None:
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.STOP])
    model = replace(_MODEL, max_output_tokens=128)
    with pytest.raises(CompactionError, match="没有增长空间"):
        await CompactionEngine(LlmClient(adapter)).compact(
            _context_snapshot(), model, require_reduction=True
        )
    assert len(adapter.requests) == 1
    assert adapter.requests[0].max_output_tokens == 128
    assert adapter.requests[0].model == model


@pytest.mark.parametrize("stage", ["snapshot", "commit"])
async def test_context_prepare_preserves_session_error_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from camel_harness.agent.context import ContextSessionError

    context_manager = ContextManager(_compaction_engine(), system_prompt="基础")
    session = _session_with_snapshot()
    failure = OSError("会话操作失败")
    operation = session.context_snapshot if stage == "snapshot" else session.compact
    operation.side_effect = failure
    plan = CompactionResult("entry-6", 100, "摘要")
    compact = AsyncMock(return_value=plan)
    monkeypatch.setattr(context_manager, "compact_if_needed", compact)
    with pytest.raises(ContextSessionError, match="会话操作失败") as caught:
        await context_manager.prepare(session, _MODEL, workspace=tmp_path)
    assert caught.value.__cause__ is failure
    if stage == "snapshot":
        compact.assert_not_awaited()
        session.compact.assert_not_awaited()
    else:
        session.compact.assert_awaited_once()


@pytest.mark.parametrize("stage", ["snapshot", "resources", "summary", "commit"])
async def test_context_prepare_does_not_wrap_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    context_manager = ContextManager(_compaction_engine(), system_prompt="基础")
    session = _session_with_snapshot()
    compact = AsyncMock(return_value=CompactionResult("entry-6", 100, "摘要"))
    resources = AsyncMock(return_value="资源")
    monkeypatch.setattr(context_manager, "compact_if_needed", compact)
    monkeypatch.setattr(context_manager, "build_system_context", resources)
    operations = {
        "snapshot": session.context_snapshot,
        "resources": resources,
        "summary": compact,
        "commit": session.compact,
    }
    operations[stage].side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await context_manager.prepare(session, _MODEL, workspace=tmp_path)
    if stage != "commit":
        session.compact.assert_not_awaited()


async def test_context_prepare_without_compaction_never_writes_session(
    tmp_path: Path,
) -> None:
    snapshot = _context_snapshot()
    session = _session_with_snapshot(snapshot)
    context_manager = ContextManager(_compaction_engine(input_limit=100_000), system_prompt="基础")
    prepared = await context_manager.prepare(session, _MODEL, workspace=tmp_path)
    assert prepared.messages == snapshot.messages
    session.compact.assert_not_awaited()


async def test_forced_context_prepare_without_compaction_plan_does_not_write_session(
    tmp_path: Path,
) -> None:
    session = _session_with_snapshot()
    context_manager = _context_manager(enabled=False)
    with pytest.raises(CompactionError, match="未开启"):
        await context_manager.prepare(
            session,
            _MODEL,
            workspace=tmp_path,
            trigger=CompactionTrigger.CONTEXT_OVERFLOW,
        )
    session.compact.assert_not_awaited()
