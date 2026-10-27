import asyncio
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path

import pytest

from camel_backend.approval import WebApproval
from camel_harness import Agent, AgentOptions, StopReason
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.context.system_prompt import DEFAULT_SYSTEM_PROMPT
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.subagent import (
    LocalSubagentRunner,
    SubagentRequest,
    SubagentType,
    build_subagent,
)
from camel_harness.agent.tools import (
    PermissionMode,
    PermissionService,
    ToolExecutor,
    ToolRegistry,
)
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.llm import (
    AssistantMessage,
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    LlmStreamEventType,
    LlmThinkingEffort,
    MessageCompleted,
    TextDelta,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
)

_MODEL = LlmModel("fake", "fake-model")


async def test_agent_reads_pages_then_performs_one_batch_edit(tmp_path: Path) -> None:
    path = tmp_path / "batch.txt"
    path.write_bytes(b"first\r\nsecond\r\n")

    class BatchLlm(LlmAdapter):
        def __init__(self) -> None:
            self.calls = 0

        @property
        def provider_id(self) -> str:
            return "fake"

        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.calls += 1
            assert llm_request.system_prompt == DEFAULT_SYSTEM_PROMPT
            result = llm_request.messages[-1]
            if self.calls == 1:
                call = ToolUseBlock("page-1", "read_file", {"path": path.name, "max_lines": 1})
            elif self.calls == 2:
                assert isinstance(result, ToolResultMessage) and not result.is_error
                assert result.details["next_start_line"] == 2
                call = ToolUseBlock("page-2", "read_file", {"path": path.name, "start_line": 2})
            elif self.calls == 3:
                assert isinstance(result, ToolResultMessage) and not result.is_error
                assert result.details["next_start_line"] is None
                call = ToolUseBlock(
                    "batch",
                    "edit_file",
                    {
                        "path": path.name,
                        "edits": [
                            {"old_text": "first", "new_text": "一"},
                            {"old_text": "second", "new_text": "二"},
                        ],
                    },
                )
            else:
                assert isinstance(result, ToolResultMessage) and not result.is_error
                assert "2 个片段" in result.content and "-first" in result.content
                assert "+一" in result.content and "+二" in result.content
                yield TextDelta("已完成批量编辑")
                yield MessageCompleted(LlmStopReason.STOP)
                return
            yield ToolCallCompleted(call)
            yield MessageCompleted(LlmStopReason.TOOL_USE)

    manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await manager.create("batch", tmp_path, "批量编辑")
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    adapter = BatchLlm()
    client = LlmClient(adapter)
    agent = Agent(
        session=manager.bind("batch"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=client,
        tool_executor=ToolExecutor(registry),
        context_manager=ContextManager(
            CompactionEngine(client), system_prompt=DEFAULT_SYSTEM_PROMPT
        ),
    )
    try:
        await agent.run(
            "读取完整文件后修改两个位置", model=_MODEL, permission_mode=PermissionMode.YOLO
        )
    finally:
        await agent.close()
    assert adapter.calls == 4
    assert path.read_bytes() == "一\r\n二\r\n".encode()
    results = [
        message
        for message in await manager.bind("batch").messages()
        if isinstance(message, ToolResultMessage)
    ]
    assert [result.name for result in results] == ["read_file", "read_file", "edit_file"]
    assert all(not result.is_error for result in results)


class FakeLlm(LlmAdapter):
    """先调用读取工具，再返回最终文本的确定性测试模型。"""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        self.requests.append(llm_request)
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

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield TextDelta(text="我准备写入文件。")
            yield ToolCallCompleted(
                ToolUseBlock(
                    "write-call",
                    "write_file",
                    {"path": "sample.txt", "content": "内容"},
                )
            )
            yield MessageCompleted(LlmStopReason.TOOL_USE, model=None)
            return
        yield TextDelta(text="文件已写入。")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


class TruncatedToolLlm(LlmAdapter):
    """模拟工具参数随输出长度上限被截断的响应。"""

    tool_name = "write_file"

    @property
    def provider_id(self) -> str:
        return "fake"

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield ToolCallCompleted(
            ToolUseBlock(
                "truncated-write",
                self.tool_name,
                {"path": "should-not-exist.txt", "content": "不应写入"},
            )
        )
        yield MessageCompleted(LlmStopReason.LENGTH, model=None)


async def test_four_identical_valid_calls_are_all_executed(tmp_path):
    class RepeatedLlm(FakeLlm):
        async def stream(self, llm_request):
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
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("repeated", tmp_path, "重复读取")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    llm_client = LlmClient(RepeatedLlm())
    agent = Agent(
        session=session_manager.bind("repeated"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )
    events: list = []
    agent.subscribe(events.append)
    await agent.run("重复检查", model=_MODEL)
    results = [event.result for event in events if event.type == "tool_execution_end"]
    assert len(results) == 4
    assert all(not result.is_error for result in results)
    assert events[-1].status == "completed"


async def test_tool_result_storage_failure_ends_run_without_retrying_tool(tmp_path):
    (tmp_path / "sample.txt").write_text("内容", encoding="utf-8")
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("tool-storage-failure", tmp_path, "归档失败")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    llm_client = LlmClient(FakeLlm())

    async def failed_storage(result, context):
        raise OSError("工具结果归档失败")

    agent = Agent(
        session=session_manager.bind("tool-storage-failure"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry, normalize_result=failed_storage),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )
    events: list = []
    agent.subscribe(events.append)
    await agent.run("读取", model=_MODEL)
    assert events[-1].stop_reason == "tool_failed"
    assert events[-1].error == "工具结果归档失败"
    messages = await session_manager.bind("tool-storage-failure").messages()
    assert len([message for message in messages if isinstance(message, AssistantMessage)]) == 1
    assert len([message for message in messages if isinstance(message, ToolResultMessage)]) == 2


async def test_agent_completes_a_tool_loop(tmp_path) -> None:
    (tmp_path / "sample.txt").write_text("内容", encoding="utf-8")
    runtime = FakeLlm()
    llm_client = LlmClient(runtime)
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    context_manager = ContextManager(
        system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
    )
    await session_manager.create("session-1", tmp_path, "读取文件")
    agent = Agent(
        session=session_manager.bind("session-1"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=context_manager,
    )

    events: list = []
    unsubscribe = agent.subscribe(events.append)
    await agent.run("读取文件", max_turns=3, effort=LlmThinkingEffort.HIGH, model=_MODEL)
    unsubscribe()

    assert runtime.calls == 2
    assert events[0].type == "agent_start"
    assert events[-1].type == "agent_end"
    assert events[-1].status == "completed"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.run_id for event in events}) == 1
    assert [event.type for event in events].count("turn_start") == 2
    assert [event.type for event in events].count("turn_end") == 2
    turns = [event for event in events if event.type == "turn_end"]
    assert isinstance(turns[0].tool_results, tuple)
    assert [message.call_id for message in turns[0].tool_results] == ["call-1", "call-2"]
    assert turns[1].tool_results == ()
    saved_results = tuple(
        event.message
        for event in events
        if event.type == "message_end" and isinstance(event.message, ToolResultMessage)
    )
    assert turns[0].tool_results == saved_results
    assert any(event.type == "tool_execution_end" for event in events)
    assert "已读取文件" == "".join(
        str(event.assistant_event.text)
        for event in events
        if event.type == "message_update"
        and event.assistant_event.type == LlmStreamEventType.TEXT_DELTA
    )
    assert [message.role for message in await session_manager.bind("session-1").messages()] == [
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
    assert {definition.name for definition in runtime.requests[0].tools} == {
        "read_file",
        "write_file",
        "edit_file",
        "run_shell",
    }
    assert all(request.effort is LlmThinkingEffort.HIGH for request in runtime.requests)


@pytest.mark.parametrize("content", ["内容", "内容\n" * 15_000], ids=["small", "large"])
async def test_local_subagent_persists_parent_link(tmp_path, content) -> None:
    (tmp_path / "sample.txt").write_text(content, encoding="utf-8")

    class IntermediateTextLlm(FakeLlm):
        async def stream(self, llm_request):
            if self.calls == 0:
                yield TextDelta("准备读取，不是最终结论")
            async for event in super().stream(llm_request):
                yield event

    llm_client = LlmClient(IntermediateTextLlm())
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "父会话")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    runner = LocalSubagentRunner(
        build_subagent=partial(
            build_subagent,
            llm_client=llm_client,
            session_manager=session_manager,
            tool_registry=tool_registry,
            permission_service=PermissionService(),
            context_manager=ContextManager(
                system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
            ),
            agent_options=AgentOptions(),
        ),
        session_manager=session_manager,
    )

    result = await runner.run(
        SubagentRequest(
            "读取文件",
            SubagentType.EXPLORE,
            tmp_path,
            PermissionMode.DEFAULT,
            "parent",
            "delegate-call",
            _MODEL,
        )
    )

    assert result.succeeded
    assert result.content == "已读取文件"
    metadata = await session_manager.get(result.session_id)
    assert metadata is not None
    assert metadata.parent_session_id == "parent"
    assert metadata.parent_tool_call_id == "delegate-call"
    results = list((session_manager.directory(result.session_id) / "tool-results").glob("*.txt"))
    assert results == []
    messages = await session_manager.bind(result.session_id).messages()
    tool_results = [message for message in messages if isinstance(message, ToolResultMessage)]
    assert len(tool_results) == 2
    assert all(not message.is_error for message in tool_results)
    lines = content.splitlines()
    for message, limit in zip(tool_results, (1, 400), strict=True):
        count = min(limit, len(lines))
        expected = "\n".join(f"{index + 1}: {line}" for index, line in enumerate(lines[:count]))
        expected += f"\n[显示第 1–{count} 行，共 {len(lines)} 行]"
        if count < len(lines):
            expected += f"\n[尚未读完，请使用 start_line={count + 1} 继续读取。]"
        assert message.content == expected
        assert message.details["end_line"] == count


async def test_agent_streams_approval_after_assistant_message(tmp_path) -> None:
    """审批事件应在本 Turn 的 assistant 流结束后发送，并在决定后继续工具结果。"""

    runtime = ApprovalFakeLlm()
    llm_client = LlmClient(runtime)
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    web_approval = WebApproval(timeout_seconds=2)
    permission_service = PermissionService(web_approval)
    context_manager = ContextManager(
        system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
    )
    await session_manager.create("session-approval", tmp_path, "写入文件")
    agent = Agent(
        session=session_manager.bind("session-approval"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(
            tool_registry=tool_registry, permission_service=permission_service
        ),
        context_manager=context_manager,
    )

    async def collect() -> list:
        events: list = []
        unsubscribe = agent.subscribe(events.append)
        try:
            await agent.run(
                "写入文件",
                max_turns=3,
                permission_mode=PermissionMode.DEFAULT,
                model=_MODEL,
            )
            return events
        finally:
            unsubscribe()

    stream_task = asyncio.create_task(collect())
    pending: tuple = ()
    for _ in range(100):
        pending = await web_approval.list_pending()
        if pending:
            break
        await asyncio.sleep(0.01)
    assert pending
    await asyncio.sleep(0)
    assert await web_approval.resolve(pending[0].request_id, True)
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


@pytest.mark.parametrize("tool_name", ["write_file", "mcp__remote_action"])
async def test_agent_does_not_execute_tool_calls_from_truncated_output(tmp_path, tool_name) -> None:
    """达到长度上限时，工具参数即使能解析也不可执行。"""

    adapter = TruncatedToolLlm()
    adapter.tool_name = tool_name
    llm_client = LlmClient(adapter)
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / ".camel" / "sessions"))
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    await session_manager.create("session-length", tmp_path, "截断工具调用")
    agent = Agent(
        session=session_manager.bind("session-length"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )

    events: list = []
    unsubscribe = agent.subscribe(events.append)
    await agent.run("写文件", model=_MODEL)
    unsubscribe()
    messages = await session_manager.bind("session-length").messages()

    assert not (tmp_path / "should-not-exist.txt").exists()
    assert events[-1].type == "agent_end"
    assert events[-1].status == "failed"
    assert events[-1].stop_reason is StopReason.MODEL_LENGTH
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
        "toolResult",
        "assistant",
        "toolResult",
    ]
    assert isinstance(messages[1], AssistantMessage)
    assert messages[1].stop_reason is LlmStopReason.LENGTH
    assert isinstance(messages[2], ToolResultMessage)
    assert messages[2].is_error
    assert "重新生成完整且更小的参数" in messages[2].content
    assert "edit_file" not in messages[2].content
    assert "文件未" not in messages[2].content


@pytest.mark.parametrize("scenario", ["success", "text_length", "turn_limit", "cancel"])
async def test_agent_recovers_once_after_truncated_tool_call(tmp_path, scenario) -> None:
    class RecoveringLlm(TruncatedToolLlm):
        def __init__(self):
            self.requests = []

        async def stream(self, llm_request):
            self.requests.append(llm_request)
            if len(self.requests) == 1:
                if scenario == "text_length":
                    yield TextDelta("未完成的文本")
                    yield MessageCompleted(LlmStopReason.LENGTH)
                else:
                    async for event in super().stream(llm_request):
                        yield event
            elif len(self.requests) == 2:
                if scenario == "cancel":
                    await asyncio.Future()
                assert isinstance(llm_request.messages[-1], ToolResultMessage)
                assert llm_request.messages[-1].is_error
                yield ToolCallCompleted(
                    ToolUseBlock(
                        "small-write",
                        "write_file",
                        {"path": "small.txt", "content": "完整"},
                    )
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            else:
                yield TextDelta("已完成")
                yield MessageCompleted(LlmStopReason.STOP)

    adapter = RecoveringLlm()
    llm_client = LlmClient(adapter)
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("recover", tmp_path, "截断恢复")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    agent = Agent(
        session=session_manager.bind("recover"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )
    events = []
    agent.subscribe(events.append)

    async def cancel_recovery(event):
        if scenario == "cancel" and event.type == "turn_start" and len(adapter.requests) == 1:
            await agent.abort()

    agent.subscribe(cancel_recovery)
    await agent.run(
        "写文件",
        permission_mode=PermissionMode.YOLO,
        max_turns=1 if scenario == "turn_limit" else 50,
        model=_MODEL,
    )
    assert not (tmp_path / "should-not-exist.txt").exists()
    if scenario == "success":
        assert len(adapter.requests) == 3
        assert (tmp_path / "small.txt").read_text() == "完整"
        assert events[-1].status == "completed"
    else:
        assert not (tmp_path / "small.txt").exists()
        assert len(adapter.requests) == 1
        assert (
            events[-1].stop_reason
            is {
                "text_length": StopReason.MODEL_LENGTH,
                "turn_limit": StopReason.MAX_TURNS,
                "cancel": StopReason.CANCELLED,
            }[scenario]
        )
    assert sum(event.type == "agent_end" for event in events) == 1


class _RoutingSpyLlm(LlmAdapter):
    """记录每个 Provider 收到的请求与模型名。"""

    def __init__(self, provider: str, *, delegate: bool = False) -> None:
        self._provider = provider
        self._delegate = delegate
        self.calls = 0
        self.requests: list[LlmRequest] = []

    @property
    def provider_id(self) -> str:
        return self._provider

    async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        self.requests.append(llm_request)
        if self._delegate and self.calls == 1:
            yield ToolCallCompleted(
                ToolUseBlock(
                    "delegate-1", "delegate_task", {"task": "子任务", "agent_type": "explore"}
                )
            )
            yield MessageCompleted(LlmStopReason.TOOL_USE, model=None)
            return
        yield TextDelta("完成")
        yield MessageCompleted(LlmStopReason.STOP, model=None)


async def test_run_model_selects_adapter_per_run(tmp_path) -> None:
    """同一个 Agent 的两次 Run 可以分别路由到不同 Provider。"""

    first, second = _RoutingSpyLlm("one"), _RoutingSpyLlm("two")
    llm_client = LlmClient({"one": first, "two": second})
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("routing", tmp_path, "按 Run 选模型")
    tool_registry = ToolRegistry()
    agent = Agent(
        session=session_manager.bind("routing"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )

    await agent.run("第一次", model=LlmModel("one", "m-a", max_output_tokens=8192))
    await agent.run("第二次", model=LlmModel("two", "m-b", context_window_tokens=262144))

    assert [request.model.model for request in first.requests] == ["m-a"]
    assert [request.model.model for request in second.requests] == ["m-b"]
    assert first.requests[0].max_output_tokens == 8192
    assert second.requests[0].max_output_tokens == 16384
    assert second.requests[0].model.context_window_tokens == 262144


async def test_subagent_follows_parent_run_model(tmp_path) -> None:
    """委派工具的子 Agent 使用父 Run 的模型选择，不回退到其他 Provider。"""

    parent_llm = _RoutingSpyLlm("one", delegate=True)
    other = _RoutingSpyLlm("two")
    llm_client = LlmClient({"one": parent_llm, "two": other})
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("parent", tmp_path, "委派模型跟随")
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider())
    runner = LocalSubagentRunner(
        build_subagent=partial(
            build_subagent,
            llm_client=llm_client,
            session_manager=session_manager,
            tool_registry=tool_registry,
            permission_service=PermissionService(),
            context_manager=ContextManager(
                system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
            ),
            agent_options=AgentOptions(),
        ),
        session_manager=session_manager,
    )
    from camel_harness.agent.subagent import SubagentToolProvider

    await tool_registry.register_provider(SubagentToolProvider(runner.run))
    agent = Agent(
        session=session_manager.bind("parent"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(tool_registry=tool_registry),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )

    await agent.run(
        "委派任务", model=LlmModel("one", "main-model"), permission_mode=PermissionMode.YOLO
    )

    assert other.requests == []
    # 父 Run 两次请求（委派 + 收尾）加子 Agent 一次，全部落在所选 Provider 与模型上
    assert len(parent_llm.requests) == 3
    assert all(request.model.model == "main-model" for request in parent_llm.requests)
