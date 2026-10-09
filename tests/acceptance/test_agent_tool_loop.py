import asyncio
from collections.abc import AsyncIterator

import pytest

from camel_backend.approval import WebApproval
from camel_harness import Agent, AgentOptions, StopReason
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import (
    PermissionMode,
    PermissionService,
    ToolExecutor,
    ToolRegistry,
)
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.agent.tools.subagent import LocalSubagentRunner, SubagentRequest, SubagentType
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    MessageCompleted,
    TextDelta,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
)


class FakeLlm(LlmAdapter):
    """先调用读取工具，再返回最终文本的确定性测试模型。"""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            yield ToolCallCompleted(
                ToolUseBlock("call-1", "read_file", {"path": "sample.txt", "max_lines": 1})
            )
            yield ToolCallCompleted(ToolUseBlock("call-2", "read_file", {"path": "sample.txt"}))
            yield MessageCompleted(LlmStopReason.TOOL_USE, model=None)
        else:
            yield TextDelta(text="已读取文件")
            yield MessageCompleted(LlmStopReason.STOP, model=None)


class ApprovalFakeLlm(LlmAdapter):
    """先请求写工具，再返回最终文本，用于验证审批事件顺序。"""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield TextDelta(text="我准备写入文件。")
            yield ToolCallCompleted(
                ToolUseBlock("write-call", "write_file", {"path": "sample.txt", "content": "内容"})
            )
            yield MessageCompleted(LlmStopReason.TOOL_USE, model=None)
            return
        yield TextDelta(text="文件已写入。")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


class TruncatedToolLlm(LlmAdapter):
    """模拟工具参数随输出长度上限被截断的响应。"""

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield ToolCallCompleted(
            ToolUseBlock(
                "truncated-write",
                "write_file",
                {"path": "should-not-exist.txt", "content": "不应写入"},
            )
        )
        yield MessageCompleted(LlmStopReason.LENGTH, model=None)


async def test_four_identical_valid_calls_are_all_executed(tmp_path):
    class RepeatedLlm(FakeLlm):
        async def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                for index in range(4):
                    yield ToolCallCompleted(
                        ToolUseBlock(f"read-{index}", "read_file", {"path": "sample.txt"})
                    )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            else:
                yield TextDelta("完成")
                yield MessageCompleted(LlmStopReason.STOP)

    (tmp_path / "sample.txt").write_text("内容", encoding="utf-8")
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("repeated", tmp_path, "重复读取")
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    llm = LlmClient(RepeatedLlm())
    agent = Agent(
        sessions.bind("repeated"),
        tmp_path,
        AgentOptions("fake"),
        llm,
        registry,
        ToolExecutor(registry),
        ContextManager(CompactionEngine(llm)),
    )
    events: list = []
    agent.subscribe(events.append)
    await agent.run("重复检查")
    results = [event.result for event in events if event.type == "tool_execution_end"]
    assert len(results) == 4
    assert all(not result.is_error for result in results)
    assert events[-1].status == "completed"


async def test_agent_completes_a_tool_loop(tmp_path) -> None:
    (tmp_path / "sample.txt").write_text("内容", encoding="utf-8")
    runtime = FakeLlm()
    llm = LlmClient(runtime)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    context = ContextManager(CompactionEngine(llm))
    await sessions.create("session-1", tmp_path, "读取文件")
    agent = Agent(
        sessions.bind("session-1"),
        tmp_path,
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry),
        context,
    )

    events: list = []
    unsubscribe = agent.subscribe(events.append)
    await agent.run("读取文件", max_turns=3, thinking=True)
    unsubscribe()

    assert runtime.calls == 2
    assert events[0].type == "agent_start"
    assert events[-1].type == "agent_end"
    assert events[-1].status == "completed"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.run_id for event in events}) == 1
    assert [event.type for event in events].count("turn_start") == 2
    assert [event.type for event in events].count("turn_end") == 2
    assert any(event.type == "tool_execution_end" for event in events)
    assert "已读取文件" == "".join(
        str(event.assistant_event.text)
        for event in events
        if event.type == "message_update"
        and event.assistant_event.type == LlmStreamEventType.TEXT_DELTA
    )
    assert [message.role for message in await sessions.bind("session-1").messages()] == [
        "user",
        "assistant",
        "toolResult",
        "toolResult",
        "assistant",
    ]
    tool_messages = [
        message
        for message in runtime.requests[1].messages
        if isinstance(message, ToolResultMessage)
    ]
    assert [message.call_id for message in tool_messages] == ["call-1", "call-2"]
    assert all(request.thinking for request in runtime.requests)


@pytest.mark.parametrize("content", ["内容", "内容" * 15_000], ids=["small", "large"])
async def test_local_subagent_persists_parent_link(tmp_path, content) -> None:
    (tmp_path / "sample.txt").write_text(content, encoding="utf-8")

    class IntermediateTextLlm(FakeLlm):
        async def stream(self, request):
            if self.calls == 0:
                yield TextDelta("准备读取，不是最终结论")
            async for event in super().stream(request):
                yield event

    llm = LlmClient(IntermediateTextLlm())
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("parent", tmp_path, "父会话")
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    runner = LocalSubagentRunner(
        llm,
        sessions,
        registry,
        PermissionService(),
        ContextManager(CompactionEngine(llm)),
        AgentOptions("fake-model"),
    )

    result = await runner.run(
        SubagentRequest(
            "读取文件",
            SubagentType.EXPLORE,
            tmp_path,
            PermissionMode.DEFAULT,
            "parent",
            "delegate-call",
        )
    )

    assert result.succeeded
    assert result.content == "已读取文件"
    metadata = await sessions.get(result.session_id)
    assert metadata is not None
    assert metadata.parent_session_id == "parent"
    assert metadata.parent_tool_call_id == "delegate-call"
    results = list((sessions.directory(result.session_id) / "tool-results").glob("*.txt"))
    if len(content) > 20_000:
        assert len(results) == 2
        assert all(path.read_text("utf-8") == f"1: {content}" for path in results)
        assert all(path.parent.parent.parent.name == "subagents" for path in results)
    else:
        assert not results


async def test_agent_streams_approval_after_assistant_message(tmp_path) -> None:
    """审批事件应在本 Turn 的 assistant 流结束后发送，并在决定后继续工具结果。"""

    runtime = ApprovalFakeLlm()
    llm = LlmClient(runtime)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    approval = WebApproval(timeout_seconds=2)
    permission = PermissionService(approval)
    context = ContextManager(CompactionEngine(llm))
    await sessions.create("session-approval", tmp_path, "写入文件")
    agent = Agent(
        sessions.bind("session-approval"),
        tmp_path,
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry, permission),
        context,
    )

    async def collect() -> list:
        events: list = []
        unsubscribe = agent.subscribe(events.append)
        try:
            await agent.run(
                "写入文件",
                max_turns=3,
                permission_mode=PermissionMode.DEFAULT,
            )
            return events
        finally:
            unsubscribe()

    stream_task = asyncio.create_task(collect())
    pending: tuple = ()
    for _ in range(100):
        pending = await approval.list_pending()
        if pending:
            break
        await asyncio.sleep(0.01)
    assert pending
    await asyncio.sleep(0)
    assert await approval.resolve(pending[0].request_id, True)
    events = await stream_task

    types = [event.type for event in events]
    approval_index = types.index("approval_requested")
    completed_index = next(
        index
        for index, event in enumerate(events)
        if event.type == "message_end" and event.message.role == "assistant"
    )
    result_index = types.index("tool_execution_end")
    assert completed_index < approval_index < result_index
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "内容"


async def test_agent_does_not_execute_tool_calls_from_truncated_output(tmp_path) -> None:
    """达到长度上限时，工具参数即使能解析也不可执行。"""

    llm = LlmClient(TruncatedToolLlm())
    sessions = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    await sessions.create("session-length", tmp_path, "截断工具调用")
    agent = Agent(
        sessions.bind("session-length"),
        tmp_path,
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry),
        ContextManager(CompactionEngine(llm)),
    )

    events: list = []
    unsubscribe = agent.subscribe(events.append)
    await agent.run("写文件")
    unsubscribe()
    messages = await sessions.bind("session-length").messages()

    assert not (tmp_path / "should-not-exist.txt").exists()
    assert events[-1].type == "agent_end"
    assert events[-1].status == "failed"
    assert events[-1].stop_reason is StopReason.MODEL_LENGTH
    assert [message.role for message in messages] == ["user", "assistant", "toolResult"]
    assert isinstance(messages[1], AssistantMessage)
    assert messages[1].stop_reason is LlmStopReason.LENGTH
    assert isinstance(messages[2], ToolResultMessage)
    assert messages[2].is_error
