from abc import ABC, abstractmethod

from backend.types import ToolContext, ToolResult


class ToolResultStorage(ABC):
    """把大型工具结果转换为可回读的轻量引用。"""

    @abstractmethod
    async def normalize(self, result: ToolResult, context: ToolContext) -> ToolResult:
        raise NotImplementedError
