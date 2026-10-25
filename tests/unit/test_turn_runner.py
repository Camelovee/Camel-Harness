from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from camel_harness.agent.context import CompactionTrigger, ContextResourceError, PreparedContext
from camel_harness.agent.session import Session
from camel_harness.agent.session_committer import SessionCommitter
from camel_harness.agent.state import AgentRun, StopReason
from camel_harness.agent.tools import PermissionMode, ToolCall, ToolResult
from camel_harness.agent.turn_runner import TurnOutcome, TurnRunner
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmErrorCode,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmThinkingEffort,
    MessageCompleted,
    StreamFailed,
    TextDelta,
    ToolCallCompleted,
    ToolUseBlock,
    UserMessage,
)

_MODEL = LlmModel("fake", "fake-model")


class ScriptedLlm(LlmAdapter):
    """按请求顺序返回预先编排的流事件。"""

    def __init__(self, scripts: list[list[LlmStreamEvent]]) -> None:
        self._scripts = scripts
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(llm_request)
        for event in self._scripts[len(self.requests) - 1]:
            yield event


class RecordingContextManager:
    """记录触发原因并为每次准备返回不同的历史。"""

    def __init__(self) -> None:
        self.triggers: list[CompactionTrigger] = []

    async def prepare(
        self,
        session: Session,
        model: LlmModel,
        *,
        workspace: Path,
        tool_definitions=(),
        on_usage=None,
        trigger: CompactionTrigger = CompactionTrigger.PRESSURE,
    ) -> PreparedContext:
        self.triggers.append(trigger)
        return PreparedContext("系统提示", (UserMessage(f"历史{len(self.triggers)}"),))


class RecordingToolExecutor:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.calls: list[ToolCall] = []

    def llm_definitions(self):
        return ()

    async def execute(self, tool_call, tool_context, *, on_approval=None) -> ToolResult:
        self.calls.append(tool_call)
        return self.result


def _runner(
    adapter: ScriptedLlm,
    *,
    context_manager: RecordingContextManager | None = None,
    tool_executor: RecordingToolExecutor | None = None,
) -> tuple[TurnRunner, AgentRun, list, AsyncMock]:
    session = AsyncMock(spec=Session)
    events: list = []

    async def emit(event) -> None:
        events.append(event)

    agent_run = AgentRun("session", "run")
    agent_run.start()
    committer = SessionCommitter(session, agent_run, emit)
    runner = TurnRunner(
        session=session,
        workspace=Path("/tmp"),
        llm_client=LlmClient(adapter),
        tool_executor=tool_executor or RecordingToolExecutor(ToolResult("call", "read", "完成")),
        context_manager=context_manager or RecordingContextManager(),
        committer=committer,
        emit=emit,
        agent_run=agent_run,
    )
    return runner, agent_run, events, session


async def test_run_turn_streams_and_returns_stop_reason() -> None:
    adapter = ScriptedLlm([[TextDelta("完成"), MessageCompleted(LlmStopReason.STOP)]])
    runner, agent_run, events, session = _runner(adapter)

    outcome = await runner.run_turn(
        model=_MODEL,
        permission_mode=PermissionMode.DEFAULT,
        effort=LlmThinkingEffort.LOW,
        user_message=UserMessage("任务"),
    )

    assert outcome == TurnOutcome(LlmStopReason.STOP, False)
    assert agent_run.turn == 1
    assert [event.type for event in events] == [
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_end",
        "turn_end",
    ]
    assert session.append_message.await_count == 2


async def test_overflow_retries_once_with_new_context() -> None:
    adapter = ScriptedLlm(
        [
            [StreamFailed("上下文超限", code=LlmErrorCode.CONTEXT_OVERFLOW)],
            [TextDelta("恢复"), MessageCompleted(LlmStopReason.STOP)],
        ]
    )
    context_manager = RecordingContextManager()
    runner, agent_run, events, session = _runner(adapter, context_manager=context_manager)

    outcome = await runner.run_turn(
        model=_MODEL,
        permission_mode=PermissionMode.DEFAULT,
        effort=LlmThinkingEffort.LOW,
        user_message=UserMessage("任务"),
    )

    assert outcome.stop_reason is LlmStopReason.STOP
    assert agent_run.context_overflow_recovery_used
    assert context_manager.triggers == [
        CompactionTrigger.PRESSURE,
        CompactionTrigger.CONTEXT_OVERFLOW,
    ]
    assert [request.messages for request in adapter.requests] == [
        (UserMessage("历史1"),),
        (UserMessage("历史2"),),
    ]
    assert [event.type for event in events].count("message_start") == 2
    assert session.append_message.await_count == 2


async def test_truncated_tool_call_is_closed_without_execution() -> None:
    adapter = ScriptedLlm(
        [
            [
                ToolCallCompleted(ToolUseBlock("call", "read", {})),
                MessageCompleted(LlmStopReason.LENGTH),
            ]
        ]
    )
    tool_executor = RecordingToolExecutor(ToolResult("call", "read", "不应执行"))
    runner, _agent_run, events, session = _runner(adapter, tool_executor=tool_executor)

    outcome = await runner.run_turn(
        model=_MODEL,
        permission_mode=PermissionMode.DEFAULT,
        effort=LlmThinkingEffort.LOW,
        user_message=None,
    )

    assert outcome == TurnOutcome(LlmStopReason.LENGTH, True)
    assert tool_executor.calls == []
    turn_end = events[-1]
    assert turn_end.type == "turn_end"
    assert turn_end.tool_results[0].is_error
    assert turn_end.tool_results[0].details["execution_status"] == "not_executed"
    assert session.append_message.await_count == 2


async def test_tool_call_is_executed_and_result_is_persisted() -> None:
    adapter = ScriptedLlm(
        [
            [
                ToolCallCompleted(ToolUseBlock("call", "read", {})),
                MessageCompleted(LlmStopReason.TOOL_USE),
            ]
        ]
    )
    tool_executor = RecordingToolExecutor(ToolResult("call", "read", "结果"))
    runner, _agent_run, events, session = _runner(adapter, tool_executor=tool_executor)

    outcome = await runner.run_turn(
        model=_MODEL,
        permission_mode=PermissionMode.DEFAULT,
        effort=LlmThinkingEffort.LOW,
        user_message=UserMessage("任务"),
    )

    assert outcome == TurnOutcome(LlmStopReason.TOOL_USE, True)
    assert [call.call_id for call in tool_executor.calls] == ["call"]
    assert [event.type for event in events] == [
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "message_start",
        "message_end",
        "turn_end",
    ]
    assert session.append_message.await_count == 3


async def test_context_resource_failure_marks_run() -> None:
    class FailingContextManager:
        async def prepare(self, *args, **kwargs) -> PreparedContext:
            raise ContextResourceError("规则读取失败")

    adapter = ScriptedLlm([[TextDelta("不会到达"), MessageCompleted(LlmStopReason.STOP)]])
    runner, agent_run, _events, _session = _runner(adapter)
    runner._context_manager = FailingContextManager()

    with pytest.raises(ContextResourceError, match="规则读取失败"):
        await runner.run_turn(
            model=_MODEL,
            permission_mode=PermissionMode.DEFAULT,
            effort=LlmThinkingEffort.LOW,
            user_message=None,
        )

    assert agent_run.stop_reason is StopReason.RESOURCE_FAILED
