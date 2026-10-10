import asyncio
from collections.abc import AsyncIterator

import pytest

from camel_harness import Agent, AgentBusyError, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolExecutor, ToolRegistry
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmUsage,
    MessageCompleted,
    StreamFailed,
    TextDelta,
    UsageUpdated,
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
        yield TextDelta(text=text)
        yield MessageCompleted(LlmStopReason.STOP, model=None)


class BlockingLlm(LlmAdapter):
    """发送一个增量后阻塞，供取消传播测试使用。"""

    def __init__(self) -> None:
        self.blocked = asyncio.Event()

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield TextDelta(text="未完成")
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
        yield TextDelta(text="继续完成")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


async def build_test_agent(
    tmp_path, session_id: str, adapter: LlmAdapter
) -> tuple[Agent, SessionManager]:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    llm = LlmClient(adapter)
    registry = ToolRegistry()
    await sessions.create(session_id, tmp_path, "运行控制测试")
    agent = Agent(
        sessions.bind(session_id),
        tmp_path,
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry),
        ContextManager(CompactionEngine(llm)),
    )
    return agent, sessions


async def test_steering_is_injected_after_current_turn(tmp_path) -> None:
    runtime = SteerableLlm()
    agent, sessions = await build_test_agent(tmp_path, "session-steer", runtime)

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
    messages = await sessions.bind("session-steer").messages()
    assert [message.role for message in messages] == ["user", "assistant", "user", "assistant"]
    assert isinstance(messages[2], UserMessage)
    assert messages[2].content == "改为只给结论"
    assert runtime.requests[1].messages[-1] == messages[2]


async def test_abort_emits_cancelled_end_without_persisting_partial_assistant(tmp_path) -> None:
    runtime = BlockingLlm()
    agent, sessions = await build_test_agent(tmp_path, "session-abort", runtime)

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
    assert [message.role for message in await sessions.bind("session-abort").messages()] == ["user"]
    assert not agent.is_running


async def test_cancelling_waiter_does_not_take_ownership_of_run(tmp_path) -> None:
    """取消等待 run() 的调用方，不得隐式取消 Agent 自己持有的任务。"""

    runtime = BlockingLlm()
    agent, _ = await build_test_agent(tmp_path, "session-waiter-cancel", runtime)
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
    agent, sessions = await build_test_agent(tmp_path, "session-resume", runtime)
    await sessions.ensure("session-resume", tmp_path, "恢复测试")
    await sessions.bind("session-resume").append_message(UserMessage("继续这个任务"))

    events: list = []
    unsubscribe = agent.subscribe(events.append)
    await agent.resume()
    unsubscribe()

    assert events[0].type == "agent_start"
    assert events[-1].status == "completed"
    assert runtime.requests[0].messages == (UserMessage("继续这个任务"),)
    assert [message.role for message in await sessions.bind("session-resume").messages()] == [
        "user",
        "assistant",
    ]


async def test_agent_rejects_a_second_active_run(tmp_path) -> None:
    runtime = BlockingLlm()
    agent, _ = await build_test_agent(tmp_path, "session-busy", runtime)
    first = asyncio.create_task(agent.run("第一个任务"))
    await runtime.blocked.wait()

    with pytest.raises(AgentBusyError):
        await agent.run("第二个任务")

    await agent.abort()
    await first


async def test_busy_resume_does_not_read_session(tmp_path, monkeypatch) -> None:
    """活动 Run 的恢复请求先被拒绝，不读取正在变化的历史。"""

    runtime = BlockingLlm()
    agent, _ = await build_test_agent(tmp_path, "busy-resume", runtime)
    first = asyncio.create_task(agent.run("当前任务"))
    await runtime.blocked.wait()
    original = agent._session.messages

    async def unexpected_read():
        raise AssertionError("活动 Run 不应生成恢复计划")

    monkeypatch.setattr(agent._session, "messages", unexpected_read)
    try:
        with pytest.raises(AgentBusyError):
            await agent.resume()
    finally:
        monkeypatch.setattr(agent._session, "messages", original)
        await agent.abort()
        await first


async def test_resume_snapshot_and_run_registration_share_start_lock(tmp_path, monkeypatch):
    """恢复读取期间另一个启动必须等待，不能抢先修改恢复边界。"""

    runtime = BlockingLlm()
    agent, sessions = await build_test_agent(tmp_path, "resume-start-lock", runtime)
    await sessions.bind("resume-start-lock").append_message(UserMessage("待恢复任务"))
    entered, release = asyncio.Event(), asyncio.Event()
    original = agent._session.messages

    async def delayed_read():
        entered.set()
        await release.wait()
        return await original()

    monkeypatch.setattr(agent._session, "messages", delayed_read)
    resumed = asyncio.create_task(agent.resume())
    await entered.wait()
    competing = asyncio.create_task(agent.run("竞争任务"))
    await asyncio.sleep(0)
    assert not competing.done()
    release.set()
    try:
        with pytest.raises(AgentBusyError):
            await competing
        await runtime.blocked.wait()
    finally:
        await agent.abort()
        await resumed
    messages = await sessions.bind("resume-start-lock").messages()
    assert messages == (UserMessage("待恢复任务"),)


async def test_message_end_is_delivered_after_persistence_with_contiguous_sequence(tmp_path):
    """完成事件确认已提交消息，事件构造不提前消耗保存期间的序号。"""

    agent, sessions = await build_test_agent(tmp_path, "saved-message-events", CompletingLlm())
    events = []

    async def observe(event):
        if event.type == "message_end":
            messages = await sessions.bind("saved-message-events").messages()
            assert messages[-1] == event.message
        events.append(event)

    agent.subscribe(observe)
    await agent.run("完成任务")
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.message.role for event in events if event.type == "message_end"] == [
        "user",
        "assistant",
    ]
    assert events[-1].status == "completed"


async def test_failed_message_save_does_not_emit_message_end(tmp_path, monkeypatch):
    """保存失败没有完成确认，并保留会话失败的唯一终态。"""

    runtime = CompletingLlm()
    agent, _ = await build_test_agent(tmp_path, "failed-message-save", runtime)
    events = []
    agent.subscribe(events.append)

    async def failed_save(message):
        raise OSError("会话写入失败")

    monkeypatch.setattr(agent._session, "append_message", failed_save)
    await agent.run("任务")
    assert not runtime.requests
    assert not any(event.type == "message_end" for event in events)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].stop_reason == "session_failed"
    assert sum(event.type == "agent_end" for event in events) == 1


async def test_subscriber_failure_does_not_break_persistence_or_duplicate_terminal(
    tmp_path,
) -> None:
    agent, sessions = await build_test_agent(tmp_path, "observer-failure", CompletingLlm())
    events: list = []
    calls = 0

    def broken(event) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("观察者故障")

    agent.subscribe(broken)
    agent.subscribe(events.append)
    await agent.run("完成")
    assert calls == 1
    assert len(await sessions.bind("observer-failure").messages()) == 2
    assert len([event for event in events if event.type == "agent_end"]) == 1
    assert events[-1].status == "completed"
    assert not agent.is_running


async def test_terminal_observer_failure_does_not_emit_a_second_terminal(tmp_path) -> None:
    agent, _ = await build_test_agent(tmp_path, "terminal-failure", CompletingLlm())
    events: list = []

    async def broken(event) -> None:
        if event.type == "agent_end":
            raise RuntimeError("终态观察者故障")

    agent.subscribe(broken)
    agent.subscribe(events.append)
    await agent.run("完成")
    await agent.wait_for_idle()
    assert [event.type for event in events].count("agent_end") == 1
    assert events[-1].status == "completed"
    assert not agent.is_running


async def test_repair_failure_still_reports_terminal_and_cleans_run(tmp_path, monkeypatch) -> None:
    runtime = BlockingLlm()
    agent, _ = await build_test_agent(tmp_path, "repair-failure", runtime)
    events: list = []
    agent.subscribe(events.append)

    async def broken_repair(run) -> None:
        raise OSError("日志不可写")

    monkeypatch.setattr(agent, "_repair_dangling_tool_calls", broken_repair)
    task = asyncio.create_task(agent.run("开始"))
    await runtime.blocked.wait()
    assert await agent.abort()
    assert await agent.abort()
    await task
    assert [event.type for event in events].count("agent_end") == 1
    assert events[-1].status == "cancelled"
    assert "修复失败" in events[-1].error
    assert not agent.is_running


async def test_large_usage_does_not_stop_completed_response(tmp_path) -> None:
    class LargeUsageLlm(CompletingLlm):
        async def stream(self, request):
            yield UsageUpdated(LlmUsage(500_000, 100_000))
            async for event in super().stream(request):
                yield event

    agent, sessions = await build_test_agent(tmp_path, "large-usage", LargeUsageLlm())
    events: list = []
    agent.subscribe(events.append)
    await agent.run("完成")
    assert events[-1].status == "completed"
    assert events[-1].total_tokens == 600_000
    assert len(await sessions.bind("large-usage").messages()) == 2


async def test_slow_subscriber_is_removed_without_hanging_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("camel_harness.agent.events._LISTENER_TIMEOUT_SECONDS", 0.01)
    agent, _ = await build_test_agent(tmp_path, "slow-observer", CompletingLlm())
    calls = 0
    events: list = []

    async def blocked(event):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()

    agent.subscribe(blocked)
    agent.subscribe(events.append)
    await asyncio.wait_for(agent.run("完成"), 1)
    assert calls == 1
    assert events[-1].status == "completed"


async def test_subscriber_self_cancellation_does_not_cancel_run(tmp_path) -> None:
    agent, sessions = await build_test_agent(tmp_path, "observer-cancel", CompletingLlm())
    calls = 0
    events = []

    async def cancelled_listener(event):
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    agent.subscribe(cancelled_listener)
    agent.subscribe(events.append)
    await agent.run("完成")
    assert calls == 1
    assert events[-1].status == "completed"
    assert len(await sessions.bind("observer-cancel").messages()) == 2


async def test_sync_and_async_subscribers_preserve_order_and_unsubscribe_is_idempotent(tmp_path):
    agent, _ = await build_test_agent(tmp_path, "observer-order", CompletingLlm())
    delivered = []

    async def async_listener(event):
        await asyncio.sleep(0)
        delivered.append((event.sequence, "async"))

    unsubscribe = agent.subscribe(lambda event: delivered.append((event.sequence, "removed")))
    unsubscribe()
    unsubscribe()
    agent.subscribe(lambda event: delivered.append((event.sequence, "sync")))
    agent.subscribe(async_listener)
    await agent.run("完成")
    assert delivered
    assert [kind for _, kind in delivered] == ["sync", "async"] * (len(delivered) // 2)
    assert all(
        delivered[index][0] == delivered[index + 1][0] for index in range(0, len(delivered), 2)
    )


async def test_failed_response_keeps_known_usage_but_not_partial_message(tmp_path):
    class FailedUsageLlm(CompletingLlm):
        async def stream(self, request):
            yield TextDelta("未完成")
            yield UsageUpdated(LlmUsage(120, 30))
            yield StreamFailed("模型故障")

    agent, sessions = await build_test_agent(tmp_path, "failed-usage", FailedUsageLlm())
    events: list = []
    agent.subscribe(events.append)
    await agent.run("开始")
    assert events[-1].status == "failed"
    assert events[-1].total_tokens == 150
    assert [message.role for message in await sessions.bind("failed-usage").messages()] == ["user"]


async def test_steering_refreshes_dynamic_context_without_accumulating_sections(
    tmp_path, monkeypatch
):
    runtime = SteerableLlm()
    agent, _ = await build_test_agent(tmp_path, "refresh-context", runtime)
    queries: list[str] = []

    async def build_context(prompt, workspace):
        queries.append(prompt)
        return f"动态上下文：{prompt}"

    monkeypatch.setattr(agent._context, "build_system_context", build_context)
    task = asyncio.create_task(agent.run("先分析", system_prompt="基础提示"))
    await runtime.started.wait()
    await agent.steer("改为测试")
    runtime.release.set()
    await task
    assert queries == ["先分析", "改为测试"]
    assert runtime.requests[0].system_prompt == "基础提示\n\n动态上下文：先分析"
    assert runtime.requests[1].system_prompt == "基础提示\n\n动态上下文：改为测试"


async def test_resource_failure_is_not_classified_as_provider_failure(tmp_path, monkeypatch):
    agent, _ = await build_test_agent(tmp_path, "resource-error", CompletingLlm())
    events: list = []
    agent.subscribe(events.append)

    async def failed_context(*args):
        raise OSError("规则无法读取")

    monkeypatch.setattr(agent._context, "build_system_context", failed_context)
    await agent.run("开始")
    assert events[-1].stop_reason == "resource_failed"
    assert events[-1].error == "规则无法读取"


async def test_new_prompt_does_not_read_raw_history_for_resource_query(tmp_path, monkeypatch):
    agent, sessions = await build_test_agent(tmp_path, "new-query", CompletingLlm())
    queries = []

    async def unexpected_read():
        raise AssertionError("新输入不应额外读取原始历史")

    async def build_context(query, workspace):
        queries.append(query)
        return ""

    monkeypatch.setattr(agent._session, "messages", unexpected_read)
    monkeypatch.setattr(agent._context, "build_system_context", build_context)
    events = []
    agent.subscribe(events.append)
    await agent.run("当前任务")
    assert events[-1].status == "completed"
    assert queries == ["当前任务"]
    assert len(await sessions.bind("new-query").messages()) == 2


async def test_resume_uses_context_manager_to_select_last_user_query(tmp_path, monkeypatch):
    agent, sessions = await build_test_agent(tmp_path, "resume-query", CompletingLlm())
    await sessions.bind("resume-query").append_message(UserMessage("继续实现测试"))
    queries = []

    async def build_context(query, workspace):
        queries.append(query)
        return ""

    monkeypatch.setattr(agent._context, "build_system_context", build_context)
    await agent.resume()
    assert queries == ["继续实现测试"]


async def test_close_failure_allows_cleanup_retry(tmp_path):
    agent, _ = await build_test_agent(tmp_path, "close-retry", CompletingLlm())
    attempts = 0

    async def close_resource():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("临时关闭失败")

    agent._on_close = close_resource
    with pytest.raises(OSError):
        await agent.close()
    await agent.close()
    await agent.close()
    assert attempts == 2


async def test_cancelling_close_waiter_does_not_cancel_resource_cleanup(tmp_path):
    agent, _ = await build_test_agent(tmp_path, "close-waiter", CompletingLlm())
    entered, release = asyncio.Event(), asyncio.Event()

    async def close_resource():
        entered.set()
        await release.wait()

    agent._on_close = close_resource
    waiter = asyncio.create_task(agent.close())
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await agent.close()
    assert agent._closed
