import asyncio
import threading
from pathlib import Path

import pytest

from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import (
    PermissionMode,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)
from camel_harness.agent.tools.builtin import BuiltinToolProvider
from camel_harness.agent.tools.file_result_storage import (
    FileToolResultStorage,
    ToolResultStorageError,
)
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmStopReason,
    MessageCompleted,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
)


async def test_shell_archive_failure_ends_run_without_repeating_side_effect(tmp_path, monkeypatch):
    class ShellLlm(LlmAdapter):
        calls = 0

        @property
        def provider_id(self):
            return "fake"

        async def stream(self, request):
            self.calls += 1
            yield ToolCallCompleted(
                ToolUseBlock("shell", "run_shell", {"command": "printf x >> marker; printf output"})
            )
            yield MessageCompleted(LlmStopReason.TOOL_USE)

    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("failure", tmp_path, "归档失败测试")
    storage = FileToolResultStorage(sessions.directory, max_inline_chars=1)

    def fail_write(*args):
        raise OSError("模拟磁盘写入失败")

    monkeypatch.setattr(storage, "_write", fail_write)
    registry = ToolRegistry()
    await registry.register_provider(BuiltinToolProvider(storage))
    adapter = ShellLlm()
    llm = LlmClient(adapter)
    agent = Agent(
        sessions.bind("failure"),
        tmp_path,
        AgentOptions("fake"),
        llm,
        registry,
        ToolExecutor(registry, normalize_result=storage.normalize),
        ContextManager(CompactionEngine(llm)),
    )
    events = []
    agent.subscribe(events.append)
    await agent.run("执行命令", permission_mode=PermissionMode.YOLO)
    assert (tmp_path / "marker").read_text() == "x"
    assert adapter.calls == 1
    assert events[-1].status == "failed"
    assert events[-1].stop_reason == "tool_failed"
    assert "归档失败" in events[-1].error
    assert sum(event.type == "agent_end" for event in events) == 1
    results = [
        message
        for message in await sessions.bind("failure").messages()
        if isinstance(message, ToolResultMessage)
    ]
    assert len(results) == 1 and results[0].is_error
    assert results[0].call_id == "shell"


async def test_plain_result_storage_failure_uses_specific_error(tmp_path, monkeypatch):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("failure", tmp_path, "测试")
    storage = FileToolResultStorage(sessions.directory, max_inline_chars=1)

    def failed_write(*args):
        raise OSError("模拟磁盘故障")

    monkeypatch.setattr(storage, "_write", failed_write)
    with pytest.raises(ToolResultStorageError) as failure:
        await storage.normalize(
            ToolResult("call", "read_file", "结果"), ToolContext("failure", tmp_path)
        )
    assert isinstance(failure.value.__cause__, OSError)


async def test_cancelled_plain_archive_waits_for_thread_and_removes_unreferenced_file(
    tmp_path, monkeypatch
):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("cancel", tmp_path, "测试")
    storage = FileToolResultStorage(sessions.directory, max_inline_chars=1)
    entered, release = threading.Event(), threading.Event()
    original = storage._write
    paths: list[Path] = []

    def delayed_write(*args):
        entered.set()
        release.wait(5)
        path = original(*args)
        paths.append(path)
        return path

    monkeypatch.setattr(storage, "_write", delayed_write)
    task = asyncio.create_task(
        storage.normalize(ToolResult("call", "read_file", "结果"), ToolContext("cancel", tmp_path))
    )
    await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert len(paths) == 1
    assert not paths[0].exists()
