import asyncio
import hashlib
import threading
from pathlib import Path

import pytest

from camel_harness.agent.tools import ToolCall, ToolContext, filesystem
from camel_harness.agent.tools.filesystem import EditFileTool, WriteFileTool


async def test_write_reports_utf8_bytes_and_preserves_permissions(tmp_path):
    path = tmp_path / "nested" / "sample.txt"
    tool_context = ToolContext("session", tmp_path)
    result = await WriteFileTool().execute(
        ToolCall(
            "write",
            "write_file",
            {"path": "nested/sample.txt", "content": "中文", "expected_revision": "0"},
        ),
        tool_context,
    )
    assert "6 字节" in result.content
    assert path.read_text() == "中文"
    path.chmod(0o754)
    await WriteFileTool().execute(
        ToolCall(
            "write-again",
            "write_file",
            {
                "path": "nested/sample.txt",
                "content": "新内容",
                "expected_revision": hashlib.sha256("中文".encode()).hexdigest(),
            },
        ),
        tool_context,
    )
    assert path.stat().st_mode & 0o777 == 0o754
    assert not list(path.parent.glob("*.tmp"))


async def test_replace_failure_preserves_original_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / "sample.txt"
    path.write_text("原始内容")

    def fail_replace(self, target):
        raise OSError("替换失败")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="替换失败"):
        await WriteFileTool().execute(
            ToolCall(
                "write",
                "write_file",
                {
                    "path": path.name,
                    "content": "新内容",
                    "expected_revision": hashlib.sha256("原始内容".encode()).hexdigest(),
                },
            ),
            ToolContext("session", tmp_path),
        )
    assert path.read_text() == "原始内容"
    assert list(tmp_path.iterdir()) == [path]


async def test_concurrent_edits_do_not_lose_updates(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("first second")
    tool_context = ToolContext("session", tmp_path)
    await asyncio.gather(
        EditFileTool().execute(
            ToolCall(
                "first", "edit_file", {"path": path.name, "old_text": "first", "new_text": "一"}
            ),
            tool_context,
        ),
        EditFileTool().execute(
            ToolCall(
                "second", "edit_file", {"path": path.name, "old_text": "second", "new_text": "二"}
            ),
            tool_context,
        ),
    )
    assert path.read_text() == "一 二"
    assert not filesystem._mutations


async def test_cancelled_write_keeps_queue_until_thread_finishes(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    original = filesystem._atomic_write

    def delayed_write(path, content):
        if content == "第一份":
            started.set()
            assert release.wait(5)
        else:
            second_started.set()
        return original(path, content)

    monkeypatch.setattr(filesystem, "_atomic_write", delayed_write)
    tool_context = ToolContext("session", tmp_path)
    tool = WriteFileTool()
    first = asyncio.create_task(
        tool.execute(
            ToolCall(
                "first",
                "write_file",
                {"path": "sample.txt", "content": "第一份", "expected_revision": "0"},
            ),
            tool_context,
        )
    )
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        second = asyncio.create_task(
            tool.execute(
                ToolCall(
                    "second",
                    "write_file",
                    {
                        "path": "./sample.txt",
                        "content": "第二份",
                        "expected_revision": hashlib.sha256("第一份".encode()).hexdigest(),
                    },
                ),
                tool_context,
            )
        )
        await asyncio.sleep(0.02)
        first.cancel()
        await asyncio.sleep(0.02)
        assert not first.done()
        assert not second_started.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        if second is not None:
            await second
    assert (tmp_path / "sample.txt").read_text() == "第二份"
    assert not filesystem._mutations


async def test_cancelled_queued_write_never_modifies_file(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    original = filesystem._atomic_write

    def delayed_write(path, content):
        started.set()
        assert release.wait(5)
        return original(path, content)

    monkeypatch.setattr(filesystem, "_atomic_write", delayed_write)
    tool_context = ToolContext("session", tmp_path)
    tool = WriteFileTool()
    first = asyncio.create_task(
        tool.execute(
            ToolCall(
                "first",
                "write_file",
                {"path": "sample.txt", "content": "保留", "expected_revision": "0"},
            ),
            tool_context,
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    second = asyncio.create_task(
        tool.execute(
            ToolCall(
                "second",
                "write_file",
                {"path": "sample.txt", "content": "不能写入", "expected_revision": "0"},
            ),
            tool_context,
        )
    )
    try:
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
    finally:
        release.set()
        await first
    assert (tmp_path / "sample.txt").read_text() == "保留"
    assert not filesystem._mutations
