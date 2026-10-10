import asyncio

import pytest

from camel_harness.agent import AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.runtime import LocalSubagentRunner
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import PermissionMode, PermissionService, ToolRegistry
from camel_harness.agent.tools.subagent import SubagentRequest, SubagentType
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmStopReason,
    MessageCompleted,
    StreamFailed,
    TextDelta,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
)


class ChildLlm(LlmAdapter):
    """可阻塞或失败的子模型，用于确认委派任务清理边界。"""

    def __init__(self, blocking: bool) -> None:
        self.blocking = blocking
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    @property
    def provider_id(self):
        return "fake"

    async def stream(self, request):
        try:
            yield TextDelta("未完成的回答")
            self.started.set()
            if self.blocking:
                await asyncio.Event().wait()
            else:
                yield StreamFailed("模型失败")
        finally:
            self.closed.set()


async def make_runner(tmp_path, adapter):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("parent", tmp_path, "父会话")
    llm = LlmClient(adapter)
    runner = LocalSubagentRunner(
        llm,
        sessions,
        ToolRegistry(),
        PermissionService(),
        ContextManager(CompactionEngine(llm)),
        AgentOptions("fake"),
    )
    request = SubagentRequest(
        "子任务", SubagentType.EXPLORE, tmp_path, PermissionMode.DEFAULT, "parent", "delegate"
    )
    return runner, request, sessions


async def test_child_failed_terminal_is_not_success_or_partial_text(tmp_path):
    adapter = ChildLlm(False)
    runner, request, sessions = await make_runner(tmp_path, adapter)
    result = await runner.run(request)
    assert not result.succeeded
    assert result.content == "模型失败"
    assert [message.role for message in await sessions.bind(result.session_id).messages()] == [
        "user"
    ]
    assert adapter.closed.is_set()


async def test_parent_cancellation_closes_child_owned_task(tmp_path):
    adapter = ChildLlm(True)
    runner, request, sessions = await make_runner(tmp_path, adapter)
    task = asyncio.create_task(runner.run(request))
    await adapter.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert adapter.closed.is_set()
    children = [item for item in await sessions.list() if item.parent_session_id == "parent"]
    assert len(children) == 1
    assert [message.role for message in await sessions.bind(children[0].session_id).messages()] == [
        "user"
    ]


async def test_child_reads_its_own_archived_result_without_closing_shared_llm(tmp_path):
    from camel_harness.agent.tools.builtin import BuiltinToolProvider

    class ReadingLlm(LlmAdapter):
        requests = 0
        closed = False

        @property
        def provider_id(self):
            return "fake"

        async def close(self):
            self.closed = True

        async def stream(self, request):
            self.requests += 1
            if self.requests == 1:
                yield ToolCallCompleted(ToolUseBlock("source", "read_file", {"path": "source.txt"}))
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            elif self.requests == 2:
                result = request.messages[-1]
                assert isinstance(result, ToolResultMessage)
                path = result.details["content_ref"]["path"]
                yield ToolCallCompleted(
                    ToolUseBlock("reference", "read_file", {"path": path, "max_lines": 1})
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            else:
                result = request.messages[-1]
                assert isinstance(result, ToolResultMessage) and not result.is_error
                assert "第一行" in result.content
                yield TextDelta("已读取子会话引用")
                yield MessageCompleted(LlmStopReason.STOP)

    (tmp_path / "source.txt").write_text("第一行\n" + "内容" * 15000, encoding="utf-8")
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("parent", tmp_path, "父会话")
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider())
    adapter = ReadingLlm()
    llm = LlmClient(adapter)
    runner = LocalSubagentRunner(
        llm,
        sessions,
        registry,
        PermissionService(),
        ContextManager(CompactionEngine(llm)),
        AgentOptions("fake"),
    )
    result = await runner.run(
        SubagentRequest(
            "读取文件及引用",
            SubagentType.EXPLORE,
            tmp_path,
            PermissionMode.DEFAULT,
            "parent",
            "delegate",
        )
    )
    assert result.succeeded and result.content == "已读取子会话引用"
    assert adapter.requests == 3
    assert not adapter.closed
    messages = await sessions.bind(result.session_id).messages()
    references = [
        message.details["content_ref"]["path"]
        for message in messages
        if isinstance(message, ToolResultMessage) and "content_ref" in message.details
    ]
    assert references and all(
        str(sessions.directory(result.session_id)) in path for path in references
    )
    assert await sessions.bind("parent").messages() == ()


@pytest.mark.parametrize("kind", list(SubagentType))
async def test_child_memory_tools_are_read_only_and_do_not_close_shared_llm(tmp_path, kind):
    from camel_harness.agent.resources import MemoryEntry, MemoryStore
    from camel_harness.agent.tools.memory_tools import MemoryToolProvider

    memories = MemoryStore(tmp_path, tmp_path / "user-memory")
    await memories.save(
        MemoryEntry("language", "中文注释", "中文说明"),
        expected_revision="0",
        session_id="seed",
        tool_call_id="seed",
    )

    class MemoryLlm(LlmAdapter):
        requests = 0
        closed = False

        @property
        def provider_id(self):
            return "fake"

        async def close(self):
            self.closed = True

        async def stream(self, request):
            self.requests += 1
            names = {tool.name for tool in request.tools}
            assert {"search_memory", "read_memory"} <= names
            assert not {"save_memory", "delete_memory"} & names
            assert "中文说明" in request.system_prompt
            if self.requests == 1:
                yield ToolCallCompleted(
                    ToolUseBlock(
                        "read",
                        "read_memory",
                        {
                            "scope": "project",
                            "name": "language",
                        },
                    )
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            elif self.requests == 2:
                assert "中文说明" in request.messages[-1].content
                yield ToolCallCompleted(
                    ToolUseBlock(
                        "write",
                        "save_memory",
                        {
                            "scope": "user",
                            "name": "unauthorized",
                            "description": "禁止",
                            "content": "不应写入",
                            "expected_revision": "0",
                        },
                    )
                )
                yield MessageCompleted(LlmStopReason.TOOL_USE)
            else:
                assert request.messages[-1].is_error
                yield TextDelta("仅完成只读记忆查询")
                yield MessageCompleted(LlmStopReason.STOP)

    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("parent", tmp_path, "父会话")
    registry = ToolRegistry()
    await registry.register_provider(MemoryToolProvider(memories))
    adapter = MemoryLlm()
    llm = LlmClient(adapter)
    runner = LocalSubagentRunner(
        llm,
        sessions,
        registry,
        PermissionService(),
        ContextManager(CompactionEngine(llm), memories=memories),
        AgentOptions("fake"),
    )
    result = await runner.run(
        SubagentRequest(
            "查询中文注释",
            kind,
            tmp_path,
            PermissionMode.YOLO,
            "parent",
            "delegate",
        )
    )
    assert result.succeeded and result.content == "仅完成只读记忆查询"
    assert adapter.requests == 3 and not adapter.closed
    assert await memories.get("unauthorized", "user") is None
