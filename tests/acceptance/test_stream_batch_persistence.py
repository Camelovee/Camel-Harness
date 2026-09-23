import asyncio
from collections.abc import AsyncIterator

from camel_harness import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import (
    ToolExecutor,
    ToolRegistry,
    validate_tool_arguments,
)
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
)


class TwoChunkLlm(LlmAdapter):
    @property
    def provider_id(self) -> str:
        return "fake"

    """输出两个文本增量，用于观察生成器暂停时的持久化状态。"""

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "你"})
        yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "好"})
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            {"stop_reason": LlmStopReason.STOP.value},
        )


class InterruptedLlm(LlmAdapter):
    @property
    def provider_id(self) -> str:
        return "fake"

    """发送一个增量后中断，用于验证不完整消息不会落盘。"""

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "未完成"})
        raise ConnectionError("流中断")


async def test_stream_is_emitted_before_llm_events_are_batch_persisted(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    sessions = SessionManager(JsonlSessionStorage(root))
    registry = ToolRegistry()
    llm = LlmClient(TwoChunkLlm())
    context = ContextManager(CompactionEngine(llm))
    agent = Agent(
        "session-1",
        tmp_path,
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry, validate_tool_arguments),
        sessions,
        context,
    )
    events: list = []
    first_delta = asyncio.Event()
    release = asyncio.Event()

    async def observe(event) -> None:
        events.append(event)
        if (
            event.type == "message_update"
            and event.assistant_event.type is LlmStreamEventType.TEXT_DELTA
            and not first_delta.is_set()
        ):
            first_delta.set()
            await release.wait()

    unsubscribe = agent.subscribe(observe)
    run_task = asyncio.create_task(agent.run("测试批量存储"))
    await first_delta.wait()
    first = events[-1]

    assert first.type == "message_update"
    assert first.assistant_event.data["text"] == "你"
    before_completion = await sessions.messages("session-1")
    assert [message.role for message in before_completion] == ["user"]

    release.set()
    await run_task
    unsubscribe()

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.type for event in events[-2:]] == ["turn_end", "agent_end"]
    persisted = await sessions.messages("session-1")
    assert [message.role for message in persisted] == [
        "user",
        "assistant",
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
        "session-failed",
        tmp_path,
        AgentOptions("fake-model", max_turns=1),
        llm,
        registry,
        ToolExecutor(registry, validate_tool_arguments),
        sessions,
        context,
    )
    events: list = []
    unsubscribe = agent.subscribe(events.append)
    await agent.run("测试失败流", system_prompt="system")
    unsubscribe()

    first = next(event for event in events if event.type == "message_update")
    assert first.assistant_event.type is LlmStreamEventType.TEXT_DELTA
    failed = events[-1]
    assert failed.type == "agent_end"
    assert failed.status == "failed"
    assert failed.error == "流中断"

    persisted = await sessions.messages("session-failed")
    assert [message.role for message in persisted] == ["user"]
