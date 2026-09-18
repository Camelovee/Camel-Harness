from __future__ import annotations

from .filesystem import EditFileTool, GrepSearchTool, ListFilesTool, ReadFileTool, WriteFileTool
from .registry import ToolProvider
from .shell import RunShellTool
from .types import Tool

__all__ = [
    "BuiltinToolProvider",
    "EditFileTool",
    "GrepSearchTool",
    "ListFilesTool",
    "ReadFileTool",
    "RunShellTool",
    "WriteFileTool",
]


class BuiltinToolProvider(ToolProvider):
    """发现 Harness 自带的工作区工具。"""

    async def discover(self) -> tuple[Tool, ...]:
        return (
            ReadFileTool(),
            ListFilesTool(),
            GrepSearchTool(),
            WriteFileTool(),
            EditFileTool(),
            RunShellTool(),
        )
