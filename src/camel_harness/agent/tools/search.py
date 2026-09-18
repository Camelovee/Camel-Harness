from __future__ import annotations

from .registry import ToolProvider, ToolRegistry
from .types import Tool, ToolCall, ToolContext, ToolResult


class ToolSearchTool(Tool):
    """检索并激活未默认暴露给模型的延迟工具。"""

    name = "tool_search"
    description = "按能力描述搜索并激活 deferred 工具。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        tools = self._registry.search_and_activate(
            str(call.arguments["query"]), int(call.arguments.get("limit", 8))
        )
        if not tools:
            return ToolResult(call.call_id, call.name, "没有匹配的延迟工具")
        content = "\n".join(f"{tool.qualified_name}: {tool.description}" for tool in tools)
        return ToolResult(call.call_id, call.name, f"已激活：\n{content}")


class ToolSearchProvider(ToolProvider):
    """提供延迟工具检索入口。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def discover(self) -> tuple[Tool, ...]:
        return (ToolSearchTool(self._registry),)
