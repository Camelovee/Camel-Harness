from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from .types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk

_mutations: dict[Path, tuple[asyncio.Lock, int]] = {}


async def _mutate_file(path: Path, action: Callable[[], int]) -> int:
    """同一规范路径串行修改；取消不能提前释放仍在写入的线程所占的锁。"""

    lock, users = _mutations.get(path, (asyncio.Lock(), 0))
    _mutations[path] = (lock, users + 1)
    try:
        async with lock:
            task = asyncio.create_task(asyncio.to_thread(action))
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                # 获取异常避免无人读取的任务警告，取消仍是调用者的最终结果。
                if not task.cancelled():
                    task.exception()
                raise asyncio.CancelledError
            return task.result()
    finally:
        _, users = _mutations[path]
        if users == 1:
            del _mutations[path]
        else:
            _mutations[path] = (lock, users - 1)


def _atomic_write(path: Path, content: str) -> int:
    """完整写入同目录临时文件再替换；失败不破坏原文件或遗留临时文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            data = content.encode("utf-8")
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        temporary.replace(path)
        return len(data)
    finally:
        temporary.unlink(missing_ok=True)


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
    description = (
        "创建或完整覆盖工作区内 UTF-8 文件，自动创建父目录。"
        "仅用于新文件或完整重写；局部修改优先使用 edit_file。"
        "content 必须完整且适合单次输出额度，大文件请分阶段构建，禁止省略内容。"
    )
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

        size = await _mutate_file(path, lambda: _atomic_write(path, content))
        return ToolResult(
            call.call_id, call.name, f"已写入 {path.relative_to(context.workspace)}（{size} 字节）"
        )


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

        def edit() -> int:
            content = path.read_text("utf-8")
            count = content.count(old_text)
            if count != 1:
                raise ValueError(f"old_text 必须唯一匹配，当前匹配 {count} 次")
            return _atomic_write(path, content.replace(old_text, new_text, 1))

        await _mutate_file(path, edit)
        return ToolResult(call.call_id, call.name, f"已编辑 {path.relative_to(context.workspace)}")
