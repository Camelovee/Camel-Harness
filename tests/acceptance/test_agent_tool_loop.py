import asyncio
from collections.abc import AsyncIterator

from camel_harness import Agent, AgentLoop, AgentOptions, AgentStreamEventType
from camel_harness.backend.approval import WebApproval
from camel_harness.harness import Harness
from camel_harness.harness.compaction import CompactionEngine, ContextManager
from camel_harness.harness.session import SessionManager
from camel_harness.harness.session.jsonl import JsonlSessionStorage
from camel_harness.harness.tools.builtin import BuiltinToolProvider
from camel_harness.harness.tools.mcp import McpManager
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmRequest,
    LlmStreamEvent,
    LlmStreamEventType,
)
from camel_harness.tools import (
    PermissionService,
    ToolExecutor,
    ToolRegistry,
)
from camel_harness.tools.jsonschema_validator import validate_tool_arguments
from camel_harness.types import PermissionMode


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
        common = {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "step_id": request.step_id,
            "provider": "fake",
        }
        if self.calls == 1:
            yield LlmStreamEvent(
                LlmStreamEventType.TOOL_CALL_COMPLETED,
                sequence=1,
                data={
                    "call_id": "call-1",
                    "name": "list_files",
                    "arguments": {"path": "."},
                },
                **common,
            )
            yield LlmStreamEvent(
                LlmStreamEventType.TOOL_CALL_COMPLETED,
                sequence=2,
                data={
                    "call_id": "call-2",
                    "name": "read_file",
                    "arguments": {"path": "sample.txt"},
                },
                **common,
            )
            yield LlmStreamEvent(
                LlmStreamEventType.MESSAGE_COMPLETED, sequence=3, data={}, **common
            )
        else:
            yield LlmStreamEvent(
                LlmStreamEventType.TEXT_DELTA,
                sequence=1,
                data={"text": "已读取文件"},
                **common,
            )
            yield LlmStreamEvent(
                LlmStreamEventType.MESSAGE_COMPLETED, sequence=2, data={}, **common
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
        common = {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "step_id": request.step_id,
            "provider": "fake",
        }
        if self.calls == 1:
            yield LlmStreamEvent(
                LlmStreamEventType.TEXT_DELTA,
                sequence=1,
                data={"text": "我准备写入文件。"},
                **common,
            )
            yield LlmStreamEvent(
                LlmStreamEventType.TOOL_CALL_COMPLETED,
                sequence=2,
                data={
                    "call_id": "write-call",
                    "name": "write_file",
                    "arguments": {"path": "sample.txt", "content": "内容"},
                },
                **common,
            )
            yield LlmStreamEvent(
                LlmStreamEventType.MESSAGE_COMPLETED, sequence=3, data={}, **common
            )
            return
        yield LlmStreamEvent(
            LlmStreamEventType.TEXT_DELTA,
            sequence=1,
            data={"text": "文件已写入。"},
            **common,
        )
        yield LlmStreamEvent(LlmStreamEventType.MESSAGE_COMPLETED, sequence=2, data={}, **common)


async def test_agent_completes_a_tool_loop(tmp_path) -> None:
    (tmp_path / "sample.txt").write_text("内容", encoding="utf-8")
    runtime = FakeLlm()
    llm = LlmClient(runtime)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
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

    events = [
        event
        async for event in harness.run_turn(
            "session-1", "读取文件", tmp_path, max_steps=3, thinking=True
        )
    ]

    assert runtime.calls == 2
    assert any(event.type is AgentStreamEventType.TOOL_RESULT for event in events)
    assert "已读取文件" == "".join(
        str(event.data["text"]) for event in events if event.type is AgentStreamEventType.TEXT_DELTA
    )
    assert [message.role.value for message in await sessions.messages("session-1")] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    tool_messages = [
        message for message in runtime.requests[1].messages if message.role.value == "tool"
    ]
    assert len(tool_messages) == 1
    assert len(tool_messages[0].content) == 2
    assert all(request.thinking for request in runtime.requests)


async def test_agent_streams_approval_after_assistant_message(tmp_path) -> None:
    """审批事件应在本 Step 的 assistant 流结束后发送，并在决定后继续工具结果。"""

    runtime = ApprovalFakeLlm()
    llm = LlmClient(runtime)
    sessions = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    approval = WebApproval(timeout_seconds=2)
    permission = PermissionService(approval)
    context = ContextManager(CompactionEngine(llm))
    agent = Agent(
        AgentOptions("fake", "fake-model"),
        AgentLoop(llm, registry, ToolExecutor(registry, validate_tool_arguments, permission)),
    )
    harness = Harness(
        agent,
        sessions,
        context,
        McpManager(tmp_path, tmp_path / ".mcp.json"),
        _no_workspace,
    )

    async def collect() -> list:
        return [
            event
            async for event in harness.run_turn(
                "session-approval",
                "写入文件",
                tmp_path,
                max_steps=3,
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
    approval_index = types.index(AgentStreamEventType.APPROVAL_REQUESTED)
    completed_index = types.index(AgentStreamEventType.MESSAGE_COMPLETED)
    result_index = types.index(AgentStreamEventType.TOOL_RESULT)
    assert completed_index < approval_index < result_index
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "内容"


async def _no_workspace() -> None:
    return None
