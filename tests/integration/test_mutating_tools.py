from camel_harness.harness.tools.builtin import EditFileTool, RunShellTool, WriteFileTool
from camel_harness.types import PermissionMode, ToolCall, ToolContext


async def test_write_and_edit_file_stay_in_workspace(tmp_path) -> None:
    context = ToolContext("session", tmp_path, PermissionMode.ACCEPT_EDITS)
    await WriteFileTool().execute(
        ToolCall("write", "write_file", {"path": "a.txt", "content": "旧内容"}), context
    )
    result = await EditFileTool().execute(
        ToolCall(
            "edit",
            "edit_file",
            {"path": "a.txt", "old_text": "旧内容", "new_text": "新内容"},
        ),
        context,
    )
    assert not result.is_error
    assert (tmp_path / "a.txt").read_text("utf-8") == "新内容"


async def test_shell_blocks_dangerous_command(tmp_path) -> None:
    result = await RunShellTool().execute(
        ToolCall("shell", "run_shell", {"command": "rm -rf build"}),
        ToolContext("session", tmp_path, PermissionMode.YOLO),
    )
    assert result.is_error
    assert "危险" in result.content
