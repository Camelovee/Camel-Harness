import asyncio
from collections.abc import AsyncIterator

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
    validate_tool_arguments,
)
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    ToolResultMessage,
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
            yield LlmStreamEvent(
                LlmStreamEventType.TOOL_CALL_COMPLETED,
                {
                    "call_id": "call-1",
                    "name": "read_file",
                    "arguments": {"path": "sample.txt", "max_lines": 1},
                },
            )
            yield LlmStreamEvent(
                LlmStreamEventType.TOOL_CALL_COMPLETED,
                {
                    "call_id": "call-2",
                    "name": "read_file",
                    "arguments": {"path": "sample.txt"},
                },
            )
            yield LlmStreamEvent(
                LlmStreamEventType.MESSAGE_COMPLETED,
                {"stop_reason": LlmStopReason.TOOL_USE.value},
            )
        else:
            yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "已读取文件"})
            yield LlmStreamEvent(
                LlmStreamEventType.MESSAGE_COMPLETED,
                {"stop_reason": LlmStopReason.STOP.value},
            )


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
            yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "我准备写入文件。"})
            yield LlmStreamEvent(
                LlmStreamEventType.TOOL_CALL_COMPLETED,
                {
                    "call_id": "write-call",
                    "name": "write_file",
                    "arguments": {"path": "sample.txt", "content": "内容"},
                },
            )
            yield LlmStreamEvent(
                LlmStreamEventType.MESSAGE_COMPLETED,
                {"stop_reason": LlmStopReason.TOOL_USE.value},
            )
            return
        yield LlmStreamEvent(LlmStreamEventType.TEXT_DELTA, {"text": "文件已写入。"})
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            {"stop_reason": LlmStopReason.STOP.value},
        )


class TruncatedToolLlm(LlmAdapter):
    """模拟工具参数随输出长度上限被截断的响应。"""

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(
            LlmStreamEventType.TOOL_CALL_COMPLETED,
            {
                "call_id": "truncated-write",
                "name": "write_file",
                "arguments": {"path": "should-not-exist.txt", "content": "不应写入"},
            },
        )
        yield LlmStreamEvent(
            LlmStreamEventType.MESSAGE_COMPLETED,
            {"stop_reason": LlmStopReason.LENGTH.value},
        )


async def test_agent_completes_a_tool_loop(tmp_path) -> None:
    (tmp_path / "sample.txt").write_text("内容", encoding="utf-8")
    runtime = FakeLlm()
    llm = LlmClient(runtime)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    context = ContextManager(CompactionEngine(llm))
    agent = Agent(
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry, validate_tool_arguments),
        sessions,
        context,
    )

    events = [
        event
        async for event in agent.run("session-1", "读取文件", tmp_path, max_turns=3, thinking=True)
    ]

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
        str(event.assistant_event.data["text"])
        for event in events
        if event.type == "message_update"
        and event.assistant_event.type is LlmStreamEventType.TEXT_DELTA
    )
    assert [message.role for message in await sessions.messages("session-1")] == [
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
    agent = Agent(
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry, validate_tool_arguments, permission),
        sessions,
        context,
    )

    async def collect() -> list:
        return [
            event
            async for event in agent.run(
                "session-approval",
                "写入文件",
                tmp_path,
                max_turns=3,
                permission_mode=PermissionMode.DEFAULT,
            )
        ]

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
    agent = Agent(
        AgentOptions("fake-model"),
        llm,
        registry,
        ToolExecutor(registry, validate_tool_arguments),
        sessions,
        ContextManager(CompactionEngine(llm)),
    )

    events = [event async for event in agent.run("session-length", "写文件", tmp_path)]
    messages = await sessions.messages("session-length")

    assert not (tmp_path / "should-not-exist.txt").exists()
    assert events[-1].type == "agent_end"
    assert events[-1].status == "failed"
    assert events[-1].stop_reason is StopReason.MODEL_LENGTH
    assert [message.role for message in messages] == ["user", "assistant", "toolResult"]
    assert isinstance(messages[1], AssistantMessage)
    assert messages[1].stop_reason is LlmStopReason.LENGTH
    assert isinstance(messages[2], ToolResultMessage)
    assert messages[2].is_error
