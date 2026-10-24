import asyncio
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from camel_backend.main import create_app
from camel_harness.agent import platform_io
from camel_harness.agent.config import AgentSettings, MemorySettings
from camel_harness.agent.resources import MemoryStore
from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolContext, ToolResult
from camel_harness.agent.tools.file_result_storage import FileToolResultStorage
from camel_harness.llm import UserMessage


def test_backend_starts_with_isolated_default_config_and_can_create_session(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/health").status_code == 200
        response = client.post("/api/sessions", json={"name": "Windows 启动烟测"})
        assert response.status_code == 201
        assert client.get("/api/sessions").status_code == 200
    assert AgentSettings.load().resolved_session_dir() == tmp_path / ".camel" / "sessions"


async def test_memory_native_create_update_read_delete(tmp_path):
    memory_store = MemoryStore(
        tmp_path / "项目 with spaces",
        MemorySettings(),
        user_root=tmp_path / "user-memory",
    )
    assert (await memory_store.add("memory", "保留中文和换行\n第二行")).changed
    assert (await memory_store.snapshot("memory")).entries == ("保留中文和换行\n第二行",)

    assert (await memory_store.replace("memory", "第二行", "新正文")).changed
    assert (await memory_store.snapshot("memory")).entries == ("新正文",)

    assert (await memory_store.remove("memory", "新正文")).changed
    assert (await memory_store.snapshot("memory")).entries == ()


async def test_native_link_or_junction_cannot_redirect_memory_root(tmp_path):
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / ".camel"
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        assert platform_io.is_link(link)
        with pytest.raises(ValueError, match="符号链接"):
            await MemoryStore(workspace, MemorySettings(), user_root=tmp_path / "user").snapshot(
                "memory"
            )
        assert list(outside.iterdir()) == []
    finally:
        if sys.platform == "win32":
            os.rmdir(link)
        else:
            link.unlink()


async def test_result_archive_keeps_exact_utf8_bytes_on_every_platform(tmp_path):
    directory = tmp_path / "session"
    directory.mkdir()
    tool_result_storage = FileToolResultStorage(lambda _: directory, max_inline_chars=1)
    text = "中文\n第二行\r\n"
    result = await tool_result_storage.normalize(
        ToolResult("c", "read_file", text), ToolContext("s", tmp_path)
    )
    reference = result.metadata["content_ref"]
    assert Path(reference["path"]).read_bytes() == text.encode("utf-8")
    assert reference["size_bytes"] == Path(reference["path"]).stat().st_size


def _append_from_process(root, writer, start):
    async def append():
        session_storage = JsonlSessionStorage(Path(root))
        for number in range(8):
            await session_storage.append_message("shared", UserMessage(f"{writer}-{number}"))

    if not start.wait(10):
        raise TimeoutError("并发测试启动超时")
    asyncio.run(append())


async def test_session_cross_process_appends_keep_single_complete_history(tmp_path):
    root = tmp_path / "sessions"
    session_manager = SessionManager(JsonlSessionStorage(root))
    await session_manager.create("shared", tmp_path, "并发写入")
    process_context = multiprocessing.get_context("spawn")
    start = process_context.Event()
    processes = [
        process_context.Process(target=_append_from_process, args=(str(root), writer, start))
        for writer in range(2)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            await asyncio.to_thread(process.join, 15)
            assert process.exitcode == 0
        session = session_manager.bind("shared")
        messages = await session.messages()
        assert len(messages) == 16
        assert {message.content for message in messages} == {
            f"{writer}-{number}" for writer in range(2) for number in range(8)
        }
        assert len((await session.context_snapshot()).nodes) == 16
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(10)
