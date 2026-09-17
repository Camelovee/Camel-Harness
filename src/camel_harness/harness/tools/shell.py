from __future__ import annotations

import asyncio
import re
import shlex

from camel_harness.types import Tool, ToolCall, ToolContext, ToolResult, ToolRisk

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

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        command = str(call.arguments["command"])
        if any(pattern.search(command) for pattern in _DANGEROUS_SHELL_PATTERNS):
            return ToolResult(call.call_id, call.name, "危险命令已被安全规则阻止", is_error=True)
        timeout = float(call.arguments.get("timeout_seconds", 30))
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=context.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(call.call_id, call.name, "Shell 命令执行超时", is_error=True)
        text = output.decode("utf-8", errors="replace")
        if len(text) > 50_000:
            text = f"{text[:50_000]}\n…输出已截断，共 {len(text)} 字符"
        return ToolResult(
            call.call_id,
            call.name,
            f"退出码：{process.returncode}\n{text}",
            is_error=process.returncode != 0,
            metadata={"argv_preview": shlex.split(command)[:8], "exit_code": process.returncode},
        )
