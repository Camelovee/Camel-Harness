from __future__ import annotations

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

    async def discover(self) -> tuple[Tool, ...]:
        return (
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            RunShellTool(),
        )
