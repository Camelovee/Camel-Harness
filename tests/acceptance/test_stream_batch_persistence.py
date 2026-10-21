import asyncio
from collections.abc import AsyncIterator

from camel_harness import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import (
    ToolExecutor,
    ToolRegistry,
)
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    MessageCompleted,
    TextDelta,
)

_MODEL = LlmModel("fake", "fake-model")


class TwoChunkLlm(LlmAdapter):
    @property
    def provider_id(self) -> str:
        return "fake"

    """输出两个文本增量，用于观察生成器暂停时的持久化状态。"""

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield TextDelta(text="你")
        yield TextDelta(text="好")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


class InterruptedLlm(LlmAdapter):
    @property
    def provider_id(self) -> str:
        return "fake"

    """发送一个增量后中断，用于验证不完整消息不会落盘。"""

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield TextDelta(text="未完成")
        raise ConnectionError("流中断")


async def test_stream_is_emitted_before_llm_events_are_batch_persisted(tmp_path) -> None:
    root = tmp_path / "global-sessions"
    session_manager = SessionManager(JsonlSessionStorage(root))
    tool_registry = ToolRegistry()
    llm_client = LlmClient(TwoChunkLlm())
    context_manager = ContextManager(
        system_prompt="system", compaction_engine=CompactionEngine(llm_client=llm_client)
    )
    await session_manager.create("session-1", tmp_path, "测试批量存储")
    agent = Agent(
        session=session_manager.bind("session-1"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=context_manager,
    )
    events: list = []
    first_delta = asyncio.Event()
    release = asyncio.Event()

    async def observe(event) -> None:
        events.append(event)
        if event.type == "message_end":
            assert event.message in await session_manager.bind("session-1").messages()
        if (
            event.type == "message_update"
            and event.assistant_event.type == LlmStreamEventType.TEXT_DELTA
            and not first_delta.is_set()
        ):
            first_delta.set()
            await release.wait()

    unsubscribe = agent.subscribe(observe)
    run_task = asyncio.create_task(agent.run("测试批量存储", model=_MODEL))
    await first_delta.wait()
    first = events[-1]

    assert first.type == "message_update"
    assert first.assistant_event.text == "你"
    before_completion = await session_manager.bind("session-1").messages()
    assert [message.role for message in before_completion] == ["user"]

    release.set()
    await run_task
    unsubscribe()

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.type for event in events[-2:]] == ["turn_end", "agent_end"]
    persisted = await session_manager.bind("session-1").messages()
    assert [message.role for message in persisted] == [
        "user",
        "assistant",
    ]
    assert persisted[-1].provider == "fake"
    assert persisted[-1].model == "fake-model"
    assert persisted[-1].response_model is None
    assert persisted[-1].usage is None
    stored_lines = (root / "session-1" / "session.jsonl").read_text("utf-8").splitlines()
    assert len(stored_lines) == 3
    assert all("stream_event_recorded" not in line for line in stored_lines)
    metadata = await session_manager.get("session-1")
    assert metadata is not None
    assert metadata.name == "测试批量存储"
    assert metadata.workspace == str(tmp_path.resolve())
    assert metadata.message_count == 2


async def test_interrupted_llm_stream_does_not_persist_partial_assistant(tmp_path) -> None:
    """Provider 异常终止时只能保留已完整接收的 user 消息。"""

    root = tmp_path / "global-sessions"
    session_manager = SessionManager(JsonlSessionStorage(root))
    tool_registry = ToolRegistry()
    llm_client = LlmClient(InterruptedLlm())
    context_manager = ContextManager(
        system_prompt="system", compaction_engine=CompactionEngine(llm_client=llm_client)
    )
    await session_manager.create("session-failed", tmp_path, "测试失败流")
    agent = Agent(
        session=session_manager.bind("session-failed"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=context_manager,
    )
    events: list = []
    unsubscribe = agent.subscribe(events.append)
    await agent.run("测试失败流", max_turns=1, model=_MODEL)
    unsubscribe()

    first = next(event for event in events if event.type == "message_update")
    assert first.assistant_event.type == LlmStreamEventType.TEXT_DELTA
    failed = events[-1]
    assert failed.type == "agent_end"
    assert failed.status == "failed"
    assert failed.error == "流中断"

    persisted = await session_manager.bind("session-failed").messages()
    assert [message.role for message in persisted] == ["user"]


async def test_failed_save_is_not_reported_as_completed_message(tmp_path, monkeypatch) -> None:
    """落盘失败不得让外部订阅者误认为 assistant 消息已经完成保存。"""
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("session-1", tmp_path, "保存失败")
    session = session_manager.bind("session-1")
    append_message = session.append_message

    async def fail_assistant(message) -> None:
        if message.role == "assistant":
            raise OSError("模拟写入失败")
        await append_message(message)

    monkeypatch.setattr(session, "append_message", fail_assistant)
    tool_registry = ToolRegistry()
    llm_client = LlmClient(TwoChunkLlm())
    agent = Agent(
        session=session,
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="system", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )
    events: list = []
    agent.subscribe(events.append)
    await agent.run("测试保存错误", model=_MODEL)
    assert [event.message.role for event in events if event.type == "message_end"] == ["user"]
    assert [message.role for message in await session.messages()] == ["user"]
    assert events[-1].type == "agent_end"
    assert events[-1].status == "failed"
    assert events[-1].error == "模拟写入失败"
    assert events[-1].stop_reason == "session_failed"
