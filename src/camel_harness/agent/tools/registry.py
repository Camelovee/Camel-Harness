from __future__ import annotations

from abc import ABC, abstractmethod

from camel_harness.llm import ToolDefinition

from .types import Tool


class ToolProvider(ABC):
    """动态工具发现接口，可由内置代码、插件或 MCP 实现。"""

    @abstractmethod
    async def discover(self) -> tuple[Tool, ...]:
        raise NotImplementedError


class ToolRegistry:
    """进程内工具目录，禁止同名工具被静默覆盖。"""

    def __init__(self) -> None:
        self._tools_by_name: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.qualified_name
        if name in self._tools_by_name:
            raise ValueError(f"工具已注册：{name}")
        self._tools_by_name[name] = tool

    async def register_provider(self, provider: ToolProvider) -> None:
        for tool in await provider.discover():
            self.register(tool)

    def resolve(self, name: str) -> Tool:
        try:
            return self._tools_by_name[name]
        except KeyError as exc:
            raise KeyError(f"未知工具：{name}") from exc

    def subset(self, names: frozenset[str]) -> ToolRegistry:
        """为子 Agent 创建只包含显式白名单的隔离工具集。"""

        tool_registry = ToolRegistry()
        for name in names:
            # 白名单静默取交集：未注册的名字不报错，便于可选工具缺席。
            if name in self._tools_by_name:
                tool_registry.register(self._tools_by_name[name])
        return tool_registry

    def llm_definitions(self) -> tuple[ToolDefinition, ...]:
        """返回当前运行时显式注册的全部工具定义。"""

        # 保持注册顺序：工具定义顺序稳定有利于提示前缀缓存。
        return tuple(tool.to_definition() for tool in self._tools_by_name.values())
