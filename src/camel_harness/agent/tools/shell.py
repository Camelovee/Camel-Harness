from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
import subprocess
import tempfile
from dataclasses import replace
from typing import BinaryIO, cast

from ..platform_io import IS_WINDOWS
from .file_result_storage import FileToolResultStorage
from .types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk

_DANGEROUS_SHELL_PATTERNS = (
    re.compile(r"(^|[;&|]\s*)rm\s+(-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\b"),
    re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[^\s]*f)\b"),
    re.compile(r"\b(shutdown|reboot|mkfs|diskutil\s+erase)\b"),
    re.compile(r"(^|\s)(sudo|su)(\s|$)"),
    re.compile(r"\b(?:rd|rmdir|del|erase)\b[^\r\n]*?/(?:s|q)\b", re.IGNORECASE),
    re.compile(r"\b(?:format|runas)\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b[^\r\n]*-Recurse\b", re.IGNORECASE),
)


class RunShellTool(Tool):
    """在工作区启动受超时与危险命令规则保护的 Shell。"""

    name = "run_shell"
    description = "在工作区执行 Shell 命令，返回退出码和合并输出。" + (
        "当前系统为 Windows，使用 cmd.exe 语法，不是 PowerShell。" if IS_WINDOWS else ""
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1},
            "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 120},
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    capabilities = frozenset({"process.execute"})
    risk = ToolRisk.EXECUTE
    timeout_seconds = 125

    def __init__(self, output_storage: FileToolResultStorage | None = None) -> None:
        self._output_storage = output_storage

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        command = str(call.arguments["command"])
        if any(pattern.search(command) for pattern in _DANGEROUS_SHELL_PATTERNS):
            return ToolResult(call.call_id, call.name, "危险命令已被安全规则阻止", is_error=True)
        timeout = float(call.arguments.get("timeout_seconds", 30))
        with tempfile.TemporaryFile() as output_file:
            output = cast(BinaryIO, output_file)
            environment = os.environ.copy()
            if IS_WINDOWS:
                # cmd 和 Python 子进程输出统一 UTF-8，结果归档无需猜测本机代码页。
                command = f"chcp 65001 >nul & {command}"
                environment["PYTHONIOENCODING"] = "utf-8"
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=context.workspace,
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
                env=environment,
                start_new_session=not IS_WINDOWS,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if IS_WINDOWS
                else 0,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                await _stop_process_group(process)
                return ToolResult(call.call_id, call.name, "Shell 命令执行超时", is_error=True)
            except asyncio.CancelledError:
                await _stop_process_group(process)
                raise
            if self._output_storage is not None:
                result = await self._output_storage.from_file(
                    output, call.call_id, call.name, context
                )
            else:
                output.seek(0)
                text = output.read(50_000).decode("utf-8", errors="replace")
                truncated = output.tell() < os.fstat(output.fileno()).st_size
                if truncated:
                    text += "\n…输出预览已截断，当前工具未配置完整结果存储"
                result = ToolResult(
                    call.call_id, call.name, text, metadata={"output_truncated": truncated}
                )
            return replace(
                result,
                content=f"退出码：{process.returncode}\n{result.content}",
                is_error=process.returncode != 0,
                metadata={
                    **result.metadata,
                    "argv_preview": _command_preview(str(call.arguments["command"])),
                    "exit_code": process.returncode,
                },
            )


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    """独立进程组涵盖 Shell 后代，避免父任务取消后工具继续产生副作用。"""
    cleanup = asyncio.create_task(_terminate_process_tree(process))
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if IS_WINDOWS:
        killer = await asyncio.create_subprocess_exec(
            "taskkill.exe",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(killer.wait(), 5)
        except TimeoutError:
            killer.kill()
            await killer.wait()
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise RuntimeError("Windows Shell 进程树清理超时") from None
        if killer.returncode != 0 and process.returncode is None:
            process.kill()
            await process.wait()
            raise RuntimeError("Windows Shell 进程树清理失败")
        await process.wait()
        return
    kill_group = getattr(os, "killpg", None)
    kill_signal = getattr(signal, "SIGKILL", None)
    if kill_group is None or kill_signal is None:
        raise RuntimeError("当前平台不支持 Shell 进程组清理")
    for sig in (signal.SIGTERM, kill_signal):
        try:
            kill_group(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                await asyncio.wait_for(process.wait(), 0.2)
            except TimeoutError:
                pass
        else:
            await process.wait()


def _command_preview(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=not IS_WINDOWS)[:8]
    except ValueError:
        # 展示元数据不能把已经执行成功的命令变成可重试的解析失败。
        return [command[:200]]
