from __future__ import annotations

import asyncio
import re
from pathlib import Path

from camel_harness.types import ToolContext, ToolResult

_SAFE_PART = re.compile(r"[^A-Za-z0-9_-]+")


class FileToolResultStorage:
    """把超大结果保存在工作区，并向模型返回预览与相对引用。"""

    def __init__(self, max_inline_chars: int = 20_000) -> None:
        self._max_inline_chars = max_inline_chars

    async def normalize(self, result: ToolResult, context: ToolContext) -> ToolResult:
        if len(result.content) <= self._max_inline_chars:
            return result
        call_id = _SAFE_PART.sub("-", result.call_id)[:80] or "result"
        path = context.workspace / ".camel" / "tool-results" / context.session_id / f"{call_id}.txt"
        await asyncio.to_thread(self._write, path, result.content)
        reference = str(path.relative_to(context.workspace))
        preview = result.content[: self._max_inline_chars]
        metadata = {
            **result.metadata,
            "content_ref": reference,
            "original_chars": len(result.content),
        }
        return ToolResult(
            result.call_id,
            result.name,
            f"{preview}\n\n[结果已截断，完整内容：{reference}]",
            result.is_error,
            metadata,
        )

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, "utf-8")
        temporary.replace(path)
