from collections.abc import AsyncIterator

from backend.agent import Agent, AgentLoop, AgentOptions, AgentStreamEventType
from backend.compaction import CompactionEngine, ContextManager
from backend.harness import Harness
from backend.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStreamEvent,
    LlmStreamEventType,
)
from backend.session import SessionManager
from backend.session.jsonl import JsonlSessionStorage
from backend.tools import McpManager, ToolExecutor, ToolRegistry
from backend.tools.jsonschema_validator import validate_tool_arguments
from backend.types import MessageRole


class TwoChunkLlm(LlmAdapter):
    @property
    def provider_id(self) -> str:
        return "fake"

    """输出两个文本增量，用于观察生成器暂停时的持久化状态。"""

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        common = {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "step_id": request.step_id,
            "provider": "fake",
        }
        yield LlmStreamEvent(
            LlmStreamEventType.TEXT_DELTA,
            sequence=1,
            data={"text": "你"},
            **common,
        )
        yield LlmStreamEvent(
            LlmStreamEventType.TEXT_DELTA,
            sequence=2,
            data={"text": "好"},
            **common,
        )
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            sequence=3,
            data={},
            **common,
        )


class InterruptedLlm(LlmAdapter):
    @property
    def provider_id(self) -> str:
        return "fake"

    """发送一个增量后中断，用于验证不完整消息不会落盘。"""

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(
            LlmStreamEventType.TEXT_DELTA,
            request.session_id,
            request.turn_id,
            request.step_id,
            1,
            "fake",
            {"text": "未完成"},
        )
        raise ConnectionError("流中断")


async def test_stream_is_emitted_before_llm_events_are_batch_persisted(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    sessions = SessionManager(JsonlSessionStorage(root))
    registry = ToolRegistry()
    llm = LlmClient(TwoChunkLlm())
    context = ContextManager(CompactionEngine(llm))
    agent = Agent(
        AgentOptions("fake", "fake-model"),
        AgentLoop(llm, registry, ToolExecutor(registry, validate_tool_arguments)),
    )
    harness = Harness(
        agent,
        sessions,
        context,
        McpManager(tmp_path, tmp_path / ".mcp.json"),
        _no_workspace,
    )
    stream = harness.run_turn("session-1", "测试批量存储", tmp_path)

    first = await anext(stream)

    assert first.type is AgentStreamEventType.TEXT_DELTA
    assert first.data["text"] == "你"
    before_completion = await sessions.messages("session-1")
    assert [message.role for message in before_completion] == [MessageRole.USER]

    remaining = [event async for event in stream]

    assert [event.sequence for event in (first, *remaining)] == [1, 2, 3, 4, 5]
    assert [event.type.value for event in remaining[-2:]] == [
        "step_completed",
        "turn_completed",
    ]
    persisted = await sessions.messages("session-1")
    assert [message.role for message in persisted] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    stored_lines = (root / "session-1" / "messages.jsonl").read_text("utf-8").splitlines()
    assert len(stored_lines) == 2
    assert all("stream_event_recorded" not in line for line in stored_lines)
    metadata = await sessions.get("session-1")
    assert metadata is not None
    assert metadata.name == "测试批量存储"
    assert metadata.workspace == str(tmp_path.resolve())
    assert metadata.message_count == 2


async def test_interrupted_llm_stream_does_not_persist_partial_assistant(tmp_path) -> None:
    """Provider 异常终止时只能保留已完整接收的 user 消息。"""

    root = tmp_path / "global-sessions"
    sessions = SessionManager(JsonlSessionStorage(root))
    registry = ToolRegistry()
    llm = LlmClient(InterruptedLlm())
    context = ContextManager(CompactionEngine(llm))
    agent = Agent(
        AgentOptions("fake", "fake-model", max_steps=1),
        AgentLoop(llm, registry, ToolExecutor(registry, validate_tool_arguments)),
    )
    harness = Harness(
        agent,
        sessions,
        context,
        McpManager(tmp_path, tmp_path / ".mcp.json"),
        _no_workspace,
    )
    stream = harness.run_turn("session-failed", "测试失败流", tmp_path, system_prompt="system")

    first = await anext(stream)
    assert first.type.value == "text_delta"
    failed = await anext(stream)
    assert failed.type.value == "turn_failed"
    try:
        await anext(stream)
    except ConnectionError:
        pass
    else:
        raise AssertionError("流中断必须向调用方传播")

    persisted = await sessions.messages("session-failed")
    assert [message.role for message in persisted] == [MessageRole.USER]


async def _no_workspace() -> None:
    return None
