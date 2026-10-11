import asyncio
import os
import sys
from importlib import import_module
from pathlib import Path

import pytest

from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import PermissionMode, ToolCall, ToolContext
from camel_harness.agent.tools.file_result_storage import FileToolResultStorage
from camel_harness.agent.tools.shell import RunShellTool


async def test_shell_large_output_reference_contains_all_output(tmp_path, python_command):
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("shell", tmp_path, "大输出")
    storage = FileToolResultStorage(sessions.directory, max_inline_chars=100)
    command = python_command(
        "-c", "import sys; sys.stdout.buffer.write(('中' * 100_000 + '\\n').encode('utf-8'))"
    )
    context = ToolContext("shell", tmp_path, PermissionMode.YOLO)
    result = await RunShellTool(storage).execute(
        ToolCall("out", "run_shell", {"command": command}), context
    )
    reference = result.metadata["content_ref"]
    assert Path(reference["path"]).read_text("utf-8") == "中" * 100_000 + "\n"
    assert reference["original_chars"] == 100_001
    assert reference["size_bytes"] == 300_001
    assert len(result.content) < 500
    assert await storage.normalize(result, context) is result


@pytest.mark.parametrize("cancel", [False, True])
async def test_shell_timeout_or_cancel_terminates_child_process_group(
    tmp_path, cancel, python_command
):
    command = python_command(Path(__file__).parents[1] / "fixtures" / "shell_tree.py")
    call = ToolCall("group", "run_shell", {"command": command, "timeout_seconds": 3})
    task = asyncio.create_task(RunShellTool().execute(call, ToolContext("shell", tmp_path)))
    for _ in range(300):
        if (tmp_path / "child.pid").exists():
            break
        await asyncio.sleep(0.01)
    child = int((tmp_path / "child.pid").read_text())
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task).is_error
    for _ in range(100):
        if sys.platform == "win32":
            win32api = import_module("win32api")
            win32process = import_module("win32process")
            pywintypes = import_module("pywintypes")
            try:
                handle = win32api.OpenProcess(0x1000, False, child)
            except pywintypes.error as exc:
                assert exc.winerror == 87
                return
            try:
                if win32process.GetExitCodeProcess(handle) != 259:
                    return
            finally:
                handle.Close()
            await asyncio.sleep(0.01)
            continue
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.01)
    pytest.fail("Shell 后代仍然存活")
