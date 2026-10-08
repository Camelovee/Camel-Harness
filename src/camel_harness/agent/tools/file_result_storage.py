from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from .types import ToolContext, ToolResult

_SAFE_PART = re.compile(r"[^A-Za-z0-9_-]+")


class FileToolResultStorage:
    """把超大结果保存在对应会话目录，并返回预览与绝对路径引用。"""

    def __init__(
        self, session_directory: Callable[[str], Path], max_inline_chars: int = 20_000
    ) -> None:
        self._session_directory = session_directory
        self._max_inline_chars = max_inline_chars

    async def normalize(self, result: ToolResult, context: ToolContext) -> ToolResult:
        if len(result.content) <= self._max_inline_chars:
            return result
        call_id = _SAFE_PART.sub("-", result.call_id)[:80] or "result"
        directory = self._session_directory(context.session_id) / "tool-results"
        path = await asyncio.to_thread(self._write, directory, call_id, result.content)
        reference = str(path)
        preview = result.content[: self._max_inline_chars]
        metadata = {
            **result.metadata,
            "content_ref": {
                "path": reference,
                "mime_type": "text/plain; charset=utf-8",
                "size_bytes": len(result.content.encode("utf-8")),
                "original_chars": len(result.content),
                "preview_chars": len(preview),
            },
        }
        return ToolResult(
            result.call_id,
            result.name,
            f"{preview}\n\n[结果已截断，完整内容：{reference}]",
            result.is_error,
            metadata,
        )

    @staticmethod
    def _write(directory: Path, call_id: str, content: str) -> Path:
        if directory.is_symlink():
            raise ValueError("工具结果目录不能是符号链接")
        directory.mkdir(mode=0o700, exist_ok=True)
        # 随机文件名避免同一会话重复工具调用覆盖旧结果；mkstemp 使用私有权限。
        descriptor, name = tempfile.mkstemp(prefix=f"{call_id}-", suffix=".txt", dir=directory)
        path = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise
