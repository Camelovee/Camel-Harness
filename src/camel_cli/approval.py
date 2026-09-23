from __future__ import annotations

import asyncio

from camel_harness.agent.tools import ApprovalHandler, ApprovalPendingListener, PermissionRequest

_INPUT_LOCK = asyncio.Lock()


async def console_input(prompt: str) -> str:
    """串行读取终端，避免 REPL steering 与审批同时占用 stdin。"""

    async with _INPUT_LOCK:
        return await asyncio.to_thread(input, prompt)


class ConsoleApproval(ApprovalHandler):
    """通过控制台向当前用户请求一次性工具授权。"""

    async def approve(
        self,
        request: PermissionRequest,
        reason: str,
        on_pending: ApprovalPendingListener | None = None,
    ) -> bool:
        summary = f"{request.tool_name} {request.arguments}"
        try:
            answer = await console_input(f"\n需要授权：{summary}\n原因：{reason}\n允许？[y/N] ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in {"y", "yes"}
