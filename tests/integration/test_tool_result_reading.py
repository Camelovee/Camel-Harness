from pathlib import Path

import pytest

from camel_harness.agent.session import SessionManager
from camel_harness.agent.session.jsonl import JsonlSessionStorage
from camel_harness.agent.tools import ToolCall, ToolContext, ToolResult
from camel_harness.agent.tools.file_result_storage import FileToolResultStorage
from camel_harness.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from camel_harness.llm import UserMessage


async def setup_result(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionManager(JsonlSessionStorage(tmp_path / "sessions"))
    await sessions.create("parent", workspace, "父会话")
    session = sessions.bind("parent")

    async def registered(session_id):
        bound = session if session_id == "parent" else sessions.bind(session_id)
        return await bound.tool_result_paths()

    store = FileToolResultStorage(
        sessions.directory, max_inline_chars=5, registered_paths=registered
    )
    root = await session.append_message(UserMessage("任务"))
    context = ToolContext("parent", workspace)
    result = await store.normalize(
        ToolResult("call", "run_shell", "第一行\n第二行\n第三行"), context
    )
    await session.append_message(result.to_message())
    return sessions, session, store, context, Path(result.metadata["content_ref"]["path"]), root


async def test_read_file_pages_registered_result_and_preserves_workspace_access(tmp_path):
    _, _, store, context, path, _ = await setup_result(tmp_path)
    tool = ReadFileTool(store.resolve_read_path)
    result = await tool.execute(
        ToolCall(
            "read",
            "read_file",
            {
                "path": str(path),
                "start_line": 2,
                "max_lines": 1,
            },
        ),
        context,
    )
    assert result.content == "2: 第二行"
    (context.workspace / "normal.txt").write_text("正常文件", encoding="utf-8")
    assert (
        await tool.execute(ToolCall("read", "read_file", {"path": "normal.txt"}), context)
    ).content == "1: 正常文件"
    with pytest.raises(ValueError, match="工作区"):
        await ReadFileTool().execute(ToolCall("read", "read_file", {"path": str(path)}), context)


@pytest.mark.parametrize(
    "case", ["unregistered", "traversal", "symlink", "directory_link", "missing"]
)
async def test_reference_read_rejects_invalid_paths(tmp_path, case):
    _, session, store, context, path, _ = await setup_result(tmp_path)
    if case == "unregistered":
        path = path.parent / "unregistered.txt"
        path.write_text("未登记", encoding="utf-8")
    elif case == "traversal":
        path = path.parent / ".." / "tool-results" / path.name
    elif case == "symlink":
        target = tmp_path / "outside.txt"
        target.write_text("外部", encoding="utf-8")
        path.unlink()
        path.symlink_to(target)
    elif case == "directory_link":
        original = path.parent
        moved = original.with_name("moved")
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
    else:
        path.unlink()
    assert await session.tool_result_paths()
    with pytest.raises(ValueError):
        await ReadFileTool(store.resolve_read_path).execute(
            ToolCall("read", "read_file", {"path": str(path)}), context
        )


async def test_current_branch_only_and_compaction_keeps_reference_registration(tmp_path):
    _, session, store, context, path, root = await setup_result(tmp_path)
    kept = await session.append_message(UserMessage("继续"))
    snapshot = await session.context_snapshot()
    await session.compact("历史摘要", kept, snapshot=snapshot)
    assert path in await session.tool_result_paths()
    assert await store.resolve_read_path(context.session_id, str(path)) == path
    await session.branch(root)
    assert not await session.tool_result_paths()
    with pytest.raises(ValueError, match="登记"):
        await store.resolve_read_path(context.session_id, str(path))


async def test_parent_child_sibling_references_are_isolated(tmp_path):
    sessions, _, store, context, parent_path, _ = await setup_result(tmp_path)
    child_paths = []
    for session_id in ("child", "sibling"):
        await sessions.create(session_id, context.workspace, "子会话", parent_session_id="parent")
        child_context = ToolContext(session_id, context.workspace)
        result = await store.normalize(
            ToolResult("out", "run_shell", "子会话完整输出"), child_context
        )
        await sessions.bind(session_id).append_message(result.to_message())
        child_paths.append(Path(result.metadata["content_ref"]["path"]))
    child_context = ToolContext("child", context.workspace)
    tool = ReadFileTool(store.resolve_read_path)
    assert (
        await tool.execute(
            ToolCall("read", "read_file", {"path": str(child_paths[0])}), child_context
        )
    ).content == "1: 子会话完整输出"
    for reader, target in [
        (context, child_paths[0]),
        (child_context, parent_path),
        (child_context, child_paths[1]),
    ]:
        with pytest.raises(ValueError):
            await tool.execute(ToolCall("read", "read_file", {"path": str(target)}), reader)


@pytest.mark.parametrize("tool", [WriteFileTool(), EditFileTool()])
async def test_result_read_permission_does_not_allow_external_mutations(tmp_path, tool):
    _, _, _, context, path, _ = await setup_result(tmp_path)
    arguments = {"path": str(path), "content": "修改", "old_text": "第一行", "new_text": "修改"}
    with pytest.raises(ValueError, match="工作区"):
        await tool.execute(ToolCall("write", tool.name, arguments), context)
    assert path.read_text("utf-8").startswith("第一行")
