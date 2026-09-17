from __future__ import annotations

from backend.resources import MemoryEntry, MemoryStore
from backend.types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk


class _MemoryTool(Tool):
    """为 Memory 工具保存共享资源访问对象。"""

    def __init__(self, memories: MemoryStore) -> None:
        self.memories = memories


class MemoryListTool(_MemoryTool):
    """列出当前项目长期记忆摘要。"""

    name = "memory_list"
    description = "列出项目长期记忆。"
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        content = "\n".join(
            f"{item.name}\t{item.kind}\t{item.description}" for item in await self.memories.list()
        )
        return ToolResult(call.call_id, call.name, content or "暂无 Memory")


class MemoryLoadTool(_MemoryTool):
    """按名称加载长期记忆正文。"""

    name = "memory_load"
    description = "按名称加载长期记忆正文。"
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1}},
        "required": ["name"],
        "additionalProperties": False,
    }

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        entry = await self.memories.get(str(call.arguments["name"]))
        if entry is None:
            return ToolResult(call.call_id, call.name, "Memory 不存在", is_error=True)
        return ToolResult(call.call_id, call.name, entry.content)


class MemorySaveTool(_MemoryTool):
    """保存项目事实、偏好或决策。"""

    name = "memory_save"
    description = "保存一条项目级长期记忆。"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "content": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "default": "project"},
        },
        "required": ["name", "description", "content"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"memory.write", "filesystem.write"})
    risk = ToolRisk.WRITE

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        entry = MemoryEntry(
            str(call.arguments["name"]),
            str(call.arguments["description"]),
            str(call.arguments["content"]),
            str(call.arguments.get("kind", "project")),
        )
        await self.memories.save(entry)
        return ToolResult(call.call_id, call.name, f"已保存 Memory：{entry.name}")


class MemoryDeleteTool(_MemoryTool):
    """删除明确命名的项目长期记忆。"""

    name = "memory_delete"
    description = "删除一条项目级长期记忆。"
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1}},
        "required": ["name"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"memory.delete", "filesystem.write"})
    risk = ToolRisk.WRITE

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        name = str(call.arguments["name"])
        deleted = await self.memories.delete(name)
        return ToolResult(
            call.call_id,
            call.name,
            f"已删除 Memory：{name}" if deleted else "Memory 不存在",
            is_error=not deleted,
        )
