from pathlib import Path

import pytest

from camel_harness.agent.tools import (
    PermissionMode,
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)
from camel_harness.agent.tools.file_result_storage import FileToolResultStorage
from camel_harness.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool


async def _execute(tmp_path: Path, name: str, arguments: dict[str, object]) -> ToolResult:
    registry = ToolRegistry()
    for tool in (ReadFileTool(), WriteFileTool(), EditFileTool()):
        registry.register(tool)
    return await ToolExecutor(registry).execute(
        ToolCall("file", name, arguments), ToolContext("file", tmp_path, PermissionMode.YOLO)
    )


async def test_read_pages_default_limit_and_continues_without_missing_lines(tmp_path: Path) -> None:
    path = tmp_path / "pages.txt"
    path.write_text("\n".join(f"内容{index}" for index in range(401)), encoding="utf-8")
    first = await _execute(tmp_path, "read_file", {"path": path.name})
    assert first.metadata["start_line"] == 1 and first.metadata["end_line"] == 400
    assert first.metadata["total_lines"] == 401 and first.metadata["next_start_line"] == 401
    assert "start_line=401" in first.content
    second = await _execute(tmp_path, "read_file", {"path": path.name, "start_line": 401})
    assert second.content == "401: 内容400\n[显示第 401–401 行，共 401 行]"
    assert second.metadata["next_start_line"] is None
    assert "file_revision" not in first.metadata


async def test_read_utf8_byte_limit_preserves_whole_lines_and_reports_next_page(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bytes.txt"
    path.write_text("\n".join(["中" * 1000] * 10), encoding="utf-8")
    first = await _execute(tmp_path, "read_file", {"path": path.name, "max_lines": 2000})
    assert first.metadata["end_line"] == 5
    assert first.metadata["next_start_line"] == 6
    assert first.metadata["output_bytes"] == len("\n".join(first.content.splitlines()[:5]).encode())
    assert first.metadata["output_bytes"] <= 16 * 1024
    second = await _execute(tmp_path, "read_file", {"path": path.name, "start_line": 6})
    assert second.metadata["start_line"] == 6 and second.metadata["end_line"] == 10
    assert second.metadata["next_start_line"] is None
    assert all(line.endswith("中" * 1000) for line in first.content.splitlines()[:5])
    assert len(first.content) < 20_000


async def test_read_empty_out_of_range_and_oversized_single_line(tmp_path: Path) -> None:
    path = tmp_path / "edge.txt"
    path.touch()
    empty = await _execute(tmp_path, "read_file", {"path": path.name})
    assert empty.content == "[空文件，共 0 行]" and not empty.is_error
    assert empty.metadata["total_lines"] == 0 and empty.metadata["next_start_line"] is None
    outside = await _execute(tmp_path, "read_file", {"path": path.name, "start_line": 2})
    assert outside.is_error and "越界" in outside.content
    path.write_text("中" * 6000, encoding="utf-8")
    oversized = await _execute(tmp_path, "read_file", {"path": path.name})
    assert "第 1 行超过 16 KiB" in oversized.content and "中" * 10 not in oversized.content
    assert oversized.metadata["start_line"] is None and oversized.metadata["end_line"] is None
    assert oversized.metadata["next_start_line"] == 1
    assert oversized.metadata["output_bytes"] == 0
    invalid_limit = await _execute(tmp_path, "read_file", {"path": path.name, "max_lines": 2001})
    assert invalid_limit.is_error and invalid_limit.metadata["execution_status"] == "not_executed"


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("bom", ["", "\ufeff"])
async def test_batch_edits_preserve_bom_line_endings_and_emit_diff(
    tmp_path: Path, ending: str, bom: str
) -> None:
    path = tmp_path / "edit.txt"
    path.write_bytes((bom + ending.join(["start", "first", "second", "end", ""])).encode())
    result = await _execute(
        tmp_path,
        "edit_file",
        {
            "path": path.name,
            "edits": [
                {"old_text": "first\n", "new_text": "一\n新增\n"},
                {"old_text": "second", "new_text": "二"},
            ],
        },
    )
    assert not result.is_error
    assert (
        path.read_bytes() == (bom + ending.join(["start", "一", "新增", "二", "end", ""])).encode()
    )
    assert "2 个片段" in result.content
    assert "--- edit.txt" in result.content and "+++ edit.txt" in result.content
    assert "-first" in result.content and "+新增" in result.content


async def test_mixed_endings_outside_replacement_are_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"before\r\ntarget\nafter\rfinish\n")
    result = await _execute(
        tmp_path,
        "edit_file",
        {
            "path": path.name,
            "edits": [
                {"old_text": "target", "new_text": "one\ntwo"},
            ],
        },
    )
    assert not result.is_error
    assert path.read_bytes() == b"before\r\none\r\ntwo\nafter\rfinish\n"


@pytest.mark.parametrize(
    ("content", "edits"),
    [
        (
            "first second",
            [
                {"old_text": "first", "new_text": "changed"},
                {"old_text": "missing", "new_text": "x"},
            ],
        ),
        ("aaa", [{"old_text": "aa", "new_text": "x"}]),
        ("abcdef", [{"old_text": "abc", "new_text": "x"}, {"old_text": "bcd", "new_text": "y"}]),
        ("abcdef", [{"old_text": "abcde", "new_text": "x"}, {"old_text": "bc", "new_text": "y"}]),
        (
            "first",
            [
                {"old_text": "first", "new_text": "second"},
                {"old_text": "second", "new_text": "third"},
            ],
        ),
        ("first", [{"old_text": "first", "new_text": "first"}]),
        ("first", [{"old_text": "", "new_text": "x"}]),
        ("first", []),
        ("‘value’", [{"old_text": "'value'", "new_text": "x"}]),
    ],
)
async def test_invalid_edits_never_partially_write(
    tmp_path: Path, content: str, edits: list[dict[str, str]]
) -> None:
    path = tmp_path / "invalid.txt"
    path.write_bytes(content.encode())
    result = await _execute(tmp_path, "edit_file", {"path": path.name, "edits": edits})
    assert result.is_error and result.metadata["execution_status"] == "not_executed"
    assert path.read_bytes() == content.encode()
    assert list(tmp_path.iterdir()) == [path]


async def test_adjacent_deletion_and_insertion_use_original_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "delete.txt"
    path.write_text("abcDEF", encoding="utf-8")
    result = await _execute(
        tmp_path,
        "edit_file",
        {
            "path": path.name,
            "edits": [
                {"old_text": "abc", "new_text": ""},
                {"old_text": "DEF", "new_text": "新的结尾\n"},
            ],
        },
    )
    assert not result.is_error and path.read_bytes() == "新的结尾\n".encode()


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("write_file", {"path": "legacy.txt", "content": "new", "expected_revision": "0"}),
        ("edit_file", {"path": "legacy.txt", "old_text": "old", "new_text": "new"}),
    ],
)
async def test_removed_parameters_are_rejected(
    tmp_path: Path, name: str, arguments: dict[str, object]
) -> None:
    result = await _execute(tmp_path, name, arguments)
    assert result.is_error and result.metadata["execution_status"] == "not_executed"
    assert not (tmp_path / "legacy.txt").exists()


async def test_diff_reports_final_newline_only_change(tmp_path: Path) -> None:
    path = tmp_path / "newline.txt"
    path.write_bytes(b"content")
    result = await _execute(
        tmp_path,
        "edit_file",
        {
            "path": path.name,
            "edits": [
                {"old_text": "content", "new_text": "content\n"},
            ],
        },
    )
    assert not result.is_error and path.read_bytes() == b"content\n"
    assert "-content\n\\ No newline at end of file\n+content\n" in result.content


async def test_large_edit_diff_is_archived_without_losing_execution_status(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    original = "旧行\n" * 4000
    updated = "新行\n" * 4000
    path.write_bytes(original.encode())
    result = await _execute(
        tmp_path,
        "edit_file",
        {
            "path": path.name,
            "edits": [
                {"old_text": original, "new_text": updated},
            ],
        },
    )
    (tmp_path / "sessions" / "large").mkdir(parents=True)
    storage = FileToolResultStorage(lambda session_id: tmp_path / "sessions" / session_id)
    archived = await storage.normalize(result, ToolContext("large", tmp_path))
    assert path.read_bytes() == updated.encode()
    assert archived.metadata["execution_status"] == "succeeded"
    reference = Path(archived.metadata["content_ref"]["path"])
    assert reference.read_text("utf-8") == result.content
    assert "-旧行" in result.content and "+新行" in result.content
