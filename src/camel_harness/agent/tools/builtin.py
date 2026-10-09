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

    def __init__(self, output_storage: FileToolResultStorage | None = None) -> None:
        self._output_storage = output_storage

    async def discover(self) -> tuple[Tool, ...]:
        return (
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            RunShellTool(self._output_storage),
        )
