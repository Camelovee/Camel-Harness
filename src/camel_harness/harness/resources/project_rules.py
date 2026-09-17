from __future__ import annotations

import asyncio
from pathlib import Path


class ProjectRules:
    """集中读取工作区项目规则，避免 Agent Loop 直接处理文件。"""

    async def read(self, workspace: Path) -> str:
        """读取工作区根目录的 AGENTS.md；文件不存在时返回空字符串。"""

        return await asyncio.to_thread(self._read, workspace)

    @staticmethod
    def _read(workspace: Path) -> str:
        path = workspace / "AGENTS.md"
        return path.read_text("utf-8") if path.is_file() else ""
