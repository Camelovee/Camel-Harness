from __future__ import annotations

import asyncio
from pathlib import Path

from .types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk


def resolve_in_workspace(workspace: Path, raw_path: str) -> Path:
    """解析并限制路径，防止绝对路径和软链接逃逸工作区。"""

    candidate = (workspace / raw_path).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError("路径超出工作区") from exc
    return candidate


class ReadFileTool(Tool):
    """读取工作区内 UTF-8 文本文件。"""

    name = "read_file"
    description = "读取工作区内的 UTF-8 文本文件，可指定起始行和最大行数。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "start_line": {"type": "integer", "minimum": 1},
            "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"filesystem.read"})

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        path = resolve_in_workspace(context.workspace, str(call.arguments["path"]))
        start = int(call.arguments.get("start_line", 1))
        limit = int(call.arguments.get("max_lines", 400))
        text = await asyncio.to_thread(path.read_text, "utf-8")
        lines = text.splitlines()
        selected = lines[start - 1 : start - 1 + limit]
        numbered = "\n".join(f"{index}: {line}" for index, line in enumerate(selected, start=start))
        return ToolResult(call.call_id, call.name, numbered)


class WriteFileTool(Tool):
    """原子覆盖工作区文件，并按需创建父目录。"""

    name = "write_file"
    description = "写入工作区内 UTF-8 文件；文件存在时会覆盖原内容。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"filesystem.write"})
    risk = ToolRisk.WRITE

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        path = resolve_in_workspace(context.workspace, str(call.arguments["path"]))
        content = str(call.arguments["content"])

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{call.call_id}.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)

        await asyncio.to_thread(write)
        return ToolResult(call.call_id, call.name, f"已写入 {path.relative_to(context.workspace)}")


class EditFileTool(Tool):
    """通过唯一文本匹配执行可验证的精确编辑。"""

    name = "edit_file"
    description = "将文件中唯一出现的 old_text 替换为 new_text。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "old_text": {"type": "string", "minLength": 1},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"filesystem.write"})
    risk = ToolRisk.WRITE

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        path = resolve_in_workspace(context.workspace, str(call.arguments["path"]))
        old_text = str(call.arguments["old_text"])
        new_text = str(call.arguments["new_text"])

        def edit() -> None:
            content = path.read_text("utf-8")
            count = content.count(old_text)
            if count != 1:
                raise ValueError(f"old_text 必须唯一匹配，当前匹配 {count} 次")
            temporary = path.with_name(f".{path.name}.{call.call_id}.tmp")
            temporary.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            temporary.replace(path)

        await asyncio.to_thread(edit)
        return ToolResult(call.call_id, call.name, f"已编辑 {path.relative_to(context.workspace)}")
