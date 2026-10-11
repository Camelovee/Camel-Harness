import asyncio
import json
import os
import sys
from importlib import import_module
from pathlib import Path

import pytest

from camel_harness.agent.tools import ToolCall, ToolContext
from camel_harness.agent.tools.mcp import McpManager

SERVER = Path(__file__).parents[1] / "fixtures" / "mcp_server.py"


def manager_for(tmp_path, modes):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {
                        "command": sys.executable,
                        "args": [str(SERVER), mode, str(tmp_path / name)],
                    }
                    for name, mode in modes.items()
                }
            }
        ),
        encoding="utf-8",
    )
    return McpManager(tmp_path, config)


async def wait_file(path):
    async with asyncio.timeout(5):
        while not path.exists():
            await asyncio.sleep(0.01)


def assert_exited(path):
    if sys.platform == "win32":
        win32api = import_module("win32api")
        pywintypes = import_module("pywintypes")
        try:
            handle = win32api.OpenProcess(0x1000, False, int(path.read_text()))
        except pywintypes.error as exc:
            assert exc.winerror == 87
            return
        try:
            assert import_module("win32process").GetExitCodeProcess(handle) != 259
        finally:
            handle.Close()
        return
    with pytest.raises(ProcessLookupError):
        os.kill(int(path.read_text()), 0)


async def test_real_mcp_start_call_and_cross_task_close(tmp_path):
    manager = manager_for(tmp_path, {"good": "good", "other": "good"})
    await asyncio.wait_for(manager.start(), 5)
    tools = await manager.discover()
    assert len(tools) == 2
    result = await tools[0].execute(
        ToolCall("ping", tools[0].qualified_name, {}), ToolContext("s", tmp_path)
    )
    assert result.content == "pong"
    await asyncio.wait_for(asyncio.create_task(manager.close()), 5)
    await manager.close()
    assert manager._task.done()
    assert_exited(tmp_path / "good")
    assert_exited(tmp_path / "other")


@pytest.mark.parametrize("good_first", [False, True])
async def test_failed_mcp_connection_is_cleaned_without_breaking_good_server(tmp_path, good_first):
    modes = {"good": "good", "failed": "fail"} if good_first else {"failed": "fail", "good": "good"}
    manager = manager_for(tmp_path, modes)
    try:
        await asyncio.wait_for(manager.start(), 5)
        assert len(await manager.discover()) == 1
        assert len(manager.errors) == 1
        assert_exited(tmp_path / "failed")
    finally:
        await manager.close()
    assert_exited(tmp_path / "good")


async def test_cancelling_mcp_start_cleans_hanging_handshake(tmp_path):
    manager = manager_for(tmp_path, {"hang": "hang"})
    start = asyncio.create_task(manager.start())
    await wait_file(tmp_path / "hang")
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(start, 5)
    assert manager._task.done()
    assert_exited(tmp_path / "hang")


async def test_cancelling_close_waiter_does_not_cancel_mcp_cleanup(tmp_path):
    manager = manager_for(tmp_path, {"slow": "slow"})
    await asyncio.wait_for(manager.start(), 5)
    close = asyncio.create_task(manager.close())
    await wait_file(tmp_path / "slow.closing")
    close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close
    await asyncio.wait_for(manager.close(), 5)
    assert_exited(tmp_path / "slow")


async def test_mcp_start_failure_remains_observable(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text("[]", encoding="utf-8")
    manager = McpManager(tmp_path, config)
    with pytest.raises(ValueError, match="顶层"):
        await manager.start()
    assert manager._task.done()
    with pytest.raises(ValueError, match="顶层"):
        await manager.discover()
    with pytest.raises(ValueError, match="顶层"):
        await manager.close()


async def test_immediate_start_cancellation_does_not_leave_owner_task(tmp_path):
    manager = manager_for(tmp_path, {"hang": "hang"})
    start = asyncio.create_task(manager.start())
    await asyncio.sleep(0)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(start, 5)
    assert manager._task.done()
    await manager.close()


async def test_start_cancellation_also_closes_previously_initialized_server(tmp_path):
    manager = manager_for(tmp_path, {"good": "good", "hang": "hang"})
    start = asyncio.create_task(manager.start())
    await wait_file(tmp_path / "hang")
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(start, 5)
    assert_exited(tmp_path / "good")
    assert_exited(tmp_path / "hang")
