import asyncio
from unittest.mock import AsyncMock

import pytest

from camel_harness.agent.tools import ToolCall, ToolContext, shell


async def test_windows_shell_uses_cmd_utf8_and_process_group(monkeypatch, tmp_path):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    monkeypatch.setattr(shell.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    process = AsyncMock(returncode=0)
    factory = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", factory)
    result = await shell.RunShellTool().execute(
        ToolCall("shell", "run_shell", {"command": 'echo "C:\\Users\\camel"'}),
        ToolContext("session", tmp_path),
    )
    args, kwargs = factory.call_args
    assert args[0].startswith("chcp 65001 >nul & ")
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert kwargs["creationflags"] == 512 and kwargs["start_new_session"] is False
    assert not result.is_error
    assert "C:\\Users\\camel" in " ".join(result.metadata["argv_preview"])


async def test_windows_cancel_kills_tree_and_waits_despite_repeated_cancellation(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_wait():
        started.set()
        await release.wait()

    process = AsyncMock(pid=456, returncode=None)
    killer = AsyncMock(returncode=0)
    killer.wait.side_effect = delayed_wait
    factory = AsyncMock(return_value=killer)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", factory)
    task = asyncio.create_task(shell._stop_process_group(process))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert factory.call_args.args == ("taskkill.exe", "/PID", "456", "/T", "/F")
    process.wait.assert_awaited_once()


async def test_windows_tree_cleanup_failure_is_not_hidden(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    process = AsyncMock(pid=456, returncode=None)
    process.kill = lambda: None
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=AsyncMock(returncode=1))
    )
    with pytest.raises(RuntimeError, match="清理失败"):
        await shell._stop_process_group(process)
    process.wait.assert_awaited_once()


@pytest.mark.parametrize(
    "command", ["rd /s /q target", "del /s target", "format C:", "Remove-Item x -Recurse"]
)
async def test_windows_dangerous_commands_are_blocked(tmp_path, command):
    result = await shell.RunShellTool().execute(
        ToolCall("shell", "run_shell", {"command": command}), ToolContext("session", tmp_path)
    )
    assert result.is_error and "安全规则" in result.content


def test_preview_does_not_turn_execution_into_parse_failure():
    assert shell._command_preview("echo 'unterminated") == ["echo 'unterminated"]
