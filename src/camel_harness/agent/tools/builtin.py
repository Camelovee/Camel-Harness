from __future__ import annotations

from .file_result_storage import FileToolResultStorage
from .filesystem import EditFileTool, ReadFileTool, WriteFileTool
from .registry import ToolProvider
from .shell import RunShellTool
from .types import Tool

__all__ = [
    "BuiltinToolProvider",
    "EditFileTool",
    "ReadFileTool",
    "RunShellTool",
    "WriteFileTool",
]


class BuiltinToolProvider(ToolProvider):
    """发现 Harness 自带的工作区工具。"""

    def __init__(self, tool_result_storage: FileToolResultStorage | None = None) -> None:
        self._tool_result_storage = tool_result_storage

    async def discover(self) -> tuple[Tool, ...]:
        return (
            ReadFileTool(
                self._tool_result_storage.resolve_read_path if self._tool_result_storage else None
            ),
            WriteFileTool(),
            EditFileTool(),
            RunShellTool(self._tool_result_storage),
        )
