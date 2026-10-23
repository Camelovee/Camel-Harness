import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest

from camel_harness import Agent, AgentOptions, StopReason
from camel_harness.agent.config import CompactionSettings
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.resources import ProjectRules
from camel_harness.agent.session import Session, SessionCompactionEvent, SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import PermissionMode, ToolExecutor, ToolRegistry
from camel_harness.agent.tools.types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmErrorCode,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmThinkingEffort,
    LlmUsage,
    MessageCompleted,
    StreamFailed,
    StreamStarted,
    TextBlock,
    TextDelta,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
    UsageUpdated,
    UserMessage,
)

_MODEL = LlmModel("fake", "fake-model")


class MarkerTool(Tool):
    """写入次数必须可观察，防止幂等写文件掩盖历史工具重执行。"""

    name = "append_marker"
    description = "追加一次标记"
    parameters = {"type": "object", "additionalProperties": False}
    capabilities = frozenset({"file.write"})
    risk = ToolRisk.WRITE

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        with (tool_context.workspace / "marker.txt").open("a", encoding="utf-8") as stream:
            stream.write("执行一次\n")
        return ToolResult(tool_call.call_id, tool_call.name, "已追加标记")


class RecoveryLlm(LlmAdapter):
    """分别编排主回答和摘要，走真实 ContextManager 与 JSONL 提交。"""

    def __init__(self, steps: list[str], summary: str = "complete") -> None:
        self.steps = steps
        self.summary = summary
        self.requests: list[LlmRequest] = []
        self.summaries: list[LlmRequest] = []
        self.blocked = asyncio.Event()
        self.cancelled = asyncio.Event()

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if (llm_request.system_prompt or "").startswith("你负责压缩 Agent"):
            self.summaries.append(llm_request)
            step = self.summary
            usage = LlmUsage(17, 3)
            text = "已完成历史任务，保留用户约束并继续当前任务。"
            if step == "larger":
                text = "巨大摘要" * 2000
        else:
            index = len(self.requests)
            self.requests.append(llm_request)
            step = self.steps[index]
            usage = LlmUsage(11, 5)
            text = "任务完成"
        if step == "overflow":
            yield StreamFailed("请求上下文超限", code=LlmErrorCode.CONTEXT_OVERFLOW)
            return
        if step == "generic_error":
            yield StreamFailed("上下文相关字段无效")
            return
        if step in {"partial", "started", "usage", "partial_tool"}:
            if step == "partial":
                yield TextDelta("不完整回答")
            elif step == "started":
                yield StreamStarted("fake-model")
            elif step == "partial_tool":
                yield ToolCallCompleted(ToolUseBlock("incomplete", "append_marker", {}))
            else:
                yield UsageUpdated(usage)
            yield StreamFailed("流开始后超限", code=LlmErrorCode.CONTEXT_OVERFLOW)
            return
        if step == "tool":
            yield ToolCallCompleted(ToolUseBlock("marker", "append_marker", {}))
            yield UsageUpdated(usage)
            yield MessageCompleted(LlmStopReason.TOOL_USE)
            return
        if step == "truncated_tool":
            yield ToolCallCompleted(
                ToolUseBlock(f"truncated-{len(self.requests)}", "append_marker", {})
            )
            yield UsageUpdated(usage)
            yield MessageCompleted(LlmStopReason.LENGTH)
            return
        yield UsageUpdated(usage)
        if step == "blocked":
            self.blocked.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        yield TextDelta("" if step == "empty" else text)
        if step != "missing_end":
            yield MessageCompleted(LlmStopReason.LENGTH if step == "length" else LlmStopReason.STOP)


async def _build(
    workspace: Path,
    adapter: RecoveryLlm,
    *,
    history_count: int = 6,
    enabled: bool = True,
    max_turns: int = 1,
    max_duration: float = 10,
) -> tuple[Agent, Session]:
    session_manager = SessionManager(JsonlSessionStorage(workspace / "sessions"))
    await session_manager.create("overflow", workspace, "超限恢复")
    session = session_manager.bind("overflow")
    for index in range(history_count):
        text = f"历史 {index} " + "旧任务已完成，记录约束。" * 60
        await session.append_message(
            UserMessage(text)
            if index % 2 == 0
            else AssistantMessage((TextBlock(text),), LlmStopReason.STOP)
        )
    llm_client = LlmClient(adapter)
    tool_registry = ToolRegistry()
    tool_registry.register(MarkerTool())
    agent = Agent(
        session=session,
        workspace=workspace,
        agent_options=AgentOptions(max_duration_seconds=max_duration),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="基础提示",
            compaction_engine=CompactionEngine(
                llm_client=llm_client, compaction_settings=CompactionSettings(enabled=enabled)
            ),
            project_rules=ProjectRules(),
        ),
    )
    return agent, session


def _assert_terminal(events, status: str, reason: StopReason) -> None:
    terminals = [event for event in events if event.type == "agent_end"]
    assert len(terminals) == 1
    assert events[-1] is terminals[0]
    assert terminals[0].status == status
    assert terminals[0].stop_reason is reason
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.run_id for event in events}) == 1


async def test_overflow_compacts_short_history_and_retries_in_same_turn(tmp_path):
    (tmp_path / "AGENTS.md").write_text("项目规则只出现一次", encoding="utf-8")
    adapter = RecoveryLlm(["overflow", "complete"])
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)

    await agent.run("继续任务", max_turns=1, effort=LlmThinkingEffort.HIGH, model=_MODEL)

    _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
    assert len(adapter.requests) == 2
    assert len(adapter.summaries) == 1
    first, retry = adapter.requests
    assert len(first.messages) == 7  # 默认 min_messages=8 不能阻止已确认超限的恢复。
    assert retry.messages[0].content.startswith("[Compacted History]\n")
    assert retry.messages[-1] == UserMessage("继续任务")
    assert len(retry.messages) < len(first.messages)
    assert retry.model == first.model
    assert retry.tools == first.tools
    assert retry.max_output_tokens == first.max_output_tokens == _MODEL.max_output_tokens
    assert retry.effort == first.effort is LlmThinkingEffort.HIGH
    assert retry.system_prompt == first.system_prompt
    assert retry.system_prompt.count("基础提示") == 1
    assert retry.system_prompt.count("项目规则只出现一次") == 1
    assert len([event for event in events if event.type == "turn_start"]) == 1
    assistant_starts = [
        event for event in events if event.type == "message_start" and event.role == "assistant"
    ]
    assistant_ends = [
        event
        for event in events
        if event.type == "message_end" and isinstance(event.message, AssistantMessage)
    ]
    assert len(assistant_starts) == len(assistant_ends) == 1
    assert assistant_starts[0].message_id == assistant_ends[0].message_id
    assert events[-1].total_tokens == 36  # 摘要 20 + 成功回答 16。
    assert len(await session.messages()) == 8
    assert sum(message == UserMessage("继续任务") for message in await session.messages()) == 1
    assert (
        len(
            [
                entry
                for entry in await session.entries()
                if isinstance(entry, SessionCompactionEvent)
            ]
        )
        == 1
    )


async def test_overflow_after_tool_keeps_call_and_result_without_reexecuting(tmp_path):
    adapter = RecoveryLlm(["tool", "overflow", "complete"])
    agent, session = await _build(tmp_path, adapter, max_turns=2)
    events = []
    agent.subscribe(events.append)
    await agent.run(
        "写入一次", max_turns=2, permission_mode=PermissionMode.ACCEPT_EDITS, model=_MODEL
    )

    _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
    assert (tmp_path / "marker.txt").read_text("utf-8") == "执行一次\n"
    assert len(adapter.requests) == 3
    assert len(adapter.summaries) == 1
    retained = adapter.requests[-1].messages
    assert isinstance(retained[-2], AssistantMessage)
    assert retained[-2].content == (ToolUseBlock("marker", "append_marker", {}),)
    assert isinstance(retained[-1], ToolResultMessage)
    assert retained[-1].call_id == "marker"
    assert sum(isinstance(message, ToolResultMessage) for message in await session.messages()) == 1
    assert len([event for event in events if event.type == "tool_execution_start"]) == 1
    assert len([event for event in events if event.type == "turn_start"]) == 2
    assert events[-1].total_tokens == 52


@pytest.mark.parametrize("steps", [["overflow", "overflow"], ["overflow", "tool", "overflow"]])
async def test_recovery_limit_applies_to_entire_run_including_later_turns(tmp_path, steps):
    adapter = RecoveryLlm(steps)
    agent, session = await _build(tmp_path, adapter, max_turns=2)
    events = []
    agent.subscribe(events.append)
    await agent.run("任务", max_turns=2, permission_mode=PermissionMode.ACCEPT_EDITS, model=_MODEL)

    _assert_terminal(events, "failed", StopReason.CONTEXT_FAILED)
    assert "恢复次数已用尽" in events[-1].error
    assert len(adapter.requests) == len(steps)
    assert len(adapter.summaries) == 1
    assert (
        len(
            [
                entry
                for entry in await session.entries()
                if isinstance(entry, SessionCompactionEvent)
            ]
        )
        == 1
    )
    if "tool" in steps:
        assert (tmp_path / "marker.txt").read_text("utf-8") == "执行一次\n"


@pytest.mark.parametrize("step", ["partial", "started", "usage", "partial_tool", "generic_error"])
@pytest.mark.parametrize("recover_first", [False, True])
async def test_started_or_unclassified_failure_never_compacts_or_saves_partial_message(
    tmp_path, step, recover_first
):
    adapter = RecoveryLlm(["overflow", step] if recover_first else [step])
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)
    await agent.run("任务", max_turns=1, model=_MODEL)

    _assert_terminal(events, "failed", StopReason.PROVIDER_FAILED)
    assert len(adapter.requests) == (2 if recover_first else 1)
    assert len(adapter.summaries) == (1 if recover_first else 0)
    assert len(await session.messages()) == 7
    assert not (tmp_path / "marker.txt").exists()
    assert events[-1].total_tokens == (20 if recover_first else 0) + (16 if step == "usage" else 0)


@pytest.mark.parametrize("enabled, history_count", [(False, 6), (True, 0)])
async def test_disabled_or_uncompressible_context_does_not_resend_request(
    tmp_path, enabled, history_count
):
    adapter = RecoveryLlm(["overflow"])
    agent, session = await _build(tmp_path, adapter, enabled=enabled, history_count=history_count)
    events = []
    agent.subscribe(events.append)
    await agent.run("无法压缩的请求", max_turns=1, model=_MODEL)

    _assert_terminal(events, "failed", StopReason.CONTEXT_FAILED)
    assert len(adapter.requests) == 1
    assert not adapter.summaries
    assert len(await session.messages()) == history_count + 1
    assert not any(isinstance(entry, SessionCompactionEvent) for entry in await session.entries())


@pytest.mark.parametrize("summary", ["overflow", "length", "empty", "missing_end", "larger"])
async def test_failed_summary_never_commits_or_retries_main_request(tmp_path, summary):
    adapter = RecoveryLlm(["overflow"], summary)
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)
    await agent.run("任务", max_turns=1, model=_MODEL)

    _assert_terminal(events, "failed", StopReason.CONTEXT_FAILED)
    assert len(adapter.requests) == 1
    assert len(adapter.summaries) == (2 if summary == "length" else 1)
    assert len(await session.messages()) == 7
    assert not any(isinstance(entry, SessionCompactionEvent) for entry in await session.entries())
    assert events[-1].total_tokens == (
        0 if summary == "overflow" else 40 if summary == "length" else 20
    )


async def test_failed_recovery_commit_preserves_session_error_and_known_usage(
    tmp_path, monkeypatch
):
    adapter = RecoveryLlm(["overflow"])
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)

    async def fail(*args, **kwargs):
        raise OSError("压缩落盘失败")

    monkeypatch.setattr(session, "compact", fail)
    await agent.run("任务", max_turns=1, model=_MODEL)
    _assert_terminal(events, "failed", StopReason.SESSION_FAILED)
    assert events[-1].error == "压缩落盘失败"
    assert events[-1].total_tokens == 20
    assert len(adapter.requests) == len(adapter.summaries) == 1
    assert not any(isinstance(entry, SessionCompactionEvent) for entry in await session.entries())


@pytest.mark.parametrize("blocked_stage", ["summary", "retry"])
async def test_cancel_during_recovery_stops_provider_and_accounts_usage(tmp_path, blocked_stage):
    adapter = RecoveryLlm(
        ["overflow"] if blocked_stage == "summary" else ["overflow", "blocked"],
        "blocked" if blocked_stage == "summary" else "complete",
    )
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)
    task = asyncio.create_task(agent.run("任务", max_turns=1, model=_MODEL))
    await asyncio.wait_for(adapter.blocked.wait(), timeout=1)
    assert await agent.abort()
    await asyncio.wait_for(task, timeout=1)

    _assert_terminal(events, "cancelled", StopReason.CANCELLED)
    assert adapter.cancelled.is_set()
    assert not agent.is_running
    assert len(await session.messages()) == 7
    assert len(adapter.summaries) == 1
    assert len(adapter.requests) == (1 if blocked_stage == "summary" else 2)
    assert events[-1].total_tokens == (20 if blocked_stage == "summary" else 36)
    assert sum(isinstance(entry, SessionCompactionEvent) for entry in await session.entries()) == (
        0 if blocked_stage == "summary" else 1
    )


async def test_recovery_obeys_run_timeout(tmp_path):
    adapter = RecoveryLlm(["overflow"], "blocked")
    agent, session = await _build(tmp_path, adapter, max_duration=0.1)
    events = []
    agent.subscribe(events.append)
    await asyncio.wait_for(agent.run("任务", max_turns=1, model=_MODEL), timeout=1)

    _assert_terminal(events, "failed", StopReason.TIMEOUT)
    assert adapter.cancelled.is_set()
    assert len(adapter.requests) == len(adapter.summaries) == 1
    assert events[-1].total_tokens == 20
    assert not any(isinstance(entry, SessionCompactionEvent) for entry in await session.entries())


async def test_steering_waits_until_recovered_turn_finishes(tmp_path):
    adapter = RecoveryLlm(["overflow", "complete", "complete"])
    agent, session = await _build(tmp_path, adapter, max_turns=2)
    events = []

    async def observe(event):
        events.append(event)
        if event.type == "message_update" and isinstance(event.assistant_event, StreamFailed):
            await agent.steer("后续任务")

    agent.subscribe(observe)
    await agent.run("原始任务", max_turns=2, model=_MODEL)
    _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
    assert adapter.requests[1].messages[-1] == UserMessage("原始任务")
    assert adapter.requests[2].messages[-1] == UserMessage("后续任务")
    assert (await session.messages())[-2] == UserMessage("后续任务")


async def test_recovery_does_not_bypass_turn_limit_after_tools(tmp_path):
    adapter = RecoveryLlm(["overflow", "tool"])
    agent, _ = await _build(tmp_path, adapter, max_turns=1)
    events = []
    agent.subscribe(events.append)
    await agent.run("任务", max_turns=1, permission_mode=PermissionMode.ACCEPT_EDITS, model=_MODEL)
    _assert_terminal(events, "failed", StopReason.MAX_TURNS)
    assert len(adapter.requests) == 2
    assert len(adapter.summaries) == 1
    assert (tmp_path / "marker.txt").read_text("utf-8") == "执行一次\n"


async def test_new_run_gets_fresh_recovery_allowance(tmp_path):
    adapter = RecoveryLlm(["overflow", "complete", "overflow", "complete"])
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)
    await agent.run("第一个任务" * 100, max_turns=1, model=_MODEL)
    _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
    events.clear()
    await agent.run("第二个任务", max_turns=1, model=_MODEL)
    _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
    assert len(adapter.requests) == 4
    assert len(adapter.summaries) == 2
    assert (
        len(
            [
                entry
                for entry in await session.entries()
                if isinstance(entry, SessionCompactionEvent)
            ]
        )
        == 2
    )


@pytest.mark.parametrize(
    "steps",
    [
        ["truncated_tool", "overflow", "truncated_tool", "truncated_tool"],
        ["overflow", "truncated_tool", "truncated_tool", "truncated_tool"],
    ],
)
async def test_length_and_overflow_recoveries_have_independent_run_allowances(
    tmp_path: Path, steps: list[str]
) -> None:
    adapter = RecoveryLlm(steps)
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)

    await agent.run("继续任务", max_turns=4, model=_MODEL)

    _assert_terminal(events, "failed", StopReason.MODEL_LENGTH)
    assert len(adapter.requests) == 4
    assert len(adapter.summaries) == 1
    tool_results = [
        message for message in await session.messages() if isinstance(message, ToolResultMessage)
    ]
    assert len(tool_results) == 3
    assert all(message.is_error for message in tool_results)
    assert not (tmp_path / "marker.txt").exists()


@pytest.mark.parametrize("restart", ["run", "resume"])
async def test_new_run_or_resume_resets_both_recovery_allowances(
    tmp_path: Path, restart: str
) -> None:
    adapter = RecoveryLlm(["overflow", "truncated_tool", "overflow", "truncated_tool", "complete"])
    agent, _ = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)

    await agent.run("第一个任务" * 100, max_turns=1, model=_MODEL)
    _assert_terminal(events, "failed", StopReason.MAX_TURNS)
    assert len(adapter.requests) == 2
    events.clear()

    if restart == "resume":
        await agent.resume(max_turns=2, model=_MODEL)
    else:
        await agent.run("新任务", max_turns=2, model=_MODEL)

    _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
    assert len(adapter.requests) == 5
    assert len(adapter.summaries) == 2
    assert not (tmp_path / "marker.txt").exists()


async def test_recovery_uses_selected_branch_and_does_not_merge_siblings(tmp_path):
    adapter = RecoveryLlm(["overflow", "complete"])
    agent, session = await _build(tmp_path, adapter)
    branch_point = (await session.entries())[-1].id
    sibling = UserMessage("兄弟分支秘密" * 100)
    await session.append_message(sibling)
    await session.append_message(AssistantMessage((TextBlock("兄弟回答"),), LlmStopReason.STOP))
    await session.branch(branch_point)
    events = []
    agent.subscribe(events.append)
    await agent.run("当前分支任务", max_turns=1, model=_MODEL)

    _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
    assert sibling not in await session.messages()
    assert all(sibling not in request.messages for request in adapter.requests)
    assert "兄弟分支秘密" not in adapter.summaries[0].messages[0].content
    assert len(await session.entries()) == 11  # 兄弟原始条目仍留在全树中。


async def test_stale_recovery_plan_is_rejected_without_retrying_provider(tmp_path, monkeypatch):
    adapter = RecoveryLlm(["overflow"])
    agent, session = await _build(tmp_path, adapter)
    compact = session.compact

    async def conflict(*args, **kwargs):
        await session.append_message(UserMessage("并发新增消息"))
        return await compact(*args, **kwargs)

    monkeypatch.setattr(session, "compact", conflict)
    events = []
    agent.subscribe(events.append)
    await agent.run("原始任务", max_turns=1, model=_MODEL)
    _assert_terminal(events, "failed", StopReason.SESSION_FAILED)
    assert "并发修改" in events[-1].error
    assert len(adapter.requests) == len(adapter.summaries) == 1
    assert not any(isinstance(entry, SessionCompactionEvent) for entry in await session.entries())
    assert (await session.messages())[-1] == UserMessage("并发新增消息")


async def test_successful_tool_turn_resets_consecutive_length_recovery(tmp_path: Path) -> None:
    adapter = RecoveryLlm(
        [
            "truncated_tool",
            "truncated_tool",
            "tool",
            "truncated_tool",
            "truncated_tool",
            "complete",
        ]
    )
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)
    try:
        await agent.run("完成任务", max_turns=6, model=_MODEL, permission_mode=PermissionMode.YOLO)
    finally:
        await agent.close()
    _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
    assert len(adapter.requests) == 6
    assert (tmp_path / "marker.txt").read_text() == "执行一次\n"
    results = [
        message for message in await session.messages() if isinstance(message, ToolResultMessage)
    ]
    assert len(results) == 5
    assert sum(not result.is_error for result in results) == 1
    assert all(
        result.details["execution_status"] == "not_executed"
        for result in results
        if result.is_error
    )


async def test_text_length_reports_budget_and_usage_without_continuation(tmp_path: Path) -> None:
    adapter = RecoveryLlm(["length"])
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)
    model = replace(_MODEL, max_output_tokens=8192, context_window_tokens=262144)
    try:
        await agent.run("回答", model=model)
    finally:
        await agent.close()
    _assert_terminal(events, "failed", StopReason.MODEL_LENGTH)
    assert len(adapter.requests) == 1
    assert adapter.requests[0].max_output_tokens == 8192
    assert "纯文本内容未完整生成" in events[-1].error
    assert "请求预算=8192" in events[-1].error
    assert "输出用量=5" in events[-1].error and "思考用量=未知" in events[-1].error
    assert "连续恢复次数=0" in events[-1].error
    messages = await session.messages()
    assert isinstance(messages[-1], AssistantMessage)
    assert messages[-1].stop_reason is LlmStopReason.LENGTH


async def test_capacity_rejection_makes_no_provider_request(tmp_path: Path) -> None:
    adapter = RecoveryLlm([])
    agent, session = await _build(tmp_path, adapter, enabled=False)
    events = []
    agent.subscribe(events.append)
    try:
        await agent.run("长" * 20000, model=replace(_MODEL, context_window_tokens=32768))
    finally:
        await agent.close()
    _assert_terminal(events, "failed", StopReason.CONTEXT_FAILED)
    assert not adapter.requests and not adapter.summaries
    assert isinstance((await session.messages())[-1], UserMessage)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("has_usage", [False, True])
@pytest.mark.parametrize("over_capacity", [False, True])
async def test_main_request_keeps_full_output_budget_or_rejects_before_execution(
    tmp_path: Path, enabled: bool, has_usage: bool, over_capacity: bool
) -> None:
    """容量不足时拒绝主请求，不以缩小输出额度代替容量检查。"""
    (tmp_path / "AGENTS.md").write_text("动态项目规则" * 100, encoding="utf-8")
    adapter = RecoveryLlm(["complete"])
    agent, session = await _build(tmp_path, adapter, history_count=0, enabled=enabled)
    model = replace(_MODEL, context_window_tokens=32768)
    input_tokens = 20000 if over_capacity else 10000
    if has_usage:
        await session.append_message(
            AssistantMessage(
                (TextBlock("短回复"),),
                LlmStopReason.STOP,
                provider=model.provider,
                model=model.model,
                usage=LlmUsage(input_tokens, 100),
            )
        )
    else:
        await session.append_message(UserMessage("文" * input_tokens))
    events = []
    agent.subscribe(events.append)
    try:
        await agent.run("继续", model=model)
    finally:
        await agent.close()
    if over_capacity:
        _assert_terminal(events, "failed", StopReason.CONTEXT_FAILED)
        assert not adapter.requests
    else:
        _assert_terminal(events, "completed", StopReason.MODEL_COMPLETED)
        assert len(adapter.requests) == 1
        request = adapter.requests[0]
        assert request.max_output_tokens == model.max_output_tokens == 16384
        assert "动态项目规则" in request.system_prompt
        assert request.tools
    assert not adapter.summaries


async def test_context_snapshot_read_failure_keeps_session_failure_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = RecoveryLlm([])
    agent, session = await _build(tmp_path, adapter)
    events = []
    agent.subscribe(events.append)

    async def fail_snapshot() -> None:
        raise OSError("快照不可读")

    monkeypatch.setattr(session, "context_snapshot", fail_snapshot)
    try:
        await agent.run("保存后读取快照", model=_MODEL)
    finally:
        await agent.close()
    _assert_terminal(events, "failed", StopReason.SESSION_FAILED)
    assert events[-1].error == "快照不可读"
    assert not adapter.requests and not adapter.summaries
    assert (await session.messages())[-1] == UserMessage("保存后读取快照")
