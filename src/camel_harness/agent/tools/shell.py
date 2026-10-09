from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
import tempfile
from dataclasses import replace

from .file_result_storage import FileToolResultStorage
from .types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk

_DANGEROUS_SHELL_PATTERNS = (
    re.compile(r"(^|[;&|]\s*)rm\s+(-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\b"),
    re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[^\s]*f)\b"),
    re.compile(r"\b(shutdown|reboot|mkfs|diskutil\s+erase)\b"),
    re.compile(r"(^|\s)(sudo|su)(\s|$)"),
)


class RunShellTool(Tool):
    """在工作区启动受超时与危险命令规则保护的 Shell。"""

    name = "run_shell"
    description = "在工作区执行 Shell 命令，返回退出码和合并输出。"
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
        with tempfile.TemporaryFile() as output:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=context.workspace,
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
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
                    "argv_preview": shlex.split(command)[:8],
                    "exit_code": process.returncode,
                },
            )


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    """独立进程组涵盖 Shell 后代，避免父任务取消后工具继续产生副作用。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                await asyncio.wait_for(process.wait(), 0.2)
            except TimeoutError:
                pass
        else:
            await process.wait()
