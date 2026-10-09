import asyncio
from collections.abc import AsyncIterator

import pytest

from camel_harness import AgentOptions
from camel_harness.agent.config import CompactionSettings
from camel_harness.agent.context import (
    CompactionEngine,
    CompactionTrigger,
    ContextManager,
)
from camel_harness.agent.context.compaction import CompactionError
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
    LlmUsage,
    MessageCompleted,
    ReasoningBlock,
    TextBlock,
    TextDelta,
    UsageUpdated,
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
    threshold: int = 384,
    min_messages: int = 8,
) -> CompactionEngine:
    llm = LlmClient(SummaryLlm())
    return CompactionEngine(
        llm,
        settings=CompactionSettings(
            context_window_tokens=max(4096, threshold + 128),
            reserve_tokens=max(4096, threshold + 128) - threshold,
            keep_recent_tokens=140,
            max_output_tokens=64,
            retry_max_output_tokens=128,
            min_messages=min_messages,
        ),
    )


def _log(count: int = 8) -> ContextSnapshot:
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
    engine = _engine()

    result = await engine.compact_if_needed(_log(), _OPTIONS)

    assert result is not None
    assert result.first_kept_entry_id == "entry-6"
    assert result.tokens_before > 0
    assert "保留目标、决策和待办。" in result.summary


async def test_basic_compaction_skips_history_below_pressure() -> None:
    result = await _engine(threshold=100_000).compact_if_needed(_log(), _OPTIONS)

    assert result is None


async def test_short_history_cannot_bypass_context_capacity_check():
    snapshot = ContextSnapshot("entry", "entry", (ContextNode("entry", UserMessage("。" * 5000)),))
    with pytest.raises(CompactionError, match="没有足够"):
        await _engine().compact_if_needed(snapshot, _OPTIONS)


async def test_disabled_compaction_does_not_disable_capacity_guard():
    settings = CompactionSettings(enabled=False)
    snapshot = ContextSnapshot(
        "entry", "entry", (ContextNode("entry", UserMessage("。" * 130_000)),)
    )
    with pytest.raises(CompactionError, match="未开启"):
        await CompactionEngine(LlmClient(SummaryLlm()), settings).compact_if_needed(
            snapshot, _OPTIONS
        )


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
        settings=CompactionSettings(min_messages=2),
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
    with pytest.raises(ValueError, match="context_window_tokens"):
        CompactionSettings(context_window_tokens=0)
    with pytest.raises(ValueError, match="min_messages"):
        CompactionSettings(min_messages=1)
    with pytest.raises(ValueError, match="重试预算"):
        CompactionSettings(max_output_tokens=8192, retry_max_output_tokens=4096)
    with pytest.raises(ValueError, match="上下文空间"):
        CompactionSettings(keep_recent_tokens=120_000)


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


class ScriptedSummaryLlm(RecordingSummaryLlm):
    """通过有序结束原因注入截断、空摘要及缺少结束事件。"""

    def __init__(self, reasons: list[LlmStopReason | None], text: str = "完整摘要") -> None:
        super().__init__()
        self.reasons = reasons
        self.text = text

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        index = len(self.requests)
        self.requests.append(request)
        yield TextDelta(self.text)
        yield UsageUpdated(LlmUsage(100, 20))
        yield UsageUpdated(LlmUsage(100, 30))
        if self.reasons[index] is not None:
            yield MessageCompleted(self.reasons[index])  # type: ignore[arg-type]


async def test_length_retry_has_independent_budget_and_counts_both_requests() -> None:
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.STOP])
    engine = CompactionEngine(LlmClient(adapter))
    charged: list[LlmUsage] = []
    result = await engine.compact_if_needed(
        _log(),
        AgentOptions("fake", max_output_tokens=1024),
        trigger=CompactionTrigger.CONTEXT_OVERFLOW,
        on_usage=charged.append,
    )
    assert result is not None
    assert [request.max_output_tokens for request in adapter.requests] == [8192, 16384]
    assert all(not request.thinking for request in adapter.requests)
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
    snapshot = _log()
    before = snapshot.nodes
    with pytest.raises(CompactionError, match=error):
        await CompactionEngine(LlmClient(adapter)).compact_if_needed(
            snapshot, _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW
        )
    assert snapshot.nodes == before
    assert len(adapter.requests) == len(reasons)


async def test_compaction_rejects_summary_input_larger_than_context_window() -> None:
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.STOP])
    settings = CompactionSettings(
        context_window_tokens=512,
        reserve_tokens=128,
        keep_recent_tokens=140,
        max_output_tokens=64,
        retry_max_output_tokens=128,
    )
    with pytest.raises(CompactionError, match="上下文窗口"):
        await CompactionEngine(LlmClient(adapter), settings).compact_if_needed(
            _log(), _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW
        )
    assert adapter.requests == []


async def test_retry_budget_is_not_reduced_by_previous_request_usage() -> None:
    snapshot = _log()
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.STOP])
    charged: list[LlmUsage] = []
    result = await CompactionEngine(LlmClient(adapter)).compact_if_needed(
        snapshot, _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW, on_usage=charged.append
    )
    assert result is not None
    assert [request.max_output_tokens for request in adapter.requests] == [8192, 16384]
    assert charged == [LlmUsage(100, 30), LlmUsage(100, 30)]


async def test_oversized_completed_summary_is_not_committed() -> None:
    adapter = ScriptedSummaryLlm([LlmStopReason.STOP], "。" * 300)
    settings = CompactionSettings(
        context_window_tokens=4096,
        reserve_tokens=3712,
        keep_recent_tokens=140,
        max_output_tokens=64,
        retry_max_output_tokens=128,
    )
    with pytest.raises(CompactionError, match="仍超过安全预算"):
        await CompactionEngine(LlmClient(adapter), settings).compact_if_needed(
            _log(), _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW
        )
    assert len(adapter.requests) == 1


async def test_cancelled_compaction_propagates_and_accounts_known_usage() -> None:
    started = asyncio.Event()

    class BlockingSummaryLlm(SummaryLlm):
        async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            yield UsageUpdated(LlmUsage(10, 2))
            started.set()
            await asyncio.Event().wait()

    charged: list[LlmUsage] = []
    task = asyncio.create_task(
        CompactionEngine(LlmClient(BlockingSummaryLlm())).compact_if_needed(
            _log(), _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW, on_usage=charged.append
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert charged == [LlmUsage(10, 2)]


async def test_disabled_compaction_does_not_call_provider() -> None:
    adapter = RecordingSummaryLlm()
    result = await CompactionEngine(
        LlmClient(adapter), CompactionSettings(enabled=False)
    ).compact_if_needed(_log(), _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW)
    assert result is None
    assert not adapter.requests


async def test_previous_summary_is_updated_with_new_history() -> None:
    adapter = RecordingSummaryLlm()
    snapshot = _log()
    nodes = (
        ContextNode("summary", UserMessage("[Compacted History]\n\n已有约束")),
    ) + snapshot.nodes
    await CompactionEngine(LlmClient(adapter)).compact_if_needed(
        ContextSnapshot(snapshot.leaf_id, snapshot.revision, nodes),
        _OPTIONS,
        trigger=CompactionTrigger.CONTEXT_OVERFLOW,
    )
    assert "更新" in (adapter.requests[0].system_prompt or "")
    assert "已有约束" in adapter.requests[0].messages[0].content


async def test_compaction_timeout_never_retries() -> None:
    requests: list[LlmRequest] = []

    class SlowSummaryLlm(SummaryLlm):
        async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            requests.append(request)
            yield TextDelta("未完成")
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await CompactionEngine(LlmClient(SlowSummaryLlm())).compact_if_needed(
                _log(), _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW
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
    engine = CompactionEngine(
        LlmClient(adapter), CompactionSettings(min_messages=2, keep_recent_tokens=50)
    )
    result = await engine.compact_if_needed(
        snapshot, _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW
    )
    assert result is not None
    assert result.first_kept_entry_id == "last"
    transcript = adapter.requests[0].messages[0].content
    assert '"tool_use"' in transcript
    assert '"toolResult"' in transcript


async def test_failed_compaction_keeps_persisted_history_and_emits_one_terminal_event(
    tmp_path,
) -> None:
    from camel_harness import Agent
    from camel_harness.agent.session import SessionManager
    from camel_harness.agent.session.jsonl import JsonlSessionStorage
    from camel_harness.agent.tools import ToolExecutor, ToolRegistry

    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("failed-compaction", tmp_path, "压缩失败")
    session = sessions.bind("failed-compaction")
    for node in _log().nodes:
        await session.append_message(node.message)
    previous = await session.entries()
    adapter = ScriptedSummaryLlm([LlmStopReason.LENGTH, LlmStopReason.LENGTH])
    llm = LlmClient(adapter)
    registry = ToolRegistry()
    settings = CompactionSettings(
        context_window_tokens=4096,
        reserve_tokens=3712,
        keep_recent_tokens=140,
        max_output_tokens=64,
        retry_max_output_tokens=128,
    )
    agent = Agent(
        session,
        tmp_path,
        _OPTIONS,
        llm,
        registry,
        ToolExecutor(registry),
        ContextManager(CompactionEngine(llm, settings)),
    )
    events: list = []
    agent.subscribe(events.append)
    await agent.run("继续")
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
