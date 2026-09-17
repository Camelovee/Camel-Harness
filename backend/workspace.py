from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path


class NativeWorkspacePicker:
    """使用 macOS 系统原生目录选择器选择工作区，不依赖 Python Tk。"""

    async def select(self, initial: Path | None = None) -> Path | None:
        """在线程池中调用 osascript，避免阻塞 FastAPI 事件循环。"""

        return await asyncio.to_thread(self._select_sync, initial)

    @staticmethod
    def _select_sync(initial: Path | None) -> Path | None:
        osascript = shutil.which("osascript")
        if osascript is None:
            raise RuntimeError("当前系统没有 osascript，无法打开本机工作区选择器")
        initial_path = str(initial.expanduser().resolve()) if initial else str(Path.home())
        escaped_initial = initial_path.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'set startFolder to POSIX file "' + escaped_initial + '"\n'
            'set selectedFolder to choose folder with prompt "选择 Camel Harness 工作区" '
            "default location startFolder\n"
            "return POSIX path of selectedFolder"
        )
        try:
            result = subprocess.run(
                [osascript, "-e", script],
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("工作区选择器等待超时") from exc
        if result.returncode != 0:
            if "-128" in result.stderr or "User canceled" in result.stderr:
                return None
            raise RuntimeError(result.stderr.strip() or "无法打开本机工作区选择器")
        selected = result.stdout.strip()
        return Path(selected).expanduser().resolve() if selected else None
