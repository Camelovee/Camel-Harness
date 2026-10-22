import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp import ClientSession

from camel_backend.api.sse import encode_sse
from camel_backend.approval import WebApproval
from camel_harness.agent import Agent, AgentOptions
from camel_harness.agent.context import CompactionEngine, ContextManager
from camel_harness.agent.events import AgentEvent, ApprovalRequestedEvent, ToolExecutionEndEvent
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import (
    PermissionMode,
    PermissionService,
    Tool,
    ToolCall,
    ToolContext,
    ToolExecutionStatus,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolRisk,
)
from camel_harness.agent.tools.file_result_storage import FileToolResultStorage
from camel_harness.agent.tools.filesystem import ReadFileTool, WriteFileTool
from camel_harness.agent.tools.mcp import McpRemoteTool
from camel_harness.agent.tools.shell import RunShellTool
from camel_harness.agent.tools.types import UNKNOWN_EXECUTION_NOTICE
from camel_harness.llm import (
    LlmAdapter,
    LlmClient,
    LlmModel,
    LlmRequest,
    LlmStopReason,
    LlmStreamEvent,
    MessageCompleted,
    TextDelta,
    ToolCallCompleted,
    ToolResultMessage,
    ToolUseBlock,
)

_MODEL = LlmModel("fake", "fake-model")


def _executor(tool: Tool, approval: WebApproval | None = None) -> ToolExecutor:
    tool_registry = ToolRegistry()
    tool_registry.register(tool)
    return ToolExecutor(tool_registry, PermissionService(approval))


async def _pending(approval: WebApproval) -> str:
    async with asyncio.timeout(2):
        while not (pending := await approval.list_pending()):
            await asyncio.sleep(0)
    return pending[0].request_id


async def test_file_revision_matches_raw_bytes_and_rejects_stale_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    original = "第一行\r\n第二行\r\n".encode()
    path.write_bytes(original)
    context = ToolContext("file", tmp_path, PermissionMode.YOLO)
    read_result = await ReadFileTool().execute(
        ToolCall("read", "read_file", {"path": path.name, "start_line": 2, "max_lines": 1}), context
    )
    revision = hashlib.sha256(original).hexdigest()
    assert read_result.content == f"文件版本：{revision}\n2: 第二行"
    assert read_result.metadata["file_revision"] == revision
    executor = _executor(WriteFileTool())
    tool_call = ToolCall(
        "write", "write_file", {"path": path.name, "content": "替换", "expected_revision": revision}
    )
    results = await asyncio.gather(
        executor.execute(tool_call, context), executor.execute(tool_call, context)
    )
    assert sorted(result.metadata["execution_status"] for result in results) == [
        "not_executed",
        "succeeded",
    ]
    assert path.read_text() == "替换"
    conflict = next(result for result in results if result.is_error)
    assert "版本冲突" in conflict.content
    succeeded = next(result for result in results if not result.is_error)
    assert succeeded.metadata["file_revision"] == hashlib.sha256("替换".encode()).hexdigest()


@pytest.mark.parametrize("change", ["modified", "deleted", "created"])
async def test_file_changes_during_approval_are_not_overwritten(
    tmp_path: Path, change: str
) -> None:
    path = tmp_path / "sample.txt"
    revision = "0"
    if change != "created":
        path.write_text("原文", encoding="utf-8")
        revision = hashlib.sha256(path.read_bytes()).hexdigest()
    approval = WebApproval()
    task = asyncio.create_task(
        _executor(WriteFileTool(), approval).execute(
            ToolCall(
                "write",
                "write_file",
                {"path": path.name, "content": "不应覆盖", "expected_revision": revision},
            ),
            ToolContext("file", tmp_path),
        )
    )
    request_id = await _pending(approval)
    if change == "deleted":
        path.unlink()
    else:
        path.write_text("用户修改", encoding="utf-8")
    assert await approval.resolve(request_id, True)
    result = await task
    assert result.is_error and result.metadata["execution_status"] == "not_executed"
    assert "版本冲突" in result.content
    if change == "deleted":
        assert not path.exists()
    else:
        assert path.read_text() == "用户修改"


async def test_missing_revision_and_denied_calls_are_not_executed(tmp_path: Path) -> None:
    executor = _executor(WriteFileTool())
    for arguments, mode in [
        ({"path": "sample.txt", "content": "正文"}, PermissionMode.YOLO),
        (
            {"path": "sample.txt", "content": "正文", "expected_revision": "0"},
            PermissionMode.DEFAULT,
        ),
        ({"path": "sample.txt", "content": "正文", "expected_revision": "0"}, PermissionMode.PLAN),
    ]:
        result = await executor.execute(
            ToolCall("write", "write_file", arguments), ToolContext("file", tmp_path, mode)
        )
        assert result.is_error and result.metadata["execution_status"] == "not_executed"
    assert not (tmp_path / "sample.txt").exists()


async def test_user_rejection_never_starts_the_write(tmp_path: Path) -> None:
    approval = WebApproval()
    task = asyncio.create_task(
        _executor(WriteFileTool(), approval).execute(
            ToolCall(
                "write",
                "write_file",
                {"path": "sample.txt", "content": "正文", "expected_revision": "0"},
            ),
            ToolContext("file", tmp_path),
        )
    )
    assert await approval.resolve(await _pending(approval), False)
    result = await task
    assert result.is_error and result.metadata["execution_status"] == "not_executed"
    assert not (tmp_path / "sample.txt").exists()


@pytest.mark.parametrize("failure", ["reported_error", "transport", "timeout"])
async def test_mcp_results_preserve_known_and_unknown_side_effects(
    tmp_path: Path, failure: str
) -> None:
    marker = tmp_path / "effect"

    class FakeSession:
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            marker.write_text("已经修改", encoding="utf-8")
            if failure == "timeout":
                await asyncio.Event().wait()
            if failure == "transport":
                raise ConnectionError("模拟连接断开")
            return SimpleNamespace(
                content=[SimpleNamespace(text="部分执行后报告失败")], isError=True
            )

    tool = McpRemoteTool(
        "fake", "change", "离线工具", {"type": "object"}, cast(ClientSession, FakeSession())
    )
    tool.timeout_seconds = 0.02
    result = await _executor(tool).execute(
        ToolCall("mcp", tool.qualified_name, {}), ToolContext("mcp", tmp_path, PermissionMode.YOLO)
    )
    assert marker.read_text() == "已经修改"
    assert result.is_error
    assert result.metadata["execution_status"] == (
        "failed" if failure == "reported_error" else "unknown"
    )
    if failure != "reported_error":
        assert result.content.startswith(UNKNOWN_EXECUTION_NOTICE)


@pytest.mark.parametrize("timeout_source", ["shell", "executor", "exit_failure"])
async def test_shell_side_effect_is_not_reported_as_unexecuted(
    tmp_path: Path, python_command: Callable[..., str], timeout_source: str
) -> None:
    script = tmp_path / "action.py"
    script.write_text(
        "from pathlib import Path\nimport time\n"
        "Path('effect').write_text('已执行', encoding='utf-8')\n"
        + ("raise SystemExit(7)\n" if timeout_source == "exit_failure" else "time.sleep(10)\n"),
        encoding="utf-8",
    )
    tool = RunShellTool()
    if timeout_source == "executor":
        tool.timeout_seconds = 1
    result = await _executor(tool).execute(
        ToolCall(
            "shell",
            "run_shell",
            {
                "command": python_command(script),
                "timeout_seconds": 1 if timeout_source == "shell" else 30,
            },
        ),
        ToolContext("shell", tmp_path, PermissionMode.YOLO),
    )
    assert (tmp_path / "effect").read_text() == "已执行"
    assert result.metadata["execution_status"] == (
        "failed" if timeout_source == "exit_failure" else "unknown"
    )
    if timeout_source != "exit_failure":
        assert result.content.startswith(UNKNOWN_EXECUTION_NOTICE)


async def test_executor_freezes_nested_arguments_before_approval(tmp_path: Path) -> None:
    approval = WebApproval()
    captured: dict[str, Any] = {}

    class RecordingTool(Tool):
        name = "record"
        risk = ToolRisk.WRITE
        parameters = {"type": "object"}

        async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
            captured.update(tool_call.arguments)
            assert tool_context.workspace == tmp_path.resolve()
            return ToolResult(tool_call.call_id, tool_call.name, "完成")

    arguments = {"nested": {"value": "批准内容"}}
    task = asyncio.create_task(
        _executor(RecordingTool(), approval).execute(
            ToolCall("snapshot", "record", arguments),
            ToolContext("snapshot", tmp_path / "nested" / ".."),
        )
    )
    request_id = await _pending(approval)
    arguments["nested"]["value"] = "审批后篡改"
    pending = (await approval.list_pending())[0]
    assert pending.arguments == {"nested": {"value": "批准内容"}}
    assert await approval.resolve(request_id, True)
    assert not (await task).is_error
    assert captured == {"nested": {"value": "批准内容"}}


async def test_shell_approval_event_and_pending_use_actual_execution_context(
    tmp_path: Path, python_command: Callable[..., str]
) -> None:
    command = python_command("-c", "from pathlib import Path; print(Path.cwd())") + "\n"
    approval = WebApproval()

    class ShellLlm(LlmAdapter):
        @property
        def provider_id(self) -> str:
            return "fake"

        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            if isinstance(llm_request.messages[-1], ToolResultMessage):
                yield TextDelta("已执行")
                yield MessageCompleted(LlmStopReason.STOP)
            else:
                yield ToolCallCompleted(ToolUseBlock("shell", "run_shell", {"command": command}))
                yield MessageCompleted(LlmStopReason.TOOL_USE)

    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("approval", tmp_path, "审批上下文")
    llm_client = LlmClient(ShellLlm())
    agent = Agent(
        session=session_manager.bind("approval"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=_executor(RunShellTool(), approval),
        context_manager=ContextManager(CompactionEngine(llm_client), system_prompt="离线测试"),
    )
    events: list[AgentEvent] = []
    agent.subscribe(events.append)
    task = asyncio.create_task(agent.run("打印目录", model=_MODEL))
    try:
        request_id = await _pending(approval)
        pending = (await approval.list_pending())[0]
        event = next(event for event in events if isinstance(event, ApprovalRequestedEvent))
        expected_context = RunShellTool().approval_context(
            ToolCall("shell", "run_shell", {"command": command}), ToolContext("approval", tmp_path)
        )
        assert event.execution_context == pending.execution_context == expected_context
        payload = json.loads(encode_sse(event).split("data: ", 1)[1])
        assert payload["execution_context"] == expected_context
        assert expected_context["command"].endswith(command)
        assert expected_context["working_directory"] == str(tmp_path.resolve())
        assert await approval.resolve(request_id, True)
        await task
        result_event = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
        assert str(tmp_path.resolve()) in result_event.result.content
        assert result_event.result.metadata["execution_status"] == "succeeded"
    finally:
        await agent.close()
        await task


async def test_unknown_result_survives_stream_archive_and_session_reload(tmp_path: Path) -> None:
    class UncertainTool(Tool):
        name = "uncertain"
        risk = ToolRisk.WRITE
        parameters = {"type": "object"}
        calls = 0

        async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
            self.calls += 1
            return ToolResult(
                tool_call.call_id, tool_call.name, "执行正文" * 100
            ).with_execution_status(ToolExecutionStatus.UNKNOWN)

    class CheckingLlm(LlmAdapter):
        @property
        def provider_id(self) -> str:
            return "fake"

        async def stream(self, llm_request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            result = llm_request.messages[-1]
            if isinstance(result, ToolResultMessage):
                assert result.content.startswith(UNKNOWN_EXECUTION_NOTICE)
                assert result.details["execution_status"] == "unknown"
                yield TextDelta("需要先核验状态")
                yield MessageCompleted(LlmStopReason.STOP)
            else:
                yield ToolCallCompleted(ToolUseBlock("uncertain", "uncertain", {}))
                yield MessageCompleted(LlmStopReason.TOOL_USE)

    session_manager = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await session_manager.create("unknown", tmp_path, "未知结果")
    tool = UncertainTool()
    registry = ToolRegistry()
    registry.register(tool)
    storage = FileToolResultStorage(session_manager.directory, max_inline_chars=1)
    llm_client = LlmClient(CheckingLlm())
    agent = Agent(
        session=session_manager.bind("unknown"),
        workspace=tmp_path,
        agent_options=AgentOptions(),
        llm_client=llm_client,
        tool_executor=ToolExecutor(registry, normalize_result=storage.normalize),
        context_manager=ContextManager(CompactionEngine(llm_client), system_prompt="离线测试"),
    )
    events: list[AgentEvent] = []
    agent.subscribe(events.append)
    try:
        await agent.run("模拟未知结果", model=_MODEL, permission_mode=PermissionMode.YOLO)
    finally:
        await agent.close()
    event = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
    assert event.result.metadata["execution_status"] == "unknown"
    payload = json.loads(encode_sse(event).split("data: ", 1)[1])
    assert payload["result"]["metadata"]["execution_status"] == "unknown"
    assert event.result.content.startswith(UNKNOWN_EXECUTION_NOTICE)
    messages = (
        await SessionManager(JsonlSessionStorage(tmp_path / "sessions")).bind("unknown").messages()
    )
    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert result.content.startswith(UNKNOWN_EXECUTION_NOTICE)
    assert result.details["execution_status"] == "unknown"
    assert (
        Path(result.details["content_ref"]["path"])
        .read_text("utf-8")
        .startswith(UNKNOWN_EXECUTION_NOTICE)
    )
    assert tool.calls == 1
