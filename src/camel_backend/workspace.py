from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path


class NativeWorkspacePicker:
    """按平台打开本机目录选择器，不改变工作区校验边界。"""

    async def select(self, initial: Path | None = None) -> Path | None:
        """在线程池中等待系统对话框，避免阻塞 FastAPI 事件循环。"""

        return await asyncio.to_thread(self._select_sync, initial)

    @staticmethod
    def _select_sync(initial: Path | None) -> Path | None:
        if sys.platform == "win32":
            return NativeWorkspacePicker._select_windows(initial)
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

    @staticmethod
    def _select_windows(initial: Path | None) -> Path | None:
        # 参数不拼进 PowerShell 代码，带引号的路径也不会变成可执行脚本。
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            raise RuntimeError("当前系统没有 Windows PowerShell，无法打开本机工作区选择器")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$dialog.Description = 'Camel Harness workspace';"
            "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false);"
            "$dialog.SelectedPath = [Console]::In.ReadToEnd();"
            "try { if ($dialog.ShowDialog() -eq 'OK') {"
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false);"
            "[Console]::Write($dialog.SelectedPath) } } finally { $dialog.Dispose() }"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-STA", "-Command", script],
                input=str(initial.expanduser().resolve() if initial else Path.home()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("工作区选择器等待超时") from exc
        if result.returncode != 0:
            raise RuntimeError("无法打开 Windows 工作区选择器")
        selected = result.stdout.strip()
        return Path(selected).resolve() if selected else None
