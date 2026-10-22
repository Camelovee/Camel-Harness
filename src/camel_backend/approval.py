from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

from camel_harness.agent.tools import (
    ApprovalHandler,
    ApprovalPendingListener,
    PendingApproval,
    PermissionRequest,
)


@dataclass(slots=True)
class _Pending:
    item: PendingApproval
    future: asyncio.Future[bool]


class WebApproval(ApprovalHandler):
    """把需要确认的工具调用挂起，交给浏览器审批后再恢复。"""

    def __init__(self, timeout_seconds: float = 300) -> None:
        self._timeout_seconds = timeout_seconds
        self._approvals_by_request_id: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()

    async def approve(
        self,
        permission_request: PermissionRequest,
        reason: str,
        on_pending: ApprovalPendingListener | None = None,
    ) -> bool:
        request_id = permission_request.request_id or uuid4().hex
        pending_approval = PendingApproval(
            request_id=request_id,
            tool_name=permission_request.tool_name,
            capabilities=permission_request.capabilities,
            risk=permission_request.risk,
            arguments=deepcopy(permission_request.arguments),
            execution_context=dict(permission_request.execution_context),
            reason=reason,
            session_id=permission_request.session_id,
        )
        decision_future = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._approvals_by_request_id[request_id] = _Pending(pending_approval, decision_future)
        try:
            if on_pending is not None:
                await on_pending(request_id, permission_request, reason)
            return await asyncio.wait_for(decision_future, timeout=self._timeout_seconds)
        except TimeoutError:
            return False
        finally:
            async with self._lock:
                self._approvals_by_request_id.pop(request_id, None)

    async def list_pending(self) -> tuple[PendingApproval, ...]:
        async with self._lock:
            return tuple(pending.item for pending in self._approvals_by_request_id.values())

    async def resolve(self, request_id: str, approved: bool) -> bool:
        async with self._lock:
            pending = self._approvals_by_request_id.get(request_id)
            if pending is None or pending.future.done():
                return False
            pending.future.set_result(approved)
            return True
