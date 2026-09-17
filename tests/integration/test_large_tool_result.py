from camel_harness.harness.tools.file_result_storage import (
    FileToolResultStorage,
)
from camel_harness.types import ToolContext, ToolResult


async def test_large_result_is_offloaded_to_workspace(tmp_path) -> None:
    store = FileToolResultStorage(max_inline_chars=5)
    result = await store.normalize(
        ToolResult("call-1", "read_file", "123456789"),
        ToolContext("session-1", tmp_path),
    )
    assert result.metadata["original_chars"] == 9
    reference = str(result.metadata["content_ref"])
    assert "完整内容" in result.content
    assert (tmp_path / reference).read_text("utf-8") == "123456789"
