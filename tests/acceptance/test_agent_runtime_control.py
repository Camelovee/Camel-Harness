import asyncio
from collections.abc import AsyncIterator

import pytest

from camel_harness import Agent, AgentBusyError, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolExecutor, ToolRegistry, validate_tool_arguments
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    UserMessage,
)


class SteerableLlm(LlmAdapter):
    """第一轮等待测试注入 steering，第二轮确认上下文后完成。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            self.started.set()
            await self.release.wait()
            text = "第一轮"
        else:
            text = "已调整"
        yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": text})
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            {"stop_reason": LlmStopReason.STOP.value},
        )


class BlockingLlm(LlmAdapter):
    """发送一个增量后阻塞，供取消传播测试使用。"""

    def __init__(self) -> None:
        self.blocked = asyncio.Event()

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "未完成"})
        self.blocked.set()
        await asyncio.Event().wait()


class CompletingLlm(LlmAdapter):
    """立即完成一次恢复请求。"""

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "继续完成"})
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            {"stop_reason": LlmStopReason.STOP.value},
        )


def build_test_agent(
    tmp_path, session_id: str, adapter: LlmAdapter
) -> tuple[Agent, SessionManager]:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    llm = LlmClient(adapter)
    registry = ToolRegistry()
    agent = Agent(
        session_id,
        tmp_path,
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry, validate_tool_arguments),
        sessions,
        ContextManager(CompactionEngine(llm)),
    )
    return agent, sessions


async def test_steering_is_injected_after_current_turn(tmp_path) -> None:
    runtime = SteerableLlm()
    agent, sessions = build_test_agent(tmp_path, "session-steer", runtime)

    events: list = []
    unsubscribe = agent.subscribe(events.append)
    task = asyncio.create_task(agent.run("先分析"))
    await runtime.started.wait()
    queued = await agent.steer("改为只给结论")
    runtime.release.set()
    await task
    unsubscribe()

    assert queued.run_id == events[0].run_id
    assert [event.type for event in events].count("turn_start") == 2
    messages = await sessions.messages("session-steer")
    assert [message.role for message in messages] == ["user", "assistant", "user", "assistant"]
    assert isinstance(messages[2], UserMessage)
    assert messages[2].content == "改为只给结论"
    assert runtime.requests[1].messages[-1] == messages[2]


async def test_abort_emits_cancelled_end_without_persisting_partial_assistant(tmp_path) -> None:
    runtime = BlockingLlm()
    agent, sessions = build_test_agent(tmp_path, "session-abort", runtime)

    events: list = []
    unsubscribe = agent.subscribe(events.append)
    task = asyncio.create_task(agent.run("开始任务"))
    await runtime.blocked.wait()
    await asyncio.sleep(0)
    assert await agent.abort()
    await task
    unsubscribe()

    assert events[-1].type == "agent_end"
    assert events[-1].status == "cancelled"
    assert [message.role for message in await sessions.messages("session-abort")] == ["user"]
    assert not agent.is_running


async def test_cancelling_waiter_does_not_take_ownership_of_run(tmp_path) -> None:
    """取消等待 run() 的调用方，不得隐式取消 Agent 自己持有的任务。"""

    runtime = BlockingLlm()
    agent, _ = build_test_agent(tmp_path, "session-waiter-cancel", runtime)
    events: list = []
    unsubscribe = agent.subscribe(events.append)
    waiter = asyncio.create_task(agent.run("继续运行"))
    await runtime.blocked.wait()

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert agent.is_running
    assert await agent.abort()
    await agent.wait_for_idle()
    unsubscribe()
    assert events[-1].type == "agent_end"
    assert events[-1].status == "cancelled"


async def test_resume_continues_from_persisted_user_message(tmp_path) -> None:
    runtime = CompletingLlm()
    agent, sessions = build_test_agent(tmp_path, "session-resume", runtime)
    await sessions.ensure("session-resume", tmp_path, "恢复测试")
    await sessions.append_message("session-resume", UserMessage("继续这个任务"))

    events: list = []
    unsubscribe = agent.subscribe(events.append)
    await agent.resume()
    unsubscribe()

    assert events[0].type == "agent_start"
    assert events[-1].status == "completed"
    assert runtime.requests[0].messages == (UserMessage("继续这个任务"),)
    assert [message.role for message in await sessions.messages("session-resume")] == [
        "user",
        "assistant",
    ]


async def test_agent_rejects_a_second_active_run(tmp_path) -> None:
    runtime = BlockingLlm()
    agent, _ = build_test_agent(tmp_path, "session-busy", runtime)
    first = asyncio.create_task(agent.run("第一个任务"))
    await runtime.blocked.wait()

    with pytest.raises(AgentBusyError):
        await agent.run("第二个任务")

    await agent.abort()
    await first
