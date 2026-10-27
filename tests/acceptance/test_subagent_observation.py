"""离线验证父流中的子任务过程、审批和收尾持久化。"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from camel_backend.api.sse import encode_sse
from camel_backend.approval import WebApproval
from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.events import AgentEvent
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.subagent import LocalSubagentRunner
from camel_harness.agent.subagent.tool import DelegateTaskTool
from camel_harness.agent.tools import PermissionService, ToolExecutor, ToolRegistry
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    MessageCompleted,
    ReasoningDelta,
    StreamFailed,
    TextDelta,
    ToolCallCompleted,
    ToolUseBlock,
)

_MODEL = LlmModel("fake", "fake")


class DelegatingLlm(LlmAdapter):
    """父子请求分别编排；不访问真实模型。"""

    def __init__(self, failure: bool = False) -> None:
        self.parent_calls = 0
        self.child_calls = 0
        self.failure = failure

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if llm_request.system_prompt.startswith("你是 general 子 Agent"):
            self.child_calls += 1
            if self.failure:
                yield TextDelta("尚未完成的子文本")
                yield StreamFailed("模拟子模型异常")
                return
            if self.child_calls == 1:
                yield ReasoningDelta("先写入目标文件")
                yield TextDelta("子任务开始工作")
                yield ToolCallCompleted(
                    ToolUseBlock(
                        "write",
                        "write_file",
                        {
                            "path": "result.txt",
                            "content": "已批准的内容",
                        },
                    )
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            else:
                yield TextDelta("子任务完成")
                yield MessageCompleted(LlmStopReason.STOP)
        else:
            self.parent_calls += 1
            if self.parent_calls == 1:
                yield ToolCallCompleted(
                    ToolUseBlock(
                        "delegate",
                        "delegate_task",
                        {
                            "task": "创建 result.txt",
                            "agent_type": "general",
                        },
                    )
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            else:
                yield TextDelta("父任务完成")
                yield MessageCompleted(LlmStopReason.STOP)


async def build_parent(
    tmp_path: Path,
    adapter: DelegatingLlm,
    *,
    approval_timeout: float = 2,
    delegation_timeout: float = 300,
) -> tuple[Agent, SessionManager, WebApproval]:
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父任务")
    llm_client = LlmClient(adapter)
    context_manager = ContextManager(
        system_prompt="父任务提示", compaction_engine=CompactionEngine(llm_client)
    )
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    web_approval = WebApproval(approval_timeout)
    permission_service = PermissionService(web_approval)
    subagent_runner = LocalSubagentRunner(
        llm_client,
        session_manager,
        tool_registry,
        permission_service,
        context_manager,
        AgentOptions(),
    )
    delegate = DelegateTaskTool(subagent_runner.run)
    delegate.timeout_seconds = delegation_timeout
    tool_registry.register(delegate)
    agent = Agent(
        session_manager.bind("parent"),
        tmp_path,
        AgentOptions(),
        llm_client,
        ToolExecutor(tool_registry, permission_service),
        context_manager,
    )
    return agent, session_manager, web_approval


@pytest.mark.parametrize("decision", ["approve", "deny", "expire", "cancel", "delegate_timeout"])
async def test_child_approval_is_visible_and_parent_stream_survives_child_end(
    tmp_path: Path,
    decision: str,
) -> None:
    agent, session_manager, web_approval = await build_parent(
        tmp_path,
        DelegatingLlm(),
        approval_timeout=0.04 if decision == "expire" else 2,
        delegation_timeout=0.08 if decision == "delegate_timeout" else 300,
    )
    events: list[AgentEvent] = []
    queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

    def observe(event: AgentEvent) -> None:
        events.append(event)
        queue.put_nowait(event)

    agent.subscribe(observe)
    task = asyncio.create_task(agent.run("执行委派", model=_MODEL))
    child_id = ""
    try:
        async with asyncio.timeout(5):
            while True:
                event = await queue.get()
                if event.type == "approval_requested":
                    assert await web_approval.resolve(event.request_id, True)
                elif event.type == "subagent_event":
                    child_id = str(event.event["session_id"])
                    assert event.session_id == "parent"
                    assert event.call_id == "delegate"
                    assert event.event["run_id"] != event.run_id
                    payload = json.loads(encode_sse(event).split("data: ")[1])
                    assert payload["event"]["session_id"] == child_id
                    if event.event["type"] == "approval_requested":
                        assert not (tmp_path / "result.txt").exists()
                        pending = await web_approval.list_pending()
                        assert pending[0].session_id == child_id
                        if decision in {"approve", "deny"}:
                            assert await web_approval.resolve(
                                pending[0].request_id, decision == "approve"
                            )
                        elif decision == "cancel":
                            await agent.abort()
                elif event.type == "agent_end":
                    break
            await task
        assert (tmp_path / "result.txt").exists() is (decision == "approve")
        assert await web_approval.list_pending() == ()
        assert [event.sequence for event in events] == sorted({event.sequence for event in events})
        child_events = [event.event for event in events if event.type == "subagent_event"]
        assert [event["sequence"] for event in child_events] == sorted(
            {event["sequence"] for event in child_events}
        )
        assert child_events[-1]["type"] == "agent_end"
        assert events[-1].type == "agent_end"
        assert events[-1].status == ("cancelled" if decision == "cancel" else "completed")
        if decision in {"approve", "deny", "expire"}:
            resolved = [event for event in child_events if event["type"] == "approval_resolved"]
            assert len(resolved) == 1
            assert resolved[0]["approved"] is (decision == "approve")
        restarted = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
        metadata = await restarted.get(child_id)
        assert metadata is not None and metadata.task_outcome is not None
        assert metadata.task_outcome.status == (
            "cancelled" if decision in {"cancel", "delegate_timeout"} else "completed"
        )
        parent_messages = await session_manager.bind("parent").messages()
        # 子过程只向观察者传递，不成为父会话模型历史。
        assert "先写入目标文件" not in repr(parent_messages)
    finally:
        await agent.close()
        await asyncio.gather(task, return_exceptions=True)


async def test_child_failure_keeps_terminal_and_discards_unfinished_text(tmp_path: Path) -> None:
    agent, session_manager, web_approval = await build_parent(tmp_path, DelegatingLlm(failure=True))

    async def approve_parent(event: AgentEvent) -> None:
        if event.type == "approval_requested":
            await web_approval.resolve(event.request_id, True)

    agent.subscribe(approve_parent)
    try:
        await agent.run("执行委派", model=_MODEL)
        child = next(item for item in await session_manager.list() if item.parent_session_id)
        assert child.task_outcome is not None
        assert child.task_outcome.status == "failed"
        assert child.task_outcome.error == "模拟子模型异常"
        assert "尚未完成的子文本" not in repr(
            await session_manager.bind(child.session_id).messages()
        )
    finally:
        await agent.close()


async def test_terminal_storage_failure_stops_parent_instead_of_reporting_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, session_manager, web_approval = await build_parent(tmp_path, DelegatingLlm(failure=True))
    events: list[AgentEvent] = []

    async def observe(event: AgentEvent) -> None:
        events.append(event)
        if event.type == "approval_requested":
            await web_approval.resolve(event.request_id, True)

    async def broken_update(session_id: str, **fields: Any) -> None:
        raise OSError("模拟保存故障")

    monkeypatch.setattr(session_manager, "update", broken_update)
    agent.subscribe(observe)
    try:
        await agent.run("执行委派", model=_MODEL)
        assert events[-1].type == "agent_end"
        assert events[-1].status == "failed"
        assert events[-1].stop_reason == "tool_failed"
        assert "终态保存失败" in (events[-1].error or "")
    finally:
        await agent.close()
