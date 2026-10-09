from __future__ import annotations

import asyncio
import codecs
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

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
        if "content_ref" in result.metadata:
            return result
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

    async def from_file(
        self, source: BinaryIO, call_id: str, name: str, context: ToolContext
    ) -> ToolResult:
        """分块归档 Shell 输出，内存只保留预览，不丢失完整文件内容。"""
        task = asyncio.create_task(
            asyncio.to_thread(self._from_file, source, call_id, name, context)
        )
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        # 源文件由工具拥有，归档线程结束前不能关闭它。
        try:
            result = task.result()
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        if cancelled:
            reference = result.metadata.get("content_ref")
            if reference:
                Path(reference["path"]).unlink(missing_ok=True)
            raise asyncio.CancelledError
        return result

    def _from_file(
        self, source: BinaryIO, call_id: str, name: str, context: ToolContext
    ) -> ToolResult:
        source.seek(0)
        size = os.fstat(source.fileno()).st_size
        if size <= self._max_inline_chars:
            return ToolResult(call_id, name, source.read().decode("utf-8", errors="replace"))
        directory = self._session_directory(context.session_id) / "tool-results"
        safe_id = _SAFE_PART.sub("-", call_id)[:80] or "result"
        path = self._write(directory, safe_id, "")
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        preview = ""
        chars = 0
        try:
            with path.open("wb") as destination:
                while chunk := source.read(65_536):
                    destination.write(chunk)
                    text = decoder.decode(chunk)
                    chars += len(text)
                    preview += text[: max(0, self._max_inline_chars - len(preview))]
                tail = decoder.decode(b"", final=True)
                chars += len(tail)
                preview += tail[: max(0, self._max_inline_chars - len(preview))]
            return ToolResult(
                call_id,
                name,
                f"{preview}\n\n[完整输出：{path}]",
                metadata={
                    "content_ref": {
                        "path": str(path),
                        "mime_type": "text/plain; charset=utf-8",
                        "size_bytes": size,
                        "original_chars": chars,
                        "preview_chars": len(preview),
                    }
                },
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise

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
