import asyncio
from pathlib import Path

import pytest

from camel_backend.approval import WebApproval
from camel_harness.agent.tools import PermissionMode, PermissionRequest, shell
from camel_harness.agent.tools.types import ToolCall, ToolContext


async def test_web_approval_can_be_resolved_from_pending_queue() -> None:
    """浏览器审批应挂起工具调用，并在用户决定后恢复。"""

    web_approval = WebApproval(timeout_seconds=1)
    permission_request = PermissionRequest(
        "write_file", frozenset({"write"}), "write", {"path": "a"}, PermissionMode.DEFAULT
    )
    task = asyncio.create_task(web_approval.approve(permission_request, "需要修改工作区"))
    await asyncio.sleep(0)

    pending = await web_approval.list_pending()
    assert len(pending) == 1
    assert pending[0].tool_name == "write_file"
    assert await web_approval.resolve(pending[0].request_id, True)
    assert await task
    assert await web_approval.list_pending() == ()


async def test_windows_shell_context_matches_complete_launched_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    captured: dict[str, object] = {}

    class CompletedProcess:
        returncode = 0

        async def wait(self) -> int:
            return 0

    async def launch(command: str, **arguments: object) -> CompletedProcess:
        captured.update(command=command, **arguments)
        return CompletedProcess()

    monkeypatch.setattr(shell.asyncio, "create_subprocess_shell", launch)
    command = 'echo "first"\necho "second"'
    tool = shell.RunShellTool()
    tool_call = ToolCall("shell", "run_shell", {"command": command})
    tool_context = ToolContext("shell", tmp_path)
    context = tool.approval_context(tool_call, tool_context)
    assert context == {
        "command": f"chcp 65001 >nul & {command}",
        "working_directory": str(tmp_path.resolve()),
    }
    assert not (await tool.execute(tool_call, tool_context)).is_error
    assert captured["command"] == context["command"]
    assert str(captured["cwd"]) == context["working_directory"]
