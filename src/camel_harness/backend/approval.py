from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from camel_harness.tools import (
    ApprovalCenter,
    ApprovalPendingListener,
    PendingApproval,
    PermissionRequest,
)


@dataclass(slots=True)
class _Pending:
    item: PendingApproval
    future: asyncio.Future[bool]


class WebApproval(ApprovalCenter):
    """把需要确认的工具调用挂起，交给浏览器审批后再恢复。"""

    def __init__(self, timeout_seconds: float = 300) -> None:
        self._timeout_seconds = timeout_seconds
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()

    async def approve(
        self,
        request: PermissionRequest,
        reason: str,
        on_pending: ApprovalPendingListener | None = None,
    ) -> bool:
        request_id = request.request_id or uuid4().hex
        item = PendingApproval(
            request_id=request_id,
            tool_name=request.tool_name,
            capabilities=request.capabilities,
            risk=request.risk,
            arguments=dict(request.arguments),
            reason=reason,
            session_id=request.session_id,
        )
        future = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending[request_id] = _Pending(item, future)
        try:
            if on_pending is not None:
                await on_pending(request_id, request, reason)
            return await asyncio.wait_for(future, timeout=self._timeout_seconds)
        except TimeoutError:
            return False
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)

    async def list_pending(self) -> tuple[PendingApproval, ...]:
        async with self._lock:
            return tuple(pending.item for pending in self._pending.values())

    async def resolve(self, request_id: str, approved: bool) -> bool:
        async with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.future.done():
                return False
            pending.future.set_result(approved)
            return True
