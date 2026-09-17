from abc import ABC, abstractmethod

from camel_harness.types import Tool


class ToolProvider(ABC):
    """动态工具发现接口，可由内置代码、插件或 MCP 实现。"""

    @abstractmethod
    async def discover(self) -> tuple[Tool, ...]:
        raise NotImplementedError
