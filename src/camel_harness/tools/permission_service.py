from __future__ import annotations

from collections.abc import Callable

from .approval import ApprovalHandler, ApprovalPendingListener
from .default_permission import default_permission_decision
from .permission import (
    PermissionAction,
    PermissionDecision,
    PermissionRequest,
)

PermissionDecider = Callable[[PermissionRequest], PermissionDecision]


class PermissionService:
    """统一完成工具授权决策与审批。"""

    def __init__(
        self,
        approval: ApprovalHandler | None = None,
        decide: PermissionDecider = default_permission_decision,
    ) -> None:
        self._approval = approval
        self._decide = decide

    async def authorize(
        self,
        request: PermissionRequest,
        on_pending: ApprovalPendingListener | None = None,
    ) -> PermissionDecision:
        """返回包含审批结果的最终权限决策。"""
        decision = self._decide(request)
        if decision.action is not PermissionAction.ASK:
            return decision
        if self._approval is None:
            return PermissionDecision(PermissionAction.DENY, "权限请求需要审批但没有可用审批服务")
        approved = await self._approval.approve(request, decision.reason, on_pending)
        if approved:
            return PermissionDecision(PermissionAction.ALLOW, decision.reason)
        return PermissionDecision(PermissionAction.DENY, "用户未批准工具执行")
