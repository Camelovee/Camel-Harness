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
    LlmModel,
    LlmStopReason,
    MessageCompleted,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
)

_MODEL = LlmModel("fake", "fake-model")


async def test_shell_archive_failure_ends_run_without_repeating_side_effect(
    tmp_path, monkeypatch, python_command
):
    command = python_command(
        "-c",
        "from pathlib import Path; p = Path('marker'); "
        "p.write_text(p.read_text() + 'x' if p.exists() else 'x'); print('output')",
    )

    class ShellLlm(LlmAdapter):
        calls = 0

        @property
        def provider_id(self):
            return "fake"

        async def stream(self, llm_request):
            self.calls += 1
            yield ToolCallCompleted(ToolUseBlock("shell", "run_shell", {"command": command}))
            yield MessageCompleted(LlmStopReason.TOOL_USE)

    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("failure", tmp_path, "归档失败测试")
    tool_result_storage = FileToolResultStorage(session_manager.directory, max_inline_chars=1)

    def fail_write(*args):
        raise OSError("模拟磁盘写入失败")

    monkeypatch.setattr(tool_result_storage, "_write", fail_write)
    tool_registry = ToolRegistry()
    await tool_registry.register_provider(BuiltinToolProvider(tool_result_storage))
    adapter = ShellLlm()
    llm_client = LlmClient(adapter)
    agent = Agent(
        session=session_manager.bind("failure"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(
            tool_registry=tool_registry, normalize_result=tool_result_storage.normalize
        ),
        context_manager=ContextManager(
            system_prompt="测试提示", compaction_engine=CompactionEngine(llm_client=llm_client)
        ),
    )
    events = []
    agent.subscribe(events.append)
    await agent.run("执行命令", permission_mode=PermissionMode.YOLO, model=_MODEL)
    assert (tmp_path / "marker").read_text() == "x"
    assert adapter.calls == 1
    assert events[-1].status == "failed"
    assert events[-1].stop_reason == "tool_failed"
    assert "归档失败" in events[-1].error
    assert sum(event.type == "agent_end" for event in events) == 1
    results = [
        message
        for message in await session_manager.bind("failure").messages()
        if isinstance(message, ToolResultMessage)
    ]
    assert len(results) == 1 and results[0].is_error
    assert results[0].call_id == "shell"


async def test_plain_result_storage_failure_uses_specific_error(tmp_path, monkeypatch):
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("failure", tmp_path, "测试")
    tool_result_storage = FileToolResultStorage(session_manager.directory, max_inline_chars=1)

    def failed_write(*args):
        raise OSError("模拟磁盘故障")

    monkeypatch.setattr(tool_result_storage, "_write", failed_write)
    with pytest.raises(ToolResultStorageError) as failure:
        await tool_result_storage.normalize(
            ToolResult("call", "read_file", "结果"), ToolContext("failure", tmp_path)
        )
    assert isinstance(failure.value.__cause__, OSError)


async def test_cancelled_plain_archive_waits_for_thread_and_removes_unreferenced_file(
    tmp_path, monkeypatch
):
    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("cancel", tmp_path, "测试")
    tool_result_storage = FileToolResultStorage(session_manager.directory, max_inline_chars=1)
    entered, release = threading.Event(), threading.Event()
    original = tool_result_storage._write
    paths: list[Path] = []

    def delayed_write(*args):
        entered.set()
        release.wait(5)
        path = original(*args)
        paths.append(path)
        return path

    monkeypatch.setattr(tool_result_storage, "_write", delayed_write)
    task = asyncio.create_task(
        tool_result_storage.normalize(
            ToolResult("call", "read_file", "结果"), ToolContext("cancel", tmp_path)
        )
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
