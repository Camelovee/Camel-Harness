from collections.abc import AsyncIterator

import pytest

from camel_harness import AgentOptions
from camel_harness.harness.compaction import (
    CompactionEngine,
    CompactionTrigger,
    ContextManager,
)
from camel_harness.harness.resources import ProjectRules
from camel_harness.harness.session import SessionLog, SessionLogEvent
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStreamEvent,
    LlmStreamEventType,
)
from camel_harness.types import Message, MessageRole, ReasoningBlock, TextBlock

_OPTIONS = AgentOptions("fake", "fake-model")


class SummaryLlm(LlmAdapter):
    """为压缩测试返回固定摘要。"""

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(
            LlmStreamEventType.TEXT_DELTA,
            request.session_id,
            request.turn_id,
            request.step_id,
            1,
            "fake",
            {"text": "保留目标、决策和待办。"},
        )
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            request.session_id,
            request.turn_id,
            request.step_id,
            2,
            "fake",
        )


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


def _log(count: int = 8) -> SessionLog:
    events = tuple(
        SessionLogEvent.append(
            index,
            index,
            Message.text(
                MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                f"消息 {index}",
            ),
        )
        for index in range(count)
    )
    return SessionLog("session-1", events)


async def test_basic_compaction_summarizes_old_three_quarters() -> None:
    engine = _engine()

    result = await engine.compact_if_needed(_log(), "turn-1", _OPTIONS)

    assert result is not None
    assert result.start_seq == 0
    assert result.end_seq == 5
    assert result.summary_message.role is MessageRole.USER
    assert isinstance(result.summary_message.content[0], TextBlock)
    assert "保留目标、决策和待办。" in result.summary_message.content[0].text


async def test_basic_compaction_skips_history_below_pressure() -> None:
    result = await _engine(threshold=100_000).compact_if_needed(_log(), "turn-1", _OPTIONS)

    assert result is None


async def test_context_overflow_trigger_ignores_pressure_threshold() -> None:
    result = await _engine(threshold=100_000).compact_if_needed(
        _log(), "turn-1", _OPTIONS, trigger=CompactionTrigger.CONTEXT_OVERFLOW
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
    reasoning = Message(
        MessageRole.ASSISTANT,
        (ReasoningBlock("不应重新注入的推理"), TextBlock("可见结论")),
    )
    log = SessionLog(
        "session-1",
        (
            SessionLogEvent.append(0, 0, reasoning),
            SessionLogEvent.append(1, 1, Message.text(MessageRole.USER, "继续")),
            SessionLogEvent.append(2, 2, Message.text(MessageRole.ASSISTANT, "结果")),
        ),
    )

    await engine.compact_if_needed(
        log,
        "turn-1",
        _OPTIONS,
        trigger=CompactionTrigger.CONTEXT_OVERFLOW,
    )

    transcript = adapter.requests[0].messages[1].content[0]
    assert isinstance(transcript, TextBlock)
    assert "可见结论" in transcript.text
    assert "不应重新注入的推理" not in transcript.text


def test_basic_compaction_rejects_invalid_options() -> None:
    llm = LlmClient(SummaryLlm())
    with pytest.raises(ValueError, match="必须大于 0"):
        CompactionEngine(llm, pressure_threshold_chars=0)
    with pytest.raises(ValueError, match="不能小于 2"):
        CompactionEngine(llm, min_messages_to_compact=1)


async def test_context_manager_loads_workspace_rules(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("只修改工作区内文件", encoding="utf-8")
    context = ContextManager(CompactionEngine(LlmClient(SummaryLlm())), ProjectRules())

    result = await context.build("继续实现", tmp_path)

    assert "# 项目规则" in result
    assert "只修改工作区内文件" in result
