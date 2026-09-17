from __future__ import annotations

from camel_harness.types import Tool, ToolDefinition

from .provider import ToolProvider


class ToolRegistry:
    """进程内工具目录，禁止同名工具被静默覆盖。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._activated_deferred: set[str] = set()

    def register(self, tool: Tool) -> None:
        name = tool.qualified_name
        if name in self._tools:
            raise ValueError(f"工具已注册：{name}")
        self._tools[name] = tool

    async def register_provider(self, provider: ToolProvider) -> None:
        for tool in await provider.discover():
            self.register(tool)

    def resolve(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"未知工具：{name}") from exc

    def subset(self, names: frozenset[str]) -> ToolRegistry:
        """创建只包含显式白名单的隔离目录。"""

        selected = ToolRegistry()
        for name in names:
            if name in self._tools:
                selected.register(self._tools[name])
        return selected

    def search_and_activate(self, query: str, limit: int = 8) -> tuple[Tool, ...]:
        """按名称、描述和标签检索延迟工具，并在本次进程中激活。"""

        terms = {term for term in query.lower().replace("_", " ").split() if term}
        scored: list[tuple[int, str, Tool]] = []
        for name, tool in self._tools.items():
            if not tool.deferred:
                continue
            text = " ".join((name, tool.description, *sorted(tool.tags))).lower()
            score = sum(term in text for term in terms)
            if score:
                scored.append((score, name, tool))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = tuple(item[2] for item in scored[:limit])
        self._activated_deferred.update(tool.qualified_name for tool in selected)
        return selected

    def llm_definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            tool.to_definition()
            for tool in self._tools.values()
            if not tool.deferred or tool.qualified_name in self._activated_deferred
        )
