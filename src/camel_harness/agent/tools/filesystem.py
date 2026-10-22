from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from .text_edits import apply_text_edits
from .types import Tool, ToolCall, ToolContext, ToolNotExecutedError, ToolResult, ToolRisk

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
        raise ToolNotExecutedError("路径超出工作区") from exc
    return candidate


def _check_memory_write(workspace: Path, path: Path) -> None:
    """通用文件工具不能绕过记忆审批，包含符号链接的规范路径别名。"""
    roots = (workspace.resolve() / ".camel" / "memory", Path.home() / ".camel" / "memory")
    if any(path.is_relative_to(root.resolve()) for root in roots):
        raise ToolNotExecutedError("记忆目录只能通过需审批的 save_memory/delete_memory 修改")


class ReadFileTool(Tool):
    """读取工作区内 UTF-8 文本文件。"""

    name = "read_file"
    description = (
        "读取工作区文件或当前会话已登记的工具结果引用，可指定起始行和最大行数。"
        "默认读取 400 行，最多 2000 行；带行号正文最多 16 KiB，只展示完整行。"
        "返回行范围、总行数和续读 start_line；未完整展示时按提示继续，不把片段当作全文。"
    )
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

    def __init__(
        self, result_path_resolver: Callable[[str, str], Awaitable[Path]] | None = None
    ) -> None:
        self._result_path_resolver = result_path_resolver

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        raw_path = str(tool_call.arguments["path"])
        try:
            path = resolve_in_workspace(tool_context.workspace, raw_path)
        except ValueError:
            if self._result_path_resolver is None:
                raise
            path = await self._result_path_resolver(tool_context.session_id, raw_path)
        start = int(tool_call.arguments.get("start_line", 1))
        limit = int(tool_call.arguments.get("max_lines", 400))
        data = await asyncio.to_thread(path.read_bytes)
        return _read_page(tool_call, data.decode("utf-8"), start, limit)


def _read_page(tool_call: ToolCall, text: str, start: int, limit: int) -> ToolResult:
    """按行与 UTF-8 字节双阈值分页，元信息不占正文额度。"""

    lines = text.splitlines()
    if start < 1 or not 1 <= limit <= 2000 or (start > len(lines) and (lines or start != 1)):
        raise ToolNotExecutedError("读取行范围越界或 max_lines 不在 1–2000 内")
    selected: list[str] = []
    output_bytes = 0
    for line_number in range(start, min(start + limit, len(lines) + 1)):
        numbered = f"{line_number}: {lines[line_number - 1]}"
        size = len(numbered.encode("utf-8")) + int(bool(selected))
        if output_bytes + size > 16 * 1024:
            break
        selected.append(numbered)
        output_bytes += size
    end = start + len(selected) - 1 if selected else None
    next_start = start + len(selected) if start + len(selected) <= len(lines) else None
    metadata = {
        "start_line": start if selected else None,
        "end_line": end,
        "total_lines": len(lines),
        "next_start_line": next_start,
        "output_bytes": output_bytes,
    }
    if not lines:
        notice = "[空文件，共 0 行]"
    elif not selected:
        notice = f"[第 {start} 行超过 16 KiB 正文上限，无法完整展示。请使用更合适的方式检查该行。]"
    else:
        notice = f"[显示第 {start}–{end} 行，共 {len(lines)} 行]"
    if next_start is not None and selected:
        notice += f"\n[尚未读完，请使用 start_line={next_start} 继续读取。]"
    return ToolResult(
        tool_call.call_id, tool_call.name, "\n".join([*selected, notice]), metadata=metadata
    )


class WriteFileTool(Tool):
    """原子覆盖工作区文件，并按需创建父目录。"""

    name = "write_file"
    description = (
        "创建或完整覆盖工作区内 UTF-8 文件，自动创建父目录。"
        "仅用于新文件或完整重写；局部修改优先使用 edit_file。"
        "覆盖前先了解现有内容；覆盖不进行版本校验，不能防止基于旧内容覆盖其他修改。"
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

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        path = resolve_in_workspace(tool_context.workspace, str(tool_call.arguments["path"]))
        _check_memory_write(tool_context.workspace, path)
        content = str(tool_call.arguments["content"])

        size = await _mutate_file(path, lambda: _atomic_write(path, content))
        return ToolResult(
            tool_call.call_id,
            tool_call.name,
            f"已写入 {path.relative_to(tool_context.workspace)}（{size} 字节）",
        )


class EditFileTool(Tool):
    """通过唯一文本匹配执行可验证的精确编辑。"""

    name = "edit_file"
    description = (
        "一次精确替换同一文件的多个独立片段。每个 old_text 都匹配原始文件，必须唯一且互不重叠。"
        "片段应尽量小而唯一，不复制行号；相邻或重叠修改合并为一项，不拼接大段无关内容。"
        "不做模糊匹配，保留 BOM 和未修改区域原文，返回修改数量及 diff。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "edits": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "old_text": {"type": "string", "minLength": 1},
                        "new_text": {"type": "string"},
                    },
                    "required": ["old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["path", "edits"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"filesystem.write"})
    risk = ToolRisk.WRITE

    async def execute(self, tool_call: ToolCall, tool_context: ToolContext) -> ToolResult:
        path = resolve_in_workspace(tool_context.workspace, str(tool_call.arguments["path"]))
        _check_memory_write(tool_context.workspace, path)
        edits = tool_call.arguments["edits"]
        diff = ""

        def edit() -> int:
            nonlocal diff
            content = path.read_bytes().decode("utf-8")
            updated, diff = apply_text_edits(
                content, edits, str(path.relative_to(tool_context.workspace))
            )
            return _atomic_write(path, updated)

        await _mutate_file(path, edit)
        return ToolResult(
            tool_call.call_id,
            tool_call.name,
            f"已编辑 {path.relative_to(tool_context.workspace)}（{len(edits)} 个片段）\n{diff}",
        )
