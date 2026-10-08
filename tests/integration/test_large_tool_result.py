from pathlib import Path

import pytest

from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolContext, ToolResult
from camel_harness.agent.tools.file_result_storage import (
    FileToolResultStorage,
)


@pytest.mark.parametrize("depth", [0, 1, 2])
async def test_large_result_is_offloaded_to_session(tmp_path, depth) -> None:
    root = tmp_path / "global" / "sessions"
    workspace = tmp_path / "workspace"
    sessions = SessionManager(JsonlSessionStorage(root))
    parent = None
    expected = root
    for index in range(depth + 1):
        session_id = f"session-{index}"
        await sessions.create(session_id, workspace, "测试", parent_session_id=parent)
        expected = expected / session_id
        if index < depth:
            expected = expected / "subagents"
        parent = session_id
    # 重新加载仍能找到嵌套目录，不依赖创建时的内存缓存。
    sessions = SessionManager(JsonlSessionStorage(root))
    store = FileToolResultStorage(sessions.directory, max_inline_chars=5)
    result = await store.normalize(
        ToolResult("call-1", "read_file", "123456789"),
        ToolContext(session_id, workspace),
    )
    reference_data = result.metadata["content_ref"]
    assert reference_data["original_chars"] == 9
    assert reference_data["size_bytes"] == 9
    assert reference_data["preview_chars"] == 5
    assert reference_data["mime_type"] == "text/plain; charset=utf-8"
    reference = reference_data["path"]
    assert "完整内容" in result.content
    path = Path(reference)
    assert path.is_absolute()
    assert path.parent == expected / "tool-results"
    assert path.read_text("utf-8") == "123456789"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not (workspace / ".camel").exists()
    await sessions.delete("session-0")
    assert not path.exists()
    assert not (root / "session-0").exists()


async def test_large_results_do_not_overwrite_repeated_call_ids(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("session-1", tmp_path, "测试")
    store = FileToolResultStorage(sessions.directory, max_inline_chars=5)
    context = ToolContext("session-1", tmp_path)
    first = await store.normalize(ToolResult("../call", "read_file", "123456"), context)
    second = await store.normalize(
        ToolResult("../call", "read_file", "abcdef", True, {"source": "测试"}), context
    )
    assert first.metadata["content_ref"] != second.metadata["content_ref"]
    assert Path(first.metadata["content_ref"]["path"]).read_text() == "123456"
    assert Path(second.metadata["content_ref"]["path"]).read_text() == "abcdef"
    assert second.is_error
    assert second.metadata["source"] == "测试"


async def test_small_result_does_not_create_files(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    store = FileToolResultStorage(sessions.directory, max_inline_chars=5)
    original = ToolResult("call", "read_file", "12345")
    assert await store.normalize(original, ToolContext("missing", tmp_path)) is original
    assert not (tmp_path / "sessions").exists()


async def test_result_storage_rejects_missing_session_and_symlink(tmp_path) -> None:
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    store = FileToolResultStorage(sessions.directory, max_inline_chars=5)
    result = ToolResult("call", "read_file", "123456")
    with pytest.raises(KeyError, match="会话不存在"):
        await store.normalize(result, ToolContext("missing", tmp_path))
    await sessions.create("session-1", tmp_path, "测试")
    outside = tmp_path / "outside"
    outside.mkdir()
    (sessions.directory("session-1") / "tool-results").symlink_to(outside)
    with pytest.raises(ValueError, match="符号链接"):
        await store.normalize(result, ToolContext("session-1", tmp_path))
    assert not list(outside.iterdir())
